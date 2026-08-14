param(
    [Parameter(Mandatory = $true)]
    [string]$SourceCsvPath,
    [string]$OutputPath = (
        "$PSScriptRoot\data\transactions_model80_10000.csv"
    ),
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$normalTarget = 9000
$fraudTarget = 1000
$totalTarget = $normalTarget + $fraudTarget
$expectedColumnCount = 64

if (-not (Test-Path -LiteralPath $SourceCsvPath -PathType Leaf)) {
    throw "원본 train1.csv를 찾을 수 없습니다: $SourceCsvPath"
}
if ((Test-Path -LiteralPath $OutputPath) -and -not $Force) {
    throw "출력 파일이 이미 있습니다. 덮어쓰려면 -Force를 사용하세요: $OutputPath"
}

$normalRows = [System.Collections.Generic.List[object]]::new()
$fraudRows = [System.Collections.Generic.List[object]]::new()

# 원본 파일의 행 순서를 바꾸지 않고 각 라벨에서 처음 만나는 목표 건수만 담는다.
# 정상 목록과 사기 목록도 각각 원본 train1.csv 안의 상대 순서를 그대로 유지한다.
Import-Csv -LiteralPath $SourceCsvPath | ForEach-Object {
    $label = [int]$_.is_fraud
    if ($label -eq 0 -and $normalRows.Count -lt $normalTarget) {
        $normalRows.Add($_)
    } elseif ($label -eq 1 -and $fraudRows.Count -lt $fraudTarget) {
        $fraudRows.Add($_)
    }
}

if ($normalRows.Count -ne $normalTarget -or $fraudRows.Count -ne $fraudTarget) {
    throw (
        "원본 CSV에서 목표 라벨 수를 선택하지 못했습니다: " +
        "normal=$($normalRows.Count)/$normalTarget, " +
        "fraud=$($fraudRows.Count)/$fraudTarget"
    )
}

$columns = @($normalRows[0].PSObject.Properties.Name)
if ($columns.Count -ne $expectedColumnCount) {
    throw (
        "원본 CSV 컬럼 수가 raw64 계약과 다릅니다: " +
        "$($columns.Count)/$expectedColumnCount"
    )
}
$requiredColumns = @(
    "transaction_id",
    "customer_id",
    "customer_name",
    "customer_identification_number",
    "account_account_number",
    "recipient_account_number",
    "ip_address",
    "mac_address",
    "is_fraud"
)
$missingColumns = @(
    $requiredColumns | Where-Object { $_ -notin $columns }
)
if ($missingColumns.Count -gt 0) {
    throw "원본 CSV 필수 컬럼이 없습니다: $($missingColumns -join ',')"
}

function Get-OrAddIndex {
    param(
        [hashtable]$Map,
        [string]$OriginalValue
    )

    if ([string]::IsNullOrWhiteSpace($OriginalValue)) {
        throw "익명화할 식별값이 비어 있습니다."
    }
    if (-not $Map.ContainsKey($OriginalValue)) {
        $Map[$OriginalValue] = $Map.Count + 1
    }
    return [int]$Map[$OriginalValue]
}

function New-AnonymousIpv4 {
    param([int]$Index)

    # 벤치마크용 비공인 198.18.0.0/15 대역을 사용한다.
    $zeroBased = $Index - 1
    $addressesPerSecondOctet = 256 * 254
    $secondOctet = 18 + [math]::Floor(
        $zeroBased / $addressesPerSecondOctet
    )
    if ($secondOctet -gt 19) {
        throw "익명 IP를 만들 수 있는 로컬 범위를 초과했습니다."
    }
    $remainder = $zeroBased % $addressesPerSecondOctet
    $thirdOctet = [math]::Floor($remainder / 254)
    $fourthOctet = ($remainder % 254) + 1
    return "198.$secondOctet.$thirdOctet.$fourthOctet"
}

function New-AnonymousMacAddress {
    param([int]$Index)

    # 첫 바이트 02는 전 세계 고유 장비 주소가 아닌 로컬 관리 주소를 뜻한다.
    return "02:00:{0:x2}:{1:x2}:{2:x2}:{3:x2}" -f `
        (($Index -shr 24) -band 0xff), `
        (($Index -shr 16) -band 0xff), `
        (($Index -shr 8) -band 0xff), `
        ($Index -band 0xff)
}

$customerIndexByOriginal = @{}
$accountIndexByOriginal = @{}
$ipIndexByOriginal = @{}
$macIndexByOriginal = @{}
$selectedRows = [System.Collections.Generic.List[object]]::new()

# 파일에서는 정상 9,000건을 먼저, 사기 1,000건을 다음에 둔다. 각 블록 내부의
# 순서는 위에서 선택한 원본 순서 그대로다.
foreach ($row in @($normalRows) + @($fraudRows)) {
    $selectedRows.Add($row)
}

$rowNumber = 0
foreach ($row in $selectedRows) {
    $rowNumber += 1

    # 같은 원본 고객은 어느 거래에 등장해도 같은 LOCAL_CUST 번호를 받는다.
    $customerIndex = Get-OrAddIndex `
        -Map $customerIndexByOriginal `
        -OriginalValue ([string]$row.customer_id)
    $row.customer_id = "LOCAL_CUST_{0:D6}" -f $customerIndex
    $row.customer_name = "테스트고객{0:D6}" -f $customerIndex
    $row.customer_identification_number = (
        "LOCAL-ID-{0:D6}" -f $customerIndex
    )

    # 출금·수취 계좌가 역할을 바꿔 다시 등장해도 하나의 공용 Map을 사용하므로
    # 항상 동일한 익명 계좌번호로 변환된다.
    $sourceAccountIndex = Get-OrAddIndex `
        -Map $accountIndexByOriginal `
        -OriginalValue ([string]$row.account_account_number)
    $recipientAccountIndex = Get-OrAddIndex `
        -Map $accountIndexByOriginal `
        -OriginalValue ([string]$row.recipient_account_number)
    $row.account_account_number = (
        "LOCAL-ACCOUNT-{0:D6}" -f $sourceAccountIndex
    )
    $row.recipient_account_number = (
        "LOCAL-ACCOUNT-{0:D6}" -f $recipientAccountIndex
    )

    $ipIndex = Get-OrAddIndex `
        -Map $ipIndexByOriginal `
        -OriginalValue ([string]$row.ip_address)
    $macIndex = Get-OrAddIndex `
        -Map $macIndexByOriginal `
        -OriginalValue ([string]$row.mac_address)
    $row.ip_address = New-AnonymousIpv4 -Index $ipIndex
    $row.mac_address = New-AnonymousMacAddress -Index $macIndex

    # CSV transaction_id는 원본 행을 구분하는 샘플 ID일 뿐이다. 실제 Backend
    # transaction_id는 POST /transactions 응답에서 DB가 새 정수로 발급한다.
    $row.transaction_id = "LOCAL_RAW60_TX_{0:D6}" -f $rowNumber
}

$outputDirectory = Split-Path -Parent $OutputPath
if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}
$selectedRows | Export-Csv `
    -LiteralPath $OutputPath `
    -NoTypeInformation `
    -Encoding UTF8

# 생성 직후 행·헤더·라벨·ID 고유성을 다시 읽어 잘못된 샘플이 남지 않게 한다.
$writtenRows = @(Import-Csv -LiteralPath $OutputPath)
$writtenColumns = @($writtenRows[0].PSObject.Properties.Name)
$writtenNormal = @(
    $writtenRows | Where-Object { [int]$_.is_fraud -eq 0 }
).Count
$writtenFraud = @(
    $writtenRows | Where-Object { [int]$_.is_fraud -eq 1 }
).Count
$uniqueTransactionIds = @(
    $writtenRows.transaction_id | Sort-Object -Unique
).Count
if (
    $writtenRows.Count -ne $totalTarget -or
    $writtenColumns.Count -ne $expectedColumnCount -or
    $writtenNormal -ne $normalTarget -or
    $writtenFraud -ne $fraudTarget -or
    $uniqueTransactionIds -ne $totalTarget
) {
    throw "생성된 10,000건 CSV의 사후 검증에 실패했습니다: $OutputPath"
}

$hash = Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256
[pscustomobject]@{
    OutputPath = $OutputPath
    Rows = $writtenRows.Count
    Columns = $writtenColumns.Count
    NormalRows = $writtenNormal
    FraudRows = $writtenFraud
    Customers = $customerIndexByOriginal.Count
    Accounts = $accountIndexByOriginal.Count
    SHA256 = $hash.Hash
} | Format-List

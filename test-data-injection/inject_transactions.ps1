param(
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [string]$MlServingUrl = "http://127.0.0.1:8001",
    [string]$AdminToken = "local-dev-mlops-token",
    [string]$CsvPath = "$PSScriptRoot\data\transactions_model80_10000.csv",
    [string]$ExpectedModelName = "fdshield-fraud-detector-v2",
    [string]$ExpectedModelVersion = "1",
    [string]$SmokePayloadPath = (
        "$PSScriptRoot\..\..\ml\examples\local-model-predict-request.json"
    )
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $CsvPath -PathType Leaf)) {
    throw (
        "10,000건 raw64 샘플 CSV를 찾을 수 없습니다: $CsvPath. " +
        "generate_transactions_sample.ps1을 먼저 실행하세요."
    )
}
if (-not (Test-Path -LiteralPath $SmokePayloadPath -PathType Leaf)) {
    throw (
        "ML Serving preflight payload를 찾을 수 없습니다: " +
        "$SmokePayloadPath"
    )
}

$totalRows = 10000
$normalTarget = 9000
$fraudTarget = 1000
$expectedRaw64ColumnCount = 64
$expectedOfficialShapCount = 56
$expectedRuleTypes = @(
    "ACCOUNT_TAKEOVER",
    "FRAUD_USED_ACCOUNT",
    "MESSENGER_PHISHING",
    "VOICE_PHISHING"
)

$backendHealth = Invoke-RestMethod -Uri "$BackendUrl/health" -TimeoutSec 10
$mlHealth = Invoke-RestMethod -Uri "$MlServingUrl/health" -TimeoutSec 10
if ($backendHealth.status -ne "ok" -or $mlHealth.status -ne "ok") {
    throw "Backend 또는 ML Serving health 확인에 실패했습니다."
}

try {
    $smokePayloadJson = Get-Content `
        -LiteralPath $SmokePayloadPath `
        -Raw `
        -Encoding UTF8
    $smokePayload = $smokePayloadJson | ConvertFrom-Json
} catch {
    throw (
        "ML Serving preflight payload JSON을 읽을 수 없습니다: " +
        "$SmokePayloadPath - $($_.Exception.Message)"
    )
}

$smokePropertyNames = @($smokePayload.PSObject.Properties.Name)
if (
    $smokePayload.transaction_id -is [string] -or
    [long]$smokePayload.transaction_id -le 0 -or
    $smokePropertyNames.Count -ne 60 -or
    $smokePropertyNames -contains "features"
) {
    throw (
        "ML Serving preflight payload는 양의 정수 transaction_id를 포함한 " +
        "flat raw60이어야 합니다: $SmokePayloadPath"
    )
}

try {
    $smokeResponse = Invoke-RestMethod `
        -Method Post `
        -Uri "$MlServingUrl/ml/predict" `
        -ContentType "application/json" `
        -Body $smokePayloadJson `
        -TimeoutSec 60
} catch {
    $detail = $_.ErrorDetails.Message
    if ([string]::IsNullOrWhiteSpace([string]$detail)) {
        $detail = $_.Exception.Message
    }
    throw "ML Serving preflight /ml/predict 실패: $detail"
}

if (
    $smokeResponse.transaction_id -is [string] -or
    [long]$smokeResponse.transaction_id -ne [long]$smokePayload.transaction_id
) {
    throw (
        "ML Serving preflight transaction_id가 예상과 다릅니다: " +
        "$($smokeResponse.transaction_id), " +
        "expected=$($smokePayload.transaction_id)"
    )
}
if (
    $smokeResponse.model_name -ne $ExpectedModelName -or
    [string]$smokeResponse.model_version -ne $ExpectedModelVersion
) {
    throw (
        "ML Serving preflight 모델이 예상과 다릅니다: " +
        "$($smokeResponse.model_name):$($smokeResponse.model_version), " +
        "expected=$ExpectedModelName`:$ExpectedModelVersion"
    )
}
if (
    [int]$smokeResponse.predict_result -notin @(0, 1) -or
    [double]$smokeResponse.predict_proba -lt 0.0 -or
    [double]$smokeResponse.predict_proba -gt 1.0
) {
    throw "ML Serving preflight 판정 또는 확률 응답이 올바르지 않습니다."
}
$smokeShapCount = if ($null -eq $smokeResponse.shap_values) {
    0
} else {
    @($smokeResponse.shap_values.PSObject.Properties).Count
}
if ($smokeShapCount -ne $expectedOfficialShapCount) {
    throw (
        "ML Serving preflight SHAP 그룹 수가 예상과 다릅니다: " +
        "$smokeShapCount, expected=$expectedOfficialShapCount"
    )
}

$rows = @(Import-Csv -LiteralPath $CsvPath)
if ($rows.Count -ne $totalRows) {
    throw "샘플 CSV는 정확히 $totalRows 행이어야 합니다: $($rows.Count)"
}
$csvColumns = @($rows[0].PSObject.Properties.Name)
$requiredCsvColumns = @(
    "transaction_id",
    "account_account_number",
    "recipient_account_number",
    "transaction_datetime",
    "transaction_amount",
    "channel",
    "type_general_automatic",
    "access_medium",
    "transaction_num_connection_failure",
    "operating_system",
    "ip_address",
    "mac_address",
    "location",
    "customer_rooting_jailbreak_indicator",
    "customer_mobile_roaming_indicator",
    "customer_vpn_indicator",
    "customer_flag_terminal_malicious_behavior_1",
    "customer_flag_terminal_malicious_behavior_2",
    "customer_flag_terminal_malicious_behavior_3",
    "customer_flag_terminal_malicious_behavior_5",
    "customer_flag_terminal_malicious_behavior_6",
    "is_fraud"
)
$missingCsvColumns = @(
    $requiredCsvColumns | Where-Object { $_ -notin $csvColumns }
)
if (
    $csvColumns.Count -ne $expectedRaw64ColumnCount -or
    $missingCsvColumns.Count -gt 0
) {
    throw (
        "샘플 CSV가 raw64 계약과 다릅니다: columns=$($csvColumns.Count), " +
        "missing=$($missingCsvColumns -join ',')"
    )
}

$normalRows = @($rows | Where-Object { [int]$_.is_fraud -eq 0 }).Count
$fraudRows = @($rows | Where-Object { [int]$_.is_fraud -eq 1 }).Count
if ($normalRows -ne $normalTarget -or $fraudRows -ne $fraudTarget) {
    throw (
        "샘플 라벨 분포가 예상과 다릅니다: " +
        "normal=$normalRows/$normalTarget, fraud=$fraudRows/$fraudTarget"
    )
}

# DB가 transaction_id를 자동 생성하므로 CSV의 문자열 ID로 재실행 여부를
# 확인할 수 없다. 중복 거래 생성을 막기 위해 이 로컬 E2E는 빈 DB에서만 시작한다.
$existingTransactions = @(Invoke-RestMethod `
    -Uri "$BackendUrl/transactions" `
    -TimeoutSec 10
)
if ($existingTransactions.Count -gt 0) {
    throw (
        "거래 DB가 비어 있지 않습니다. 로컬 DB를 초기화한 뒤 다시 실행하세요. " +
        "현재 조회 건수=$($existingTransactions.Count)"
    )
}

$adminHeaders = @{ "X-MLOps-Admin-Token" = $AdminToken }
try {
    $activeRuleSet = Invoke-RestMethod `
        -Uri "$BackendUrl/rule-sets/active" `
        -Headers $adminHeaders `
        -TimeoutSec 10
} catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 404) {
        throw
    }

    $draft = Invoke-RestMethod `
        -Method Post `
        -Uri "$BackendUrl/rule-sets/drafts" `
        -Headers $adminHeaders `
        -ContentType "application/json" `
        -Body "{}" `
        -TimeoutSec 30
    $validation = Invoke-RestMethod `
        -Method Post `
        -Uri "$BackendUrl/rule-sets/$($draft.id)/validate" `
        -Headers $adminHeaders `
        -TimeoutSec 30
    if (-not $validation.valid) {
        throw "기본 룰셋 검증에 실패했습니다."
    }
    $activeRuleSet = Invoke-RestMethod `
        -Method Post `
        -Uri "$BackendUrl/rule-sets/$($draft.id)/activate" `
        -Headers $adminHeaders `
        -TimeoutSec 30
}

function Convert-ToNullableString {
    param([object]$Value)

    if ([string]::IsNullOrWhiteSpace([string]$Value)) {
        return $null
    }
    return [string]$Value
}

function Convert-ToCsvBoolean {
    param(
        [object]$Value,
        [string]$FieldName,
        [string]$SourceRowId
    )

    if ([string]$Value -notin @("0", "1")) {
        throw "거래 $SourceRowId의 $FieldName 값은 0 또는 1이어야 합니다."
    }
    return [bool]([int]$Value)
}

# 생성된 DB ID가 같은 실행 안에서 중복되지 않는지도 별도로 확인한다.
$createdTransactionIds = [System.Collections.Generic.HashSet[long]]::new()
$processed = 0
$results = foreach ($row in $rows) {
    $processed += 1
    Write-Progress `
        -Activity "slim 거래 10,000건 주입" `
        -Status "$processed / $totalRows" `
        -PercentComplete (($processed / $totalRows) * 100)

    $sourceRowId = [string]$row.transaction_id
    $csvLabel = Convert-ToCsvBoolean `
        -Value $row.is_fraud `
        -FieldName "is_fraud" `
        -SourceRowId $sourceRowId

    # train1의 location은 '주소 위도 경도' 한 컬럼이다. API가 요구하는
    # 위도·경도는 문자열 끝의 두 숫자를 분리해 전달한다.
    $locationMatch = [regex]::Match(
        [string]$row.location,
        "(?<latitude>-?\d+(?:\.\d+)?)\s+" +
        "(?<longitude>-?\d+(?:\.\d+)?)$"
    )
    if (-not $locationMatch.Success) {
        throw "거래 $sourceRowId의 location에서 위도·경도를 읽을 수 없습니다."
    }
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $latitude = [double]::Parse(
        $locationMatch.Groups["latitude"].Value,
        $culture
    )
    $longitude = [double]::Parse(
        $locationMatch.Groups["longitude"].Value,
        $culture
    )

    # POST /transactions에는 사용자가 실제로 입력할 수 있는 slim DTO 필드만
    # 보낸다. 고객 상세·계좌 상태·파생 Feature는 현재 Backend의 임시 기본값이
    # raw59의 빈자리를 채우므로 raw64 전체를 억지로 전송하지 않는다.
    $requestPayload = [ordered]@{
        customer_id = $null
        source_account_number = [string]$row.account_account_number
        recipient_account_number = Convert-ToNullableString `
            -Value $row.recipient_account_number
        transaction_datetime = [string]$row.transaction_datetime
        transaction_amount = [long]$row.transaction_amount
        channel = [string]$row.channel
        type_general_automatic = [string]$row.type_general_automatic
        access_medium = Convert-ToNullableString -Value $row.access_medium
        num_connection_failure = [int]$row.transaction_num_connection_failure
        operating_system = Convert-ToNullableString -Value $row.operating_system
        ip_address = Convert-ToNullableString -Value $row.ip_address
        mac_address = Convert-ToNullableString -Value $row.mac_address
        location_lat = $latitude
        location_lon = $longitude
        customer_rooting_jailbreak_indicator = Convert-ToCsvBoolean `
            -Value $row.customer_rooting_jailbreak_indicator `
            -FieldName "customer_rooting_jailbreak_indicator" `
            -SourceRowId $sourceRowId
        customer_mobile_roaming_indicator = Convert-ToCsvBoolean `
            -Value $row.customer_mobile_roaming_indicator `
            -FieldName "customer_mobile_roaming_indicator" `
            -SourceRowId $sourceRowId
        customer_vpn_indicator = Convert-ToCsvBoolean `
            -Value $row.customer_vpn_indicator `
            -FieldName "customer_vpn_indicator" `
            -SourceRowId $sourceRowId
        customer_flag_terminal_malicious_behavior_1 = Convert-ToCsvBoolean `
            -Value $row.customer_flag_terminal_malicious_behavior_1 `
            -FieldName "customer_flag_terminal_malicious_behavior_1" `
            -SourceRowId $sourceRowId
        customer_flag_terminal_malicious_behavior_2 = Convert-ToCsvBoolean `
            -Value $row.customer_flag_terminal_malicious_behavior_2 `
            -FieldName "customer_flag_terminal_malicious_behavior_2" `
            -SourceRowId $sourceRowId
        customer_flag_terminal_malicious_behavior_3 = Convert-ToCsvBoolean `
            -Value $row.customer_flag_terminal_malicious_behavior_3 `
            -FieldName "customer_flag_terminal_malicious_behavior_3" `
            -SourceRowId $sourceRowId
        customer_flag_terminal_malicious_behavior_5 = Convert-ToCsvBoolean `
            -Value $row.customer_flag_terminal_malicious_behavior_5 `
            -FieldName "customer_flag_terminal_malicious_behavior_5" `
            -SourceRowId $sourceRowId
        customer_flag_terminal_malicious_behavior_6 = Convert-ToCsvBoolean `
            -Value $row.customer_flag_terminal_malicious_behavior_6 `
            -FieldName "customer_flag_terminal_malicious_behavior_6" `
            -SourceRowId $sourceRowId
    }

    try {
        $response = Invoke-RestMethod `
            -Method Post `
            -Uri "$BackendUrl/transactions" `
            -ContentType "application/json" `
            -Body ($requestPayload | ConvertTo-Json -Depth 5 -Compress) `
            -TimeoutSec 60
    } catch {
        $detail = $_.ErrorDetails.Message
        if ([string]::IsNullOrWhiteSpace([string]$detail)) {
            $detail = $_.Exception.Message
        }
        throw "거래 $sourceRowId POST 실패: $detail"
    }

    if (
        $response.transaction_id -is [string] -or
        [long]$response.transaction_id -le 0
    ) {
        throw "거래 $sourceRowId의 Backend transaction_id가 양의 정수가 아닙니다."
    }
    $transactionId = [long]$response.transaction_id
    if (-not $createdTransactionIds.Add($transactionId)) {
        throw "Backend가 중복 transaction_id를 반환했습니다: $transactionId"
    }
    if ($response.prediction_status -ne "COMPLETED") {
        throw (
            "거래 $sourceRowId의 ML 추론이 완료되지 않았습니다: " +
            "$($response.prediction_status)"
        )
    }
    if ($null -eq $response.predict_result) {
        throw "거래 $sourceRowId의 predict_result가 비어 있습니다."
    }
    if (
        $null -eq $response.predict_proba -or
        [double]$response.predict_proba -lt 0.0 -or
        [double]$response.predict_proba -gt 1.0
    ) {
        throw "거래 $sourceRowId의 predict_proba 범위가 올바르지 않습니다."
    }

    $mlFraud = [bool]$response.predict_result
    if ($mlFraud) {
        if ($null -eq $response.rule_set_id -or $null -eq $response.rule_scores) {
            throw "사기 판정 거래 $sourceRowId에 룰 점수가 없습니다."
        }
        if ([long]$response.rule_set_id -ne [long]$activeRuleSet.id) {
            throw "거래 $sourceRowId에 현재 ACTIVE 룰셋이 적용되지 않았습니다."
        }
        $ruleTypes = @(
            $response.rule_scores.PSObject.Properties.Name | Sort-Object
        )
        if (($ruleTypes -join ",") -ne ($expectedRuleTypes -join ",")) {
            throw (
                "거래 $sourceRowId의 룰 유형이 예상과 다릅니다: " +
                "$($ruleTypes -join ',')"
            )
        }
    } else {
        if ($null -ne $response.rule_set_id -or $null -ne $response.rule_scores) {
            throw "정상 판정 거래 $sourceRowId에 룰 점수가 생성됐습니다."
        }
        $ruleTypes = @()
    }

    # 학습 정답 is_fraud는 거래 요청과 분리한다. 방금 생성된 정수 ID를 사용해
    # 확정 라벨 API를 호출해야 거래·예측·라벨 FK가 같은 ID로 연결된다.
    $labelResponse = Invoke-RestMethod `
        -Method Put `
        -Uri "$BackendUrl/transactions/$transactionId/label" `
        -ContentType "application/json" `
        -Body (@{ confirmed_is_fraud = $csvLabel } | ConvertTo-Json -Compress) `
        -TimeoutSec 30
    if (
        $labelResponse.transaction_id -is [string] -or
        [long]$labelResponse.transaction_id -ne $transactionId -or
        [bool]$labelResponse.confirmed_is_fraud -ne $csvLabel
    ) {
        throw "거래 $sourceRowId의 확정 라벨 저장 결과가 예상과 다릅니다."
    }

    [pscustomobject]@{
        SourceRowId = $sourceRowId
        TransactionId = $transactionId
        CsvLabel = $csvLabel
        MlFraud = $mlFraud
        Probability = [double]$response.predict_proba
        RuleSetId = $response.rule_set_id
        RuleTypes = if ($ruleTypes.Count) { $ruleTypes -join "," } else { "-" }
    }
}
Write-Progress -Activity "slim 거래 10,000건 주입" -Completed

Write-Output (
    "Backend=$($backendHealth.status), ML=$($mlHealth.status), " +
    "MLPreflight=$($smokeResponse.model_name):$($smokeResponse.model_version), " +
    "SHAPGroups=$smokeShapCount, ActiveRuleSet=$($activeRuleSet.id)"
)
$agreementCount = @($results | Where-Object { $_.CsvLabel -eq $_.MlFraud }).Count
$summary = [pscustomobject]@{
    SelectedRows = $results.Count
    CsvNormalRows = @($results | Where-Object { -not $_.CsvLabel }).Count
    CsvFraudRows = @($results | Where-Object { $_.CsvLabel }).Count
    CreatedRows = $createdTransactionIds.Count
    MlFraudRows = @($results | Where-Object { $_.MlFraud }).Count
    RuleScoredRows = @($results | Where-Object { $null -ne $_.RuleSetId }).Count
    LabelAgreement = "$agreementCount / $($results.Count)"
}
$matrix = foreach ($csvLabel in @($false, $true)) {
    foreach ($mlFraud in @($false, $true)) {
        [pscustomobject]@{
            CsvLabel = if ($csvLabel) { "fraud(1)" } else { "normal(0)" }
            MlPrediction = if ($mlFraud) { "fraud" } else { "normal" }
            Count = @(
                $results |
                    Where-Object {
                        $_.CsvLabel -eq $csvLabel -and $_.MlFraud -eq $mlFraud
                    }
            ).Count
        }
    }
}

$summary | Format-List
$matrix | Format-Table -AutoSize
Write-Output "Top 10 risk examples"
$results |
    Sort-Object Probability -Descending |
    Select-Object -First 10 `
        SourceRowId,TransactionId,CsvLabel,MlFraud,Probability,RuleSetId,RuleTypes |
    Format-Table -AutoSize

if ($summary.RuleScoredRows -ne $summary.MlFraudRows) {
    throw "ML 사기 판정 수와 룰 점수 저장 수가 일치하지 않습니다."
}
if ($summary.MlFraudRows -eq 0) {
    throw "ML 사기 판정이 0건이라 실제 룰 E2E 경로를 확인하지 못했습니다."
}

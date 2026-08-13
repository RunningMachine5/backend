param(
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [string]$MlServingUrl = "http://127.0.0.1:8001",
    [string]$AdminToken = "local-dev-mlops-token",
    [string]$CsvPath = "$PSScriptRoot\data\transactions_model80_1000.csv",
    [string]$ExpectedModelName = "fdshield-fraud-detector-v2",
    [string]$ExpectedModelVersion = "1",
    [string]$SmokePayloadPath = (
        "$PSScriptRoot\..\..\ml\examples\local-model-predict-request.json"
    )
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $CsvPath -PathType Leaf)) {
    throw "raw64 샘플 CSV를 찾을 수 없습니다: $CsvPath"
}
if (-not (Test-Path -LiteralPath $SmokePayloadPath -PathType Leaf)) {
    throw (
        "ML Serving preflight payload를 찾을 수 없습니다: " +
        "$SmokePayloadPath"
    )
}

$totalRows = 1000
$normalTarget = 900
$fraudTarget = 100
$expectedRaw64ColumnCount = 64
$expectedOfficialShapCount = 56

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
    [string]::IsNullOrWhiteSpace([string]$smokePayload.transaction_id) -or
    $smokePropertyNames.Count -ne 60 -or
    $smokePropertyNames -contains "features"
) {
    throw (
        "ML Serving preflight payload는 transaction_id를 포함한 flat raw60이어야 " +
        "합니다: $SmokePayloadPath"
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

if ([string]$smokeResponse.transaction_id -ne [string]$smokePayload.transaction_id) {
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

$rowsFromCsv = @(Import-Csv -LiteralPath $CsvPath)
if ($rowsFromCsv.Count -ne $totalRows) {
    throw "샘플 CSV는 정확히 $totalRows 행이어야 합니다: $($rowsFromCsv.Count)"
}
$csvColumns = @($rowsFromCsv[0].PSObject.Properties.Name)
$requiredCsvColumns = @(
    "transaction_id",
    "customer_birth_date",
    "customer_identification_number",
    "flag_deposit_more_than_tenmillion",
    "customer_id",
    "balance_drain_ratio",
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

# Import-Csv는 모든 값을 문자열로 읽는다. 의미는 바꾸지 않고 DTO가 엄격하게
# 구분하는 bool, 0/1 정수, nullable 값만 JSON 타입으로 바꾼다.
$booleanFields = @(
    "customer_flag_change_of_authentication_1",
    "customer_flag_change_of_authentication_2",
    "customer_flag_change_of_authentication_3",
    "customer_flag_change_of_authentication_4",
    "customer_rooting_jailbreak_indicator",
    "customer_mobile_roaming_indicator",
    "customer_vpn_indicator",
    "customer_flag_terminal_malicious_behavior_1",
    "customer_flag_terminal_malicious_behavior_2",
    "customer_flag_terminal_malicious_behavior_3",
    "customer_flag_terminal_malicious_behavior_5",
    "customer_flag_terminal_malicious_behavior_6",
    "customer_inquery_atm_limit",
    "customer_increase_atm_limit",
    "account_indicator_openbanking",
    "account_release_suspention",
    "another_person_account",
    "unused_terminal_status",
    "flag_deposit_more_than_tenmillion",
    "unused_account_status",
    "recipient_account_suspend_status",
    "first_time_ios_by_vulnerable_user"
)
$binaryIntegerFields = @("account_indicator_release_limit_excess")
$nullableFields = @(
    "account_initial_balance",
    "account_balance",
    "account_remaining_amount_daily_limit_exceeded",
    "access_medium",
    "operating_system",
    "ip_address",
    "mac_address",
    "last_atm_transaction_datetime",
    "last_bank_branch_transaction_datetime",
    "transaction_resumed_date"
)

$selectedNormals = @(
    $rowsFromCsv |
        Where-Object { [int]$_.is_fraud -eq 0 } |
        Select-Object -First $normalTarget
)
$selectedFrauds = @(
    $rowsFromCsv |
        Where-Object { [int]$_.is_fraud -eq 1 } |
        Select-Object -First $fraudTarget
)
if ($selectedNormals.Count -ne $normalTarget) {
    throw "정상 거래 목표 $normalTarget건 중 $($selectedNormals.Count)건만 찾았습니다."
}
if ($selectedFrauds.Count -ne $fraudTarget) {
    throw "이상 거래 목표 $fraudTarget건 중 $($selectedFrauds.Count)건만 찾았습니다."
}
$rows = @($selectedNormals) + @($selectedFrauds) |
    Sort-Object { [string]$_.transaction_id }

$processed = 0
$results = foreach ($row in $rows) {
    $processed += 1
    Write-Progress `
        -Activity "raw64 거래 주입" `
        -Status "$processed / $totalRows" `
        -PercentComplete (($processed / $totalRows) * 100)

    $csvLabel = [bool]([int]$row.is_fraud)
    $createdNow = $false
    try {
        $response = Invoke-RestMethod `
            -Uri "$BackendUrl/transactions/$($row.transaction_id)" `
            -TimeoutSec 20
    } catch {
        if ($_.Exception.Response.StatusCode.value__ -ne 404) {
            throw
        }

        foreach ($field in $booleanFields) {
            $row.$field = [bool]([int]$row.$field)
        }
        foreach ($field in $binaryIntegerFields) {
            $row.$field = [int]$row.$field
        }
        foreach ($field in $nullableFields) {
            if ([string]::IsNullOrWhiteSpace([string]$row.$field)) {
                $row.$field = $null
            }
        }
        $row.is_fraud = [bool]([int]$row.is_fraud)

        $body = $row | ConvertTo-Json -Depth 8 -Compress
        try {
            $response = Invoke-RestMethod `
                -Method Post `
                -Uri "$BackendUrl/transactions" `
                -ContentType "application/json" `
                -Body $body `
                -TimeoutSec 60
        } catch {
            $detail = $_.ErrorDetails.Message
            if ([string]::IsNullOrWhiteSpace([string]$detail)) {
                $detail = $_.Exception.Message
            }
            throw "거래 $($row.transaction_id) POST 실패: $detail"
        }
        $createdNow = $true
    }

    if (
        $response.model_name -ne $ExpectedModelName -or
        [string]$response.model_version -ne $ExpectedModelVersion
    ) {
        throw (
            "거래 $($row.transaction_id)의 모델이 예상과 다릅니다: " +
            "$($response.model_name):$($response.model_version), " +
            "expected=$ExpectedModelName`:$ExpectedModelVersion"
        )
    }

    $ruleTypes = if ($null -eq $response.rule_scores) {
        "-"
    } else {
        ($response.rule_scores.PSObject.Properties.Name | Sort-Object) -join ","
    }

    [pscustomobject]@{
        TransactionId = $row.transaction_id
        CreatedNow = $createdNow
        CsvLabel = $csvLabel
        MlFraud = $response.ml_is_fraud
        Probability = $response.fraud_probability
        Model = "$($response.model_name):$($response.model_version)"
        RuleSetId = $response.rule_set_id
        RuleTypes = $ruleTypes
    }
}
Write-Progress -Activity "raw64 거래 주입" -Completed

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
    CreatedNow = @($results | Where-Object { $_.CreatedNow }).Count
    AlreadyExisted = @($results | Where-Object { -not $_.CreatedNow }).Count
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
        TransactionId,CsvLabel,MlFraud,Probability,RuleSetId,RuleTypes |
    Format-Table -AutoSize

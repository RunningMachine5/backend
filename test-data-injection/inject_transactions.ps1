param(
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [string]$MlServingUrl = "http://127.0.0.1:8001",
    [string]$AdminToken = "local-dev-mlops-token",
    [string]$CsvPath = "$PSScriptRoot\data\transactions_v5_1000.csv",
    [string]$ExpectedModelName = "fdshield-fraud-detector",
    [string]$ExpectedModelVersion = "5",
    [string]$SmokePayloadPath = (
        "$PSScriptRoot\..\..\ml\examples\local-model-predict-request.json"
    )
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $CsvPath)) {
    throw "transactions.csv를 찾을 수 없습니다: $CsvPath"
}
if (-not (Test-Path -LiteralPath $SmokePayloadPath -PathType Leaf)) {
    throw (
        "ML Serving preflight payload를 찾을 수 없습니다: " +
        "$SmokePayloadPath"
    )
}
$totalRows = 1000
$normalTarget = 900
$anomalyTarget = 100

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
if (
    [string]::IsNullOrWhiteSpace([string]$smokePayload.transaction_id) -or
    $null -eq $smokePayload.features
) {
    throw (
        "ML Serving preflight payload에 transaction_id와 features가 필요합니다: " +
        "$SmokePayloadPath"
    )
}

try {
    $smokeResponse = Invoke-RestMethod `
        -Method Post `
        -Uri "$MlServingUrl/predict" `
        -ContentType "application/json" `
        -Body $smokePayloadJson `
        -TimeoutSec 60
} catch {
    $detail = $_.ErrorDetails.Message
    if ([string]::IsNullOrWhiteSpace([string]$detail)) {
        $detail = $_.Exception.Message
    }
    throw "ML Serving preflight /predict 실패: $detail"
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
$smokeShapCount = if ($null -eq $smokeResponse.shap) {
    0
} else {
    @($smokeResponse.shap.PSObject.Properties).Count
}
if ($smokeShapCount -ne 91) {
    throw (
        "ML Serving preflight SHAP Feature 수가 예상과 다릅니다: " +
        "$smokeShapCount, expected=91"
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

# Import-Csv는 모든 값을 문자열로 읽는다. Backend BinaryFlag는 숫자 0/1을
# 요구하므로 값의 의미는 유지하면서 JSON 타입만 숫자로 변환한다.
$binaryFields = @(
    "Customer_flag_change_of_authentication_1",
    "Customer_flag_change_of_authentication_2",
    "Customer_flag_change_of_authentication_3",
    "Customer_flag_change_of_authentication_4",
    "Customer_rooting_jailbreak_indicator",
    "Customer_mobile_roaming_indicator",
    "Customer_VPN_Indicator",
    "Customer_flag_terminal_malicious_behavior_1",
    "Customer_flag_terminal_malicious_behavior_2",
    "Customer_flag_terminal_malicious_behavior_3",
    "Customer_flag_terminal_malicious_behavior_5",
    "Customer_flag_terminal_malicious_behavior_6",
    "Customer_inquery_atm_limit",
    "Customer_increase_atm_limit",
    "Account_indicator_release_limit_excess",
    "Account_indicator_Openbanking",
    "Account_release_suspention",
    "Another_Person_Account",
    "Unused_terminal_status",
    "Flag_deposit_more_than_tenMillion",
    "Unused_account_status",
    "Recipient_account_suspend_status",
    "First_time_iOS_by_vulnerable_user"
)
$nullableDateFields = @(
    "Last_atm_transaction_datetime",
    "Last_bank_branch_transaction_datetime",
    "Transaction_resumed_date"
)

$selectedNormals = @(
    Import-Csv -LiteralPath $CsvPath |
        Where-Object { [int]$_.Is_Fraud -eq 0 } |
        Select-Object -First $normalTarget
)
$selectedAnomalies = @(
    Import-Csv -LiteralPath $CsvPath |
        Where-Object { [int]$_.Is_Fraud -eq 1 } |
        Select-Object -First $anomalyTarget
)
if ($selectedNormals.Count -ne $normalTarget) {
    throw "정상 거래 목표 $normalTarget건 중 $($selectedNormals.Count)건만 찾았습니다."
}
if ($selectedAnomalies.Count -ne $anomalyTarget) {
    throw "이상 거래 목표 $anomalyTarget건 중 $($selectedAnomalies.Count)건만 찾았습니다."
}
$rows = @($selectedNormals) + @($selectedAnomalies) |
    Sort-Object { [string]$_.ID }

$processed = 0
$results = foreach ($row in $rows) {
    $processed += 1
    Write-Progress `
        -Activity "transactions.csv 거래 주입" `
        -Status "$processed / $totalRows" `
        -PercentComplete (($processed / $totalRows) * 100)

    $csvLabel = [bool]([int]$row.Is_Fraud)
    $createdNow = $false
    try {
        $response = Invoke-RestMethod `
            -Uri "$BackendUrl/transactions/$($row.ID)" `
            -TimeoutSec 20
    } catch {
        if ($_.Exception.Response.StatusCode.value__ -ne 404) {
            throw
        }

        foreach ($field in $binaryFields) {
            $row.$field = [int]$row.$field
        }
        foreach ($field in $nullableDateFields) {
            if ([string]::IsNullOrWhiteSpace([string]$row.$field)) {
                $row.$field = $null
            }
        }
        $row.Is_Fraud = [bool]([int]$row.Is_Fraud)

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
            throw "거래 $($row.ID) POST 실패: $detail"
        }
        $createdNow = $true
    }

    if (
        $response.model_name -ne $ExpectedModelName -or
        [string]$response.model_version -ne $ExpectedModelVersion
    ) {
        throw (
            "거래 $($row.ID)의 모델이 예상과 다릅니다: " +
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
        TransactionId = $row.ID
        CreatedNow = $createdNow
        CsvLabel = $csvLabel
        MlFraud = $response.ml_is_fraud
        Probability = $response.fraud_probability
        Model = "$($response.model_name):$($response.model_version)"
        RuleSetId = $response.rule_set_id
        RuleTypes = $ruleTypes
    }
}
Write-Progress -Activity "transactions.csv 거래 주입" -Completed

Write-Output (
    "Backend=$($backendHealth.status), ML=$($mlHealth.status), " +
    "MLPreflight=$($smokeResponse.model_name):$($smokeResponse.model_version), " +
    "SHAP=$smokeShapCount, ActiveRuleSet=$($activeRuleSet.id)"
)
$agreementCount = @($results | Where-Object { $_.CsvLabel -eq $_.MlFraud }).Count
$summary = [pscustomobject]@{
    SelectedRows = $results.Count
    CsvNormalRows = @($results | Where-Object { -not $_.CsvLabel }).Count
    CsvAnomalyRows = @($results | Where-Object { $_.CsvLabel }).Count
    CreatedNow = @($results | Where-Object { $_.CreatedNow }).Count
    AlreadyExisted = @($results | Where-Object { -not $_.CreatedNow }).Count
    MlFraudRows = @($results | Where-Object { $_.MlFraud }).Count
    RuleScoredRows = @($results | Where-Object { $null -ne $_.RuleSetId }).Count
    LabelAgreement = "$agreementCount / $($results.Count)"
}
$matrix = foreach ($csvLabel in @($false, $true)) {
    foreach ($mlFraud in @($false, $true)) {
        [pscustomobject]@{
            CsvLabel = if ($csvLabel) { "anomaly(1)" } else { "normal(0)" }
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

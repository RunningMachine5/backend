# Backend 로컬 slim 거래 E2E 데이터 주입 가이드

GCP와 원격 MLflow 없이 ML 담당자의 `train1.csv` 계약과 전달 모델을 사용해 다음
흐름을 확인한다. 원본에서 정상 9,000건과 사기 라벨 1,000건을 선택하는 생성
스크립트와 Backend 주입 스크립트를 함께 제공한다.

```text
backend/test-data-injection/
├─ README.md
├─ generate_transactions_sample.ps1
├─ inject_transactions.ps1
└─ data/
   └─ transactions_model80_10000.csv
```

```text
train1 raw64 샘플 행
→ slim TransactionRequestDTO만 Backend POST /transactions
→ DB가 양의 정수 transaction_id 발급
→ Backend가 거래 원천값을 저장하고 미구현 고객·계좌·파생값은 임시 기본값으로 보완
→ ML Serving POST /ml/predict (transaction_id + raw59 = flat raw60)
→ ML 공용 전처리 raw59 → model80
→ 판정·확률·모델 이름·버전·지연시간 저장
→ ML 사기 판정 거래만 ACTIVE Rule Set으로 4개 유형 점수 계산
→ 생성된 정수 ID로 CSV의 is_fraud를 PUT /transactions/{id}/label에 저장
```

## 1. 준비 사항

- Windows PowerShell 7
- Docker Desktop
- 같은 상위 폴더에 있는 최신 `backend`, `ml` 저장소
- ML 담당자가 전달한 원본 `train1.csv`
- 포트 `5432`, `8000`, `8001` 사용 가능

명령은 두 저장소의 상위 `RunningMachine5` 폴더에서 실행한다.

```text
RunningMachine5/
├─ backend/
└─ ml/
```

환경 파일이 없을 때만 예시를 복사한다.

```powershell
if (-not (Test-Path -LiteralPath '.\backend\.env')) {
    Copy-Item -LiteralPath '.\backend\.env.example' -Destination '.\backend\.env'
}
if (-not (Test-Path -LiteralPath '.\ml\.env')) {
    Copy-Item -LiteralPath '.\ml\.env.example' -Destination '.\ml\.env'
}
```

`backend/.env`의 로컬 관리자 토큰을 확인한다. 실제 운영 토큰은 Git에 넣지 않는다.

```dotenv
MLOPS_ADMIN_TOKEN=local-dev-mlops-token
```

## 2. 10,000건 CSV 생성

원본 `train1.csv` 경로를 넘겨 비식별화한 샘플을 생성한다.

```powershell
.\backend\test-data-injection\generate_transactions_sample.ps1 `
  -SourceCsvPath 'C:\path\to\train1.csv'
```

출력 파일은 다음 위치에 생성된다.

```text
backend/test-data-injection/data/transactions_model80_10000.csv
```

생성 기준은 다음과 같다.

| 구분 | 선택 건수 | 선택 순서 |
|---|---:|---|
| 정상 거래 | 9,000 | `is_fraud=0`인 행을 원본 순서대로 선택 |
| 사기 라벨 거래 | 1,000 | `is_fraud=1`인 행을 원본 순서대로 선택 |
| 합계 | 10,000 | 정상 블록 다음 사기 블록, 각 블록 내부 원본 순서 유지 |

원본은 정상 197,000건, 사기 3,000건으로 사기 비율이 약 1.5%다. 로컬 샘플은
화면·API·룰 흐름을 충분히 확인하려고 90:10으로 구성하므로 운영 분포나 모델 성능을
대표하지 않는다.

생성 스크립트는 같은 원본 고객·계좌·IP·MAC이 여러 거래에 등장하면 항상 같은
익명값을 사용한다. 출금계좌와 수취계좌도 하나의 공용 계좌 Map으로 변환하므로 역할이
바뀌어도 관계가 유지된다. 수치·범주·거래 시각·금액·위치·위험 신호와 `is_fraud`는
바꾸지 않는다. `transaction_amount`도 원본의 양수 값을 그대로 유지한다.

이미 출력 파일이 있으면 실수로 덮어쓰지 않는다. 다시 만들 때만 `-Force`를 사용한다.

## 3. model80 ML Serving 재빌드·기동

```powershell
Push-Location '.\ml'
docker compose --env-file .env -f compose.serving.yml up -d --build --wait
Pop-Location
```

기본 로컬 모드는 `models/fdshield-fraud-detector-v2`의 전달 XGBoost 모델을 사용하므로
MLflow 주소나 계정이 필요하지 않다. 프로세스 상태는 다음 주소에서 확인한다.

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health'
Invoke-RestMethod -Uri 'http://127.0.0.1:8001/ready'
```

주입 스크립트는 DB를 변경하기 전에 ML 예제 요청이 양의 정수 `transaction_id`를
포함한 flat raw60인지, 응답 모델과 SHAP 계약이 맞는지 검사한다.

## 4. Backend·DB 재빌드 및 migration

```powershell
Push-Location '.\backend'

docker compose --env-file .env -f docker-compose.yml up -d db

docker compose --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.local.yml `
  build backend

docker compose --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.local.yml `
  run --rm --no-deps backend alembic upgrade head

docker compose --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.local.yml `
  up -d --no-deps --force-recreate --wait backend

Pop-Location
```

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health'
```

DB가 생성하는 정수 ID를 사용하므로 기존 거래가 있어도 이어서 주입할 수 있다.
같은 CSV를 다시 실행하면 기존 행을 재사용하지 않고 새로운 거래 ID 10,000개를
발급해 추가 저장한다. 각 확정 라벨은 해당 실행에서 새로 발급된 거래 ID에 연결된다.

## 5. 10,000건 주입

```powershell
.\backend\test-data-injection\inject_transactions.ps1
```

기본값은 `-TransactionsPerSecond 100`이며 `POST /transactions` 요청 시작 속도의
최댓값을 뜻한다. 스크립트는 거래 저장·ML 추론·룰 검증·라벨 저장을 한 행씩
순차 처리하므로 실제 처리량은 Backend·ML·DB 응답시간에 따라 100건/초보다 낮을
수 있다. 더 천천히 확인하려면 다음처럼 조절한다.

```powershell
.\backend\test-data-injection\inject_transactions.ps1 `
  -TransactionsPerSecond 10
```

다른 로컬 모델 버전을 검증할 때만 예상값을 명시한다.

```powershell
.\backend\test-data-injection\inject_transactions.ps1 `
  -ExpectedModelName 'fdshield-fraud-detector-v2' `
  -ExpectedModelVersion '2'
```

처음 실행할 때 ACTIVE 룰셋이 없으면 기본 룰 4개를 생성·검증·활성화한다. 각 CSV
행은 다음 순서로 처리한다.

1. raw64 행에서 slim 거래 요청만 조립한다.
2. `POST /transactions` 응답의 양의 정수 `transaction_id`를 확인한다.
3. `prediction_status=COMPLETED`와 `predict_result`, `predict_proba`를 확인한다.
4. 정상 판정이면 룰 필드가 `null`인지 확인한다.
5. 사기 판정이면 룰셋 ID와 아래 4개 유형 점수가 모두 있는지 확인한다.
6. 같은 정수 ID로 CSV 라벨을 `PUT /transactions/{id}/label`에 저장한다.

```text
VOICE_PHISHING
MESSENGER_PHISHING
ACCOUNT_TAKEOVER
FRAUD_USED_ACCOUNT
```

출력은 다음 내용을 요약한다.

- 정상·사기 라벨 건수와 생성된 정수 거래 ID 수
- ML 사기 판정 수와 룰 점수 저장 수
- CSV 라벨과 ML 판정의 2×2 교차표
- 사기확률 상위 10건과 적용 룰 유형

`RuleScoredRows`는 `MlFraudRows`와 같아야 한다. ML 사기 판정이 0건이면 실제 룰
경로를 확인하지 못한 것이므로 스크립트가 실패로 종료한다. `LabelAgreement`와 교차표는
라벨·예측 연결 확인용이며 독립 검증셋 성능으로 해석하지 않는다. 모델 비교 지표의
원본은 MLflow다.

### 소량 Agent 비동기 E2E 검증

실제 이메일·임베딩·LLM을 사용하는 Agent 흐름은 10,000건 전체가 아니라 소량으로
검증한다. 원본 CSV는 정상 거래 다음 사기 라벨 거래 순서이므로 단순히 앞의 N건을
자르지 않고 정상·사기 라벨 건수를 각각 지정한다.

```powershell
.\backend\test-data-injection\inject_transactions.ps1 `
  -NormalRowLimit 2 `
  -FraudRowLimit 8 `
  -TransactionsPerSecond 1 `
  -WaitForAgent
```

확인하는 전체 흐름은 다음과 같다.

```text
거래 저장 → ML 판정 → 사기 거래 Rule 점수 → 위험등급 산정
→ FastAPI BackgroundTasks에 Agent 등록 → 거래 API 즉시 응답
→ 고객 이메일 → 유형 확실성 분기 → 필요 시 유사 사건 조사
→ 내부 정책 조회 → 대응 가이드 RAG·LLM → AGENT_CASES 저장
→ 거래 ID 기반 Agent 조회 API를 폴링하여 완료 확인
```

`-WaitForAgent`는 소량 실행 옵션과 함께만 사용한다. Agent 완료 기본 대기시간은
180초이며 필요하면 다음처럼 바꿀 수 있다.

```powershell
.\backend\test-data-injection\inject_transactions.ps1 `
  -NormalRowLimit 1 `
  -FraudRowLimit 3 `
  -TransactionsPerSecond 1 `
  -WaitForAgent `
  -AgentWaitTimeoutSeconds 240 `
  -AgentPollIntervalSeconds 2
```

출력되는 Agent 요약 항목은 다음과 같다.

- Agent 대상·완료·실패·시간초과 건수
- 대응 계획 생성 건수
- RAG·LLM 보강 없이 내부 정책만 사용한 대응 계획 건수
- 거래 API 평균 응답시간
- 거래 API 응답 이후 Agent 완료가 관찰될 때까지의 P50·P95 지연시간

`ObservedAgentLatency`는 폴링 간격을 포함한 E2E 관찰값이다. Agent 내부 단계별
정확한 시간은 `agent_cases.generation_metadata`를 별도로 조회해 분석한다.
`COMPLETED` 사건에 대응 계획이 없거나 `FAILED`, `TIMEOUT` 사건이 있으면 스크립트는
실패로 종료한다.

실행 전 `SMTP_TO_EMAIL`이 실제 고객 주소가 아닌 테스트 수신 주소인지 확인한다.
이 모드는 실제 이메일과 OpenAI 호출을 발생시킬 수 있으므로 전체 10,000건 실행에는
`-WaitForAgent`를 사용하지 않는다.

## 6. raw64에서 slim 요청으로 바뀌는 값

`POST /transactions`는 raw64 전체가 아니라 다음 원천값만 받는다.

| slim 요청 필드 | CSV 원본 |
|---|---|
| `customer_id` | 현재 고객 원장이 없으므로 `null` |
| `source_account_number` | `account_account_number` |
| `recipient_account_number` | 같은 이름의 컬럼 |
| `num_connection_failure` | `transaction_num_connection_failure` |
| `location_lat`, `location_lon` | `location` 문자열 끝의 위도·경도 |
| 거래·채널·단말·접속·보안 플래그 | 같은 의미의 train1 컬럼 |

PowerShell `Import-Csv`가 모든 값을 문자열로 읽으므로 bool 플래그 `0/1`은 JSON
boolean으로, 금액과 접속 실패 횟수는 JSON 정수로 변환한다. 비어 있는 선택 문자열은
JSON `null`로 보낸다. `is_fraud`는 거래 요청에 넣지 않고 라벨 API에 따로 보낸다.

현재 Backend에는 실시간 고객·계좌 상세·파생 Feature 계산기가 없다. 따라서 raw64의
나머지 값은 거래 API에 억지로 전달하지 않으며, ML raw59 조립 시 Backend가 준비한
임시 기본값이 사용된다. 이 테스트는 train1의 64개 값을 그대로 추론하는 성능 검증이
아니라 slim 거래 수신부터 ML·룰·라벨 저장까지의 연결 검증이다.

거래 API 응답에는 모델명·버전이 없으므로 해당 값은 ML Serving preflight에서만
검증한다. 거래 응답은 최신 필드인 `predict_result`, `predict_proba`, `rule_set_id`,
`rule_scores`를 사용한다.

## 7. 확인용 URL

- Backend Swagger: <http://127.0.0.1:8000/docs>
- Backend health: <http://127.0.0.1:8000/health>
- ML Serving health: <http://127.0.0.1:8001/health>
- ML Serving readiness: <http://127.0.0.1:8001/ready>
- 최근 거래: <http://127.0.0.1:8000/transactions>

## 8. 종료와 재실행

다음 명령은 컨테이너만 중지하고 PostgreSQL 볼륨은 유지한다.

```powershell
Push-Location '.\backend'
docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml stop backend db
Pop-Location

Push-Location '.\ml'
docker compose --env-file .env -f compose.serving.yml stop ml-serving
Pop-Location
```

`docker compose down -v`는 로컬 PostgreSQL 볼륨을 삭제하므로, 로컬 E2E 데이터를
의도적으로 초기화할 때만 사용한다.

## 9. 자주 발생하는 문제

- CSV 파일 없음: `generate_transactions_sample.ps1`을 먼저 실행한다.
- 출력 파일 이미 존재: 새로 만들 목적일 때만 생성 명령에 `-Force`를 붙인다.
- 거래 DB가 비어 있지 않음: 로컬 DB를 초기화한 뒤 처음부터 다시 실행한다.
- `/mlops` 또는 `/rule-sets`가 503: Backend 관리자 토큰을 확인한다.
- CSV POST가 422: 오류가 난 원본 행 ID와 slim 필드의 타입·nullable 값을 확인한다.
- `prediction_status=FAILED`: ML 컨테이너 로그와 Backend의 ML URL을 확인한다.
- 사기 판정인데 룰 점수가 없음: ACTIVE 룰셋과 룰 컴포넌트를 확인한다.
- ML 사기 판정이 0건: 현재 임시 Feature 조합으로 룰 E2E가 실행되지 않은 상태다.
- `/ml/predict` preflight 실패: ML 컨테이너 로그와 raw60 예제 파일을 확인한다.
- 예상 모델 버전 오류: 로컬 manifest 버전을 확인한 뒤 스크립트 인자로 명시한다.
- 모델 비교 결과: `/mlops/training/runs/{id}/model-details`에서 확인한다.

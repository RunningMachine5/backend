# Backend 로컬 raw60 E2E 데이터 주입 가이드

GCP와 원격 MLflow 없이 ML 담당자의 `train1.csv` 계약과 전달 모델을 사용해 다음 흐름을
확인하는 절차다. 실행 스크립트와 비식별화한 raw64 샘플 1,000건을 함께 제공한다.

```text
backend/test-data-injection/
├─ README.md
├─ inject_transactions.ps1
└─ data/
   └─ transactions_model80_1000.csv
```

```text
raw64 CSV 행
→ Backend POST /transactions
→ raw59를 고객·계좌·거래·파생 피처 테이블에 정규화 저장
→ ML Serving POST /ml/predict (flat raw60)
→ ML 공용 전처리 raw59 → model80
→ 사기 판정·확률·모델 이름·버전·지연시간 저장
→ 사기 판정 거래는 ACTIVE Rule Set으로 4개 유형 점수 계산
→ CSV의 is_fraud를 확정 라벨로 저장
```

## 1. 준비 사항

- Windows PowerShell
- Docker Desktop
- 같은 상위 폴더에 있는 최신 `backend`, `ml` 저장소
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

## 2. model80 ML Serving 재빌드·기동

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

실제 모델명·버전·raw60 추론 계약은 주입 스크립트가 DB를 변경하기 전에 별도로
검증한다.

## 3. Backend·DB 재빌드 및 migration

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

기존 로컬 테스트 데이터를 비우려면 별도로 DB 볼륨을 초기화해야 한다. 아래 7절의
일반 종료 명령은 볼륨을 지우지 않는다.

## 4. raw64 샘플 1,000건 주입

```powershell
.\backend\test-data-injection\inject_transactions.ps1
```

스크립트는 거래나 룰셋을 쓰기 전에 다음 계약을 검사한다.

- ML 예제 JSON이 `transaction_id + raw59`인 flat raw60인가
- `POST /ml/predict`가 성공하는가
- 모델이 기본값 `fdshield-fraud-detector-v2:1`인가
- 공식 응답의 `predict_result`, `predict_proba`, 56개 SHAP 그룹이 유효한가
- 주입 CSV가 정확한 raw64 헤더와 1,000개 행을 갖는가

다른 로컬 모델 버전을 검증하려면 명시적으로 넘긴다.

```powershell
.\backend\test-data-injection\inject_transactions.ps1 `
  -ExpectedModelName 'fdshield-fraud-detector-v2' `
  -ExpectedModelVersion '2'
```

샘플은 ML 담당자의 `train1.csv`에서 다음처럼 층화 선택했다.

| 구분 | 선택 건수 | CSV 기준 |
|---|---:|---|
| 정상 거래 | 900 | `is_fraud=0`인 앞쪽 900건 |
| 사기 라벨 거래 | 100 | `is_fraud=1`인 앞쪽 100건 |
| 합계 | 1,000 | raw64 값·라벨 유지, 식별값만 비식별화 |

원본 사기 비율은 약 1.5%다. 1,000건을 단순 절단하면 사기 라벨이 약 15건뿐이므로
화면·API·룰 점수 흐름을 충분히 확인할 수 있도록 이 로컬 샘플만 900:100으로 구성했다.
이 분포는 운영 분포나 모델 성능을 대표하지 않는다.

거래·고객·출금계좌·수취계좌 ID, 고객명·식별번호, IP·MAC은 관계를 보존하는
`LOCAL_*` 또는 문서용 주소로 바꿨다. 모델80 계산에 쓰이는 수치·범주·거래 시각·금액·
거리·위험 신호와 `is_fraud`는 원본 값을 유지한다. `transaction_amount`는 양수이며
`account_balance`는 원본처럼 음수가 될 수 있다.

처음 실행할 때 ACTIVE 룰셋이 없으면 기본 룰 4개를 생성·검증·활성화한다. 같은
`transaction_id`가 이미 있으면 POST하지 않고 기존 결과를 조회하므로 재실행할 수 있다.
ML preflight는 매번 실행한다.

출력은 다음 내용을 요약한다.

- 정상·사기 라벨 선택 수
- 새로 생성된 수와 기존 조회 수
- ML 사기 판정 수와 룰 점수 저장 수
- CSV 라벨과 ML 판정의 2×2 교차표
- 사기확률 상위 10건과 적용 룰 유형

`LabelAgreement`와 교차표는 라벨 저장·예측 연결을 확인하는 참고값이다. 독립 검증셋
성능이나 일반화 성능으로 해석하지 않는다. 모델 비교 지표는 MLflow를 원본으로 본다.

## 5. CSV 타입 변환

PowerShell `Import-Csv`는 모든 값을 문자열로 읽는다. 스크립트는 의미를 바꾸지 않고
다음 값만 JSON 타입으로 변환한다.

- raw59의 bool 플래그 `0/1` → JSON boolean
- `account_indicator_release_limit_excess` → JSON 숫자 `0/1`
- `is_fraud` → JSON boolean
- 비어 있는 OS·IP·MAC·선택 날짜 → JSON `null`

나머지 숫자 문자열과 `time_difference`는 Backend DTO가 검증·정규화한다. CSV의 알려진
오타 `flag_deposit_more_than_tenmillion`은 입력 alias로만 허용되며 내부에서는
`flag_deposit_more_than_ten_million`으로 통일된다.

## 6. 확인용 URL

- Backend Swagger: <http://127.0.0.1:8000/docs>
- Backend health: <http://127.0.0.1:8000/health>
- ML Serving health: <http://127.0.0.1:8001/health>
- ML Serving readiness: <http://127.0.0.1:8001/ready>
- 최근 거래: <http://127.0.0.1:8000/transactions>

## 7. 종료와 재실행

다음 명령은 컨테이너만 중지하고 PostgreSQL 볼륨은 유지한다.

```powershell
Push-Location '.\backend'
docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml stop backend db
Pop-Location

Push-Location '.\ml'
docker compose --env-file .env -f compose.serving.yml stop ml-serving
Pop-Location
```

`docker compose down -v`는 로컬 PostgreSQL 볼륨을 삭제하므로 데이터 초기화가 목적일
때만 사용한다.

## 8. 자주 발생하는 문제

- `/mlops` 또는 `/rule-sets`가 503: Backend 관리자 토큰을 확인하고 재생성한다.
- CSV POST가 422: 응답 detail의 raw64 필드명·타입과 nullable 여부를 확인한다.
- 거래 POST가 409: 동일 거래 ID 또는 고객·계좌 식별값 충돌이다. 제공 스크립트는
  동일 거래 ID라면 기존 결과를 조회한다.
- 사기 판정인데 `rule_scores=null`: ACTIVE 룰셋 유무를 확인한다.
- `/ml/predict` preflight 실패: ML 컨테이너 로그와 raw60 예제 파일을 확인한다.
- 예상 모델 버전 오류: 현재 로컬 manifest 버전을 확인한 뒤 스크립트 인자로 명시한다.
- 모델 비교 결과: `/mlops/training/runs/{id}/model-details`에서 확인한다.

# Backend 로컬 테스트 데이터 주입 가이드

이 문서는 GCP와 원격 MLflow를 사용하지 않고 Git에 포함된 운영 v5 모델로 다음 흐름을
확인하는 절차다.

이 폴더 하나에 실행 스크립트와 1,000건 샘플 CSV가 함께 들어 있다.

```text
backend/test-data-injection/
├─ README.md
├─ inject_transactions.ps1
└─ data/
   └─ transactions_v5_1000.csv
```

```text
transactions.csv 일부 행
→ Backend POST /transactions
→ Git 포함 fdshield-fraud-detector v5 /predict
→ ML 결과 저장
→ 사기 거래는 ACTIVE Rule Set으로 4개 유형 점수 계산
→ 확정 라벨과 함께 PostgreSQL 저장
```

## 1. 준비 사항

- Windows PowerShell
- Docker Desktop 실행
- `backend/dev`, `ml/main` 최신 코드
- 포트 `5432`, `8000`, `8001` 사용 가능

아래 명령은 두 저장소가 다음처럼 같은 상위 폴더에 있다고 가정한다.

```text
RunningMachine5/
├─ backend/
└─ ml/
```

먼저 PowerShell에서 `RunningMachine5` 폴더로 이동한다. 이후 명령은 모두 이 위치를
기준으로 실행한다.

실제 비밀값이 든 `.env`는 Git에 커밋하지 않는다.

Backend 환경파일이 없을 때만 예시를 복사한다.

```powershell
if (-not (Test-Path -LiteralPath '.\backend\.env')) {
    Copy-Item -LiteralPath '.\backend\.env.example' -Destination '.\backend\.env'
}
```

`backend/.env`에 로컬 공용 테스트 토큰이 있어야 한다.

```dotenv
MLOPS_ADMIN_TOKEN=local-dev-mlops-token
```

이 값은 로컬 개발용이다. 운영 토큰으로 사용하거나 Git에 넣으면 안 된다.

ML 환경파일이 없을 때만 예시를 복사한다.

```powershell
if (-not (Test-Path -LiteralPath '.\ml\.env')) {
    Copy-Item -LiteralPath '.\ml\.env.example' -Destination '.\ml\.env'
}
```

## 2. 고정 운영 v5 ML Serving 재빌드·기동

```powershell
Push-Location '.\ml'
docker compose --env-file .env -f compose.serving.yml up -d --build --wait
Pop-Location
```

ML 저장소에는 약 1.3MB의 `fdshield-fraud-detector` v5 native XGBoost 모델이 포함돼 있다.
기본 `ML_PREDICTOR_MODE=local`은 이 모델을 사용하므로 MLflow 주소·계정·비밀번호가
필요하지 않다. 사용자 실행용 Stub 모드는 지원하지 않는다.

확인:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health'
```

정상이면 `status=ok`가 나온다. Health는 프로세스 상태만 확인하며, 실제 모델
추론과 버전은 4절의 주입 스크립트가 별도로 preflight 검증한다.

## 3. Backend·DB 재빌드 및 migration

```powershell
Push-Location '.\backend'

# DB 볼륨은 유지하고 컨테이너만 기동한다.
docker compose --env-file .env -f docker-compose.yml up -d db

# 최신 Backend 이미지를 만든다.
docker compose --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.local.yml `
  build backend

# 기존 로컬 DB에 미적용 migration만 순서대로 적용한다.
docker compose --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.local.yml `
  run --rm --no-deps backend alembic upgrade head

# 새 이미지로 Backend 컨테이너를 교체하고 health를 기다린다.
docker compose --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.local.yml `
  up -d --no-deps --force-recreate --wait backend

Pop-Location
```

확인:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health'
```

## 4. transactions.csv 1,000건 주입

`RunningMachine5`에서 다음 명령 하나만 실행한다.

```powershell
.\backend\test-data-injection\inject_transactions.ps1
```

스크립트는 어떤 거래나 룰셋을 DB에 쓰기 전에 sibling ML 저장소의
`ml/examples/local-model-predict-request.json`을 `POST /predict`로 직접 전송한다.
응답의 모델이 `fdshield-fraud-detector:5`이고 실제 XGBoost contribution이
91개인 경우에만 계속한다. smoke payload 파일이 없거나 응답 계약이 다르면
DB를 변경하지 않고 즉시 실패한다.

스크립트는 함께 커밋된 `backend/test-data-injection/data/transactions_v5_1000.csv`를
사용한다. 이 파일은 생성형 원본 `transactions.csv`에서 다음 기준으로 미리 추출했다.

| 구분 | 선택 건수 | CSV 기준 |
|---|---:|---|
| 정상 거래 | 900 | `Is_Fraud=0`인 앞쪽 900건 |
| 이상 거래 | 100 | `Is_Fraud=1`인 앞쪽 100건 |
| 합계 | 1,000 | ML Feature·라벨 유지, 직접 식별자 비식별화 |

원본 데이터의 사기 비율은 약 1.5%이므로 단순히 앞에서 1,000건만 자르면 이상 거래가
약 15건뿐이다. 화면·API·룰엔진 테스트에 충분한 이상 거래를 포함하도록 정상과 이상
라벨을 900:100으로 층화 선택했다. 팀원은 별도의 91MB 원본 CSV를 내려받을 필요가 없다.
`Is_Fraud`는 확정 라벨 저장에만 사용하며 모델의 예측 확률 계산에는 전달하지 않는다.

Git에 안전하게 공유할 수 있도록 거래·고객·계좌·수취계좌 식별자와 이름·식별번호,
IP·MAC은 관계를 보존하는 `LOCAL_*` 값으로 비식별화했다. ML 추론 Feature 54개의 수치와
범주, 거래 시각·금액·위치·위험 신호 및 `Is_Fraud` 라벨은 유지한다.

각 행은 다음 흐름으로 한 건씩 처리한다.

```text
CSV 행 타입 변환
→ POST /transactions
→ 고정 운영 v5 /predict
→ 예측 결과와 91개 실제 XGBoost contribution 저장
→ 사기 판정이면 ACTIVE 룰셋 4개 유형 점수 저장
```

처음 실행할 때 ACTIVE 룰셋이 없으면 코드에 포함된 최종 기본 룰 4개를 생성·검증·활성화한다.
이미 같은 ID가 저장돼 있으면 중복 POST하지 않고 기존 결과를 조회하므로 재실행할 수 있다.
이 경우에도 preflight `/predict`는 매번 실행하므로, 재사용하는 DB 결과뿐 아니라
현재 8001번에 기동한 ML Serving이 정확한 v5인지도 검증한다.

1,000개 거래를 표로 전부 출력하지 않고 다음 내용만 보여준다.

- 선택한 정상·이상 거래 수
- 새로 생성된 수와 이미 존재한 수
- ML 사기 판정 수와 룰 점수 저장 수
- CSV 라벨과 ML 판정의 2×2 교차표
- 사기확률이 높은 상위 10건과 주요 SHAP 신호

현재 고정 운영 v5와 로컬 PostgreSQL에서 새 `LOCAL_V5_TX_*` 거래로 확인한 판정 분포는
다음과 같다.

| CSV 라벨 | ML 정상 | ML 사기 | 합계 |
|---|---:|---:|---:|
| 정상 0 | 898 | 2 | 900 |
| 이상 1 | 0 | 100 | 100 |
| 합계 | 898 | 102 | 1,000 |

ML이 사기로 판정한 102건에는 ACTIVE 룰셋의 4개 사기유형 점수가 저장됐다. CSV 라벨과
ML 판정은 998건에서 일치했다. 두 번째 동일 실행에서는 `CreatedNow=0`,
`AlreadyExisted=1000`으로 기존 결과를 재사용했다.

이 결과는 생성형 1,000건 로컬 표본에 대한 연동 확인값이다. 독립적인 운영 성능평가나
일반화 성능으로 해석하지 않는다.

응답의 `shap`은 v5 XGBoost가 계산한 실제 per-transaction contribution이며 모델 Feature
91개 키를 모두 반환한다. 스크립트의 `TopSignals`는 절대 기여도가 큰 상위 3개를
보여준다. 응답에서는 XGBoost bias 항을 제외하므로 SHAP 합계만으로 예측확률을 다시
계산하면 안 된다. 전체 Feature importance와 학습 시 생성한 SHAP summary plot은 MLflow
학습 artifact에서 관리하며 이 로컬 거래 E2E 범위에는 포함하지 않는다.

## 5. CSV 타입 변환이 필요한 이유

PowerShell `Import-Csv`는 모든 값을 문자열로 읽는다. Backend의 `BinaryFlag`는 숫자 `0/1`만 허용하므로 문자열 `"0"`, `"1"`을 그대로 JSON으로 보내면 HTTP 422가 발생한다.

스크립트는 CSV 값의 의미를 변경하지 않고 다음 타입 변환만 수행한다.

- BinaryFlag 문자열 `0/1` → JSON 숫자 `0/1`
- `Is_Fraud` 문자열 `0/1` → JSON boolean
- 비어 있는 선택 날짜 → JSON `null`

`Customer_personal_identifier`는 `테스트고객000001` 형태이며 서로 다른 고객에 같은
이름이 존재하도록 구성해 동명이인 저장도 함께 검증한다. `Customer_identification_number`
는 고객별 고유 `LOCAL-ID-*` 값이다. 두 값은 ML 추론 Feature 54개에는 포함되지 않는다.

## 6. 확인용 URL

- Backend Swagger: <http://127.0.0.1:8000/docs>
- Backend health: <http://127.0.0.1:8000/health>
- ML Serving health: <http://127.0.0.1:8001/health>
- 최근 거래: <http://127.0.0.1:8000/transactions>

## 7. 종료와 재실행

컨테이너만 중지하고 DB 볼륨은 유지한다.

```powershell
Push-Location '.\backend'
docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml stop backend db
Pop-Location

Push-Location '.\ml'
docker compose --env-file .env -f compose.serving.yml stop ml-serving
Pop-Location
```

`docker compose down -v`는 로컬 PostgreSQL 볼륨을 삭제하므로 데이터 초기화가 목적이 아니면 사용하지 않는다.

## 8. 자주 발생하는 문제

- `/mlops` 또는 `/rule-sets`가 503: `backend/.env`의 `MLOPS_ADMIN_TOKEN`을 확인하고 Backend를 재생성한다.
- CSV POST가 422이고 `Input should be 0 or 1`: 문자열 BinaryFlag를 숫자로 변환하지 않은 요청이다.
- 거래 POST가 409: 같은 CSV ID가 이미 저장돼 있다. 제공 스크립트는 기존 결과를 조회한다.
- 사기인데 `rule_scores=null`: ACTIVE 룰셋이 없는지 확인한다.
- Backend에서 ML 호출 실패: 고정 v5 ML Serving이 8001번에서 healthy인지 확인한다.
- `ML Serving preflight payload를 찾을 수 없습니다`: Backend·ML 저장소가 같은 상위 폴더에 있고 ML 최신 코드인지 확인한다.
- `ML Serving preflight /predict 실패`: health만이 아니라 v5 모델 로드와 54개 Feature 추론이 정상인지 ML Serving 로그를 확인한다.
- `expected=fdshield-fraud-detector:5` 오류: ML 저장소 최신 코드로 다시 빌드한다.
- 전체 Feature importance와 학습 SHAP summary는 MLflow 학습 artifact에서 확인한다.

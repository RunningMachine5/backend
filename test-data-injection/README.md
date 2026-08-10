# Backend 로컬 테스트 데이터 주입 가이드

이 문서는 GCP와 원격 MLflow를 사용하지 않고 로컬에서 다음 흐름을 확인하는 절차다.

이 폴더 하나에 실행 스크립트와 1,000건 샘플 CSV가 함께 들어 있다.

```text
backend/test-data-injection/
├─ README.md
├─ inject_transactions.ps1
└─ data/
   └─ transactions_stub_1000.csv
```

```text
transactions.csv 일부 행
→ Backend POST /transactions
→ 로컬 ML Serving Stub /predict
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

## 2. ML Serving Stub 재빌드·기동

```powershell
Push-Location '.\ml'
$env:ML_PREDICTOR_MODE='stub'
$env:ML_MODEL_NAME='fdshield-rule-based-stub'
$env:ML_MODEL_VERSION='0'

docker compose --env-file .env -f compose.serving.yml up -d --build --wait
Pop-Location
```

확인:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health'
```

정상이면 `status=ok`가 나온다.

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

스크립트는 함께 커밋된 `backend/test-data-injection/data/transactions_stub_1000.csv`를
사용한다. 이 파일은 생성형 원본 `transactions.csv`에서 다음 기준으로 미리 추출했다.

| 구분 | 선택 건수 | CSV 기준 |
|---|---:|---|
| 정상 거래 | 900 | `Is_Fraud=0`인 앞쪽 900건 |
| 이상 거래 | 100 | `Is_Fraud=1`인 앞쪽 100건 |
| 합계 | 1,000 | ML Feature·라벨 유지, 직접 식별자 비식별화 |

원본 데이터의 사기 비율은 약 1.5%이므로 단순히 앞에서 1,000건만 자르면 이상 거래가
약 15건뿐이다. 화면·API·룰엔진 테스트에 충분한 이상 거래를 포함하도록 정상과 이상
라벨을 900:100으로 층화 선택했다. 팀원은 별도의 91MB 원본 CSV를 내려받을 필요가 없다.
`Is_Fraud`는 확정 라벨 저장에만 사용하며 Stub의 예측 확률 계산에는 전달하지 않는다.

Git에 안전하게 공유할 수 있도록 거래·고객·계좌·수취계좌 식별자와 이름·식별번호,
IP·MAC은 관계를 보존하는 `LOCAL_*` 값으로 비식별화했다. ML 추론 Feature 54개의 수치와
범주, 거래 시각·금액·위치·위험 신호 및 `Is_Fraud` 라벨은 유지한다.

각 행은 다음 흐름으로 한 건씩 처리한다.

```text
CSV 행 타입 변환
→ POST /transactions
→ ML Stub /predict
→ 예측 결과와 91개 더미 SHAP 저장
→ 사기 판정이면 ACTIVE 룰셋 4개 유형 점수 저장
```

처음 실행할 때 ACTIVE 룰셋이 없으면 코드에 포함된 최종 기본 룰 4개를 생성·검증·활성화한다.
이미 같은 ID가 저장돼 있으면 중복 POST하지 않고 기존 결과를 조회하므로 재실행할 수 있다.

1,000개 거래를 표로 전부 출력하지 않고 다음 내용만 보여준다.

- 선택한 정상·이상 거래 수
- 새로 생성된 수와 이미 존재한 수
- ML 사기 판정 수와 룰 점수 저장 수
- CSV 라벨과 ML 판정의 2×2 교차표
- 사기확률이 높은 상위 10건과 주요 SHAP 신호

현재 Stub과 로컬 DB에서 확인한 판정 분포는 다음과 같다.

| CSV 라벨 | ML 정상 | ML 사기 | 합계 |
|---|---:|---:|---:|
| 정상 0 | 886 | 14 | 900 |
| 이상 1 | 23 | 77 | 100 |
| 합계 | 909 | 91 | 1,000 |

ML이 사기로 판정한 91건에는 ACTIVE 룰셋의 4개 사기유형 점수가 저장됐다. CSV 라벨과
ML 판정은 963건에서 일치했다. 이 숫자는 실제 모델 성능이 아니라 현재 규칙 기반 Stub의
로컬 연동 결과다.

Stub은 정답 라벨을 보지 않고 실제 54개 원본 Feature를 91개로 전처리한 뒤 다음 신호를
결정적으로 조합한다.

- 거래금액과 잔액·일 한도·최근 최대금액·표준편차의 상대 비율
- VPN, 루팅, 인증 변경, 악성 단말 행동, 접속 실패
- 미사용 단말·계좌, 정지 수취계좌, 타인 계좌 여부
- 거래 시각, 이전 거래와의 시간 차이·이동 거리
- 수취계좌 거래 횟수와 과거 이력

응답의 `shap`은 실제 ML 모델의 SHAP가 아니다. 스텁 확률을 만든 **log-odds 기여도**를
실제 XGBoost 응답과 동일하게 91개 모델 Feature 형태로 채운 더미 설명값이다. 고정된
규칙 가중치가 그대로 보이지 않도록 거래 Feature 값에 따라 재현 가능한 소수점 값으로
분산한다. 직접 위험 계산에 사용된 Feature에는 큰 값을, 나머지 Feature에는 전체 합을
바꾸지 않는 작은 양·음수 배경값을 넣어 실제 SHAP과 비슷한 밀도로 보이게 한다.
스크립트 출력의 `TopSignals`에서 영향이 큰 상위 3개 신호를 볼 수 있다. 따라서
UI·Backend 계약 확인에는 쓸 수 있지만, 모델 성능 평가나 실제 사유 해석에는 사용하지
않는다.

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
- Backend에서 ML 호출 실패: ML Stub이 8001번에서 healthy인지 확인한다.
- 실제 MLflow 모델·Feature importance·SHAP summary는 이 Stub E2E 범위가 아니다.

# AI 기반 금융 이상거래 탐지 및 대응 파이프라인

FastAPI가 거래 한 건을 받아 PostgreSQL에 저장하고, ML Serving의 실제 모델 예측과
동적 사기유형 룰 점수를 함께 기록하는 Backend입니다. Agent와 고객 챗봇 영역은
각 담당자가 교체할 수 있도록 기존 스켈레톤을 별도로 유지합니다.

## 실행

uv로 의존성을 설치하고 DB 마이그레이션을 적용한 뒤 FastAPI를 실행합니다.

```bash
uv sync
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

테스트:

```bash
uv run python -m unittest discover -s tests -v
```

## Docker 실행

`.env.example`을 `.env`로 복사하고 비밀번호를 변경합니다.

### 1. DB만 Docker로 실행하고 Backend는 로컬에서 실행

일반적인 Backend 개발 방식입니다. 기본 Compose에는 ParadeDB만 들어 있으므로
다음 명령으로 Backend 컨테이너 없이 DB만 실행합니다.

이전 Compose 구성으로 Backend 컨테이너를 이미 실행했던 PC에서는 최초 한 번
다음 명령으로 기존 DB와 Backend 컨테이너를 내립니다. `-v`를 사용하지 않으므로
DB 볼륨은 삭제되지 않습니다.

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  down
```

그다음 기본 구성을 실행합니다.

```bash
docker compose up -d
docker compose ps
```

의존성을 설치하고 DB 마이그레이션을 적용한 뒤 FastAPI 개발 서버를 실행합니다.

```bash
uv sync
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

확인 주소:

- API: `http://localhost:8000`
- Health Check: `http://localhost:8000/health`
- Swagger UI: `http://localhost:8000/docs`

### 실제 ML Serving 연동 확인

ML 저장소의 서빙 서버를 먼저 `localhost:8001`에 실행한 뒤 거래를 한 건씩 요청합니다.
거래 API는 계좌번호, 거래 시각·금액, 채널과 단말 위험 신호만 담은 Slim JSON을
받습니다. Backend는 거래와 계좌를 저장하고, 아직 준비되지 않은 고객·계좌 상세와
파생 Feature에는 임시 기본값을 붙여 ML 추론용 raw59를 조립합니다. 별도의
`transactions.raw_features` JSON 스냅샷 컬럼은 사용하지 않습니다.

```bash
curl -X POST http://localhost:8000/transactions \
  -H "Content-Type: application/json" \
  --data @examples/transaction-request.json
```

요청 예시는 [`examples/transaction-request.json`](examples/transaction-request.json)에
있습니다. `transaction_id`는 Backend DB가 생성하므로 요청에서 보내지 않습니다.
개인정보 원장이 아직 없으면 `customer_id`를 `null`로 보낼 수 있고, Backend는 가짜 고객
행을 저장하지 않은 채 ML 조립 시에만 임시 고객 프로필을 사용합니다. 수취 계좌가 없는
ATM 거래는 `recipient_account_number`도 `null`로 보낼 수 있습니다. 거래금액의 부호는
요청과 ML 입력에서 유지하고, 룰의 금액 임계값은 거래 규모를 보도록 절댓값을 사용합니다.

ML 응답이 정상 저장되면 `prediction_status`는 `COMPLETED`가 됩니다. ML 서버가
꺼져 있거나 응답 계약이 다르면 거래 원본은 유지되고 POST 응답은 `FAILED`가 됩니다.
현재 ERD에는 실패 이력 컬럼이 없으므로 ML 실패 자체는 별도 결과 행으로 저장하지 않습니다.
Timeout·네트워크 오류와 `429`, `5xx` 응답은 scale-to-zero 재기동 같은 일시 오류로
보고 기본 2회까지 호출하며, `4xx` 입력 오류와 응답 계약 오류는 재시도하지 않습니다.
횟수와 간격은 `ML_SERVING_MAX_ATTEMPTS`, `ML_SERVING_RETRY_DELAY_SECONDS`로
조절합니다.

ML이 사기로 예측한 거래는 활성 룰셋으로 모든 사기유형 점수를 계산합니다.
Backend는 하나의 대표 유형을 확정하지 않으며 `rule_scores`에 유형별 점수를 전부
저장하고 응답합니다. 화면에서 필요한 상위 N개 선택과 정렬은 이 값을 사용하는
클라이언트가 담당합니다. 정상 거래이거나 점수를 계산하지 못한 경우에는
`rule_scores`가 `null`입니다.

ML 추론, 실시간 룰 점수, 룰셋 테스트, 과거 거래 재현은 모두 동일한 raw60
계약(`transaction_id` + snake_case Feature 59개)을 사용합니다. 룰 관리 화면에는
이 중 이름·계좌번호·IP·MAC·위치·생년월일 같은 식별 원본을 노출하지 않고,
생년월일은 거래 시점 연령인 `transaction_age`로만 제공합니다. 이전 대문자 표기
Feature 이름은 신규 룰 조건식에서 지원하지 않습니다. 다만 운영 DB의 기존
ACTIVE 룰셋을 새 기본 룰셋으로 교체하기 전까지 엔진 내부에서만 기존 이름을 한시
해석하며, `/rule-features`에는 노출하지 않습니다.

룰 수정 전 영향 확인은 DRAFT 룰셋에 대해
`POST /rule-sets/{draft_rule_set_id}/replay`를 호출합니다. 현재 ACTIVE 룰셋과 DRAFT를
최신 ML 결과가 사기인 과거 거래에 각각 적용해 점수·매칭 근거 변화만 비교합니다.
`sample_size`는 기본·최대 1,000건, `detail_limit`은 기본·최대 100건이며 DB에는 새
예측이나 룰 점수를 쓰지 않습니다. 확정 사기유형 정답을 비교하는 정확도 평가가 아니라
룰 변경 영향 재현입니다.

```json
{
  "prediction_status": "COMPLETED",
  "ml_is_fraud": true,
  "fraud_probability": 0.9959,
  "rule_scores": {
    "VOICE_PHISHING": 0.70,
    "FRAUD_USED_ACCOUNT": 0.20,
    "ACCOUNT_TAKEOVER": 0.10,
    "MESSENGER_PHISHING": 0.05
  }
}
```

저장된 거래와 점수는 `GET /transactions/{transaction_id}`로 다시 조회할 수 있습니다.
담당자가 거래의 사기 여부를 확정하면 다음 API로 재학습용 이진 라벨을 저장합니다.

```bash
curl -X PUT http://localhost:8000/transactions/TX-001/label \
  -H "Content-Type: application/json" \
  -d '{"confirmed_is_fraud":true}'
```

라벨이 없으면 `transaction_labels`에 생성하고, 기존 값과 다르면 확정값과
`labeled_at`을 갱신합니다. 같은 값을 반복 요청하면 기존 행과 확정 시각을 유지합니다.
거래가 없으면 `404`를 반환합니다. `POST /transactions`, `GET /transactions`,
`GET /transactions/{transaction_id}` 응답에서도 `confirmed_is_fraud`와 `labeled_at`을
확인할 수 있습니다. 사기 유형 확정값은 Agent 검토 영역에서 별도로 관리하며, 이
테이블에는 ML 이진 재학습에 필요한 정답만 저장합니다.

DB 로그와 종료 명령:

```bash
docker compose logs -f db
docker compose down
```

### 2. DB와 Backend를 모두 Docker로 실행

Docker 이미지나 컨테이너 환경까지 통합 확인할 때만 로컬 오버레이를 함께
사용합니다.

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  up -d --build

docker compose \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  ps

curl http://localhost:8000/health
```

Backend 로그 확인:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  logs -f backend
```

두 컨테이너 종료:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  down
```

### 3. 개발 VM 배포

개발 VM에서는 기본 파일과 운영 오버레이를 병합합니다.

```bash
BACKEND_IMAGE=asia-northeast3-docker.pkg.dev/<GCP_PROJECT_ID>/fdshield/backend:<commit-sha> \
docker compose \
  --project-name fdshield \
  --env-file .env.dev \
  -f backend/docker-compose.yml \
  -f backend/docker-compose.prod.yml \
  up -d db backend
```

운영 오버레이는 `BACKEND_IMAGE`에 Artifact Registry의 커밋 SHA 이미지를
요구합니다. 실제 배포에서는 GitHub Actions가 이미지 주소를 주입하고 위 작업을
자동으로 수행하므로, 일반적으로 팀원이 VM에서 직접 실행하지 않습니다.

VM에서는 DB 5432와 Backend 8000 포트를 호스트에 직접 공개하지 않으며,
Backend는 Nginx를 통해서만 외부에 노출합니다.

> 운영 VM에서 `docker compose down -v`를 실행하면 DB 볼륨이 삭제될 수 있으므로
> 사용하지 않습니다.

## CI/CD

- PR: uv 의존성 동기화, 단위 테스트, Docker 이미지 빌드
- `dev` push: 테스트 후 GCP Artifact Registry 이미지 발행, 공용 개발 VM의 Backend 컨테이너 교체
- `main`은 운영 서버가 준비되기 전까지 자동배포하지 않음
- 운영 이미지는 `asia-northeast3-docker.pkg.dev/project-4cc3406c-72d8-4907-a5d/fdshield/backend:<commit-sha>` 형식을 사용
- GitHub Actions는 Workload Identity Federation으로 GCP에 인증하며 서비스 계정 JSON 키를 저장하지 않음
- DB 비밀번호와 애플리케이션 환경변수는 개발 VM의 `/opt/fdshield/.env.dev`에 저장

GitHub Repository Secrets:

- `VM_HOST`
- `VM_USER`
- `VM_SSH_PORT`
- `VM_SSH_PRIVATE_KEY`
- `VM_SSH_KNOWN_HOSTS`

VM에는 배포 사용자가 쓰기 가능한 `/opt/fdshield/backend` 디렉터리와 Docker
Engine 및 Docker Compose 플러그인이 미리 준비되어 있어야 합니다.
VM에 연결된 서비스 계정에는 `fdshield` Artifact Registry 저장소의
`Artifact Registry Reader` 역할이 필요합니다. 배포 시 VM 메타데이터에서 단기
토큰을 발급받아 이미지를 pull하며 장기 Registry 비밀번호는 저장하지 않습니다.
ParadeDB의 최초 초기화 과정에서 PostgreSQL이 한 번 재시작되므로 Alembic
마이그레이션은 일시적인 연결 실패 시 최대 30회 재시도합니다.

## MLOps 관리자 API

`MLOPS_ADMIN_TOKEN`이 비어 있으면 `/mlops` 전체가 `503`으로 비활성화됩니다. 운영
토큰은 저장소가 아니라 VM의 `/opt/fdshield/.env.dev` 또는 Secret Manager에 저장하고
모든 요청의 `X-MLOps-Admin-Token` 헤더로 전달합니다.

권장 실행 순서는 다음과 같습니다.

1. 이미 준비된 GCS CSV는 `POST /mlops/datasets`로 등록합니다. DB 확정 라벨을
   반영할 때는 `POST /mlops/datasets/build`로 고정 원본
   `gs://fdshield-ml-data-801817539291/base/train1.csv`에서 새 불변 CSV와
   데이터셋 버전을 함께 만듭니다.
2. 등록된 `dataset_version_id`로 `POST /mlops/training/runs`를 호출합니다. Backend가
   `training_runs` 이력을 만든 뒤 Cloud Run Training Job을 시작합니다.
3. Training Job은 후보와 현재 champion을 평가하고 성공 시 `status`, `mlflow_run_id`,
   실제 Cloud Run execution 이름만 `POST /mlops/training/runs/{id}/result`로 보냅니다.
4. Backend의 `training_runs`에는 실행 연결 정보와 상태만 저장합니다. 관리자는
   `GET /mlops/training/runs/{id}/model-details`에서 MLflow 원본의 모델 버전·지표·파라미터·
   태그를 확인한 뒤 `POST /mlops/training/runs/{id}/decision`으로 승인 또는 거절합니다.
5. 승인하면 Backend가 `mlflow_run_id`에 대응하는 등록 모델 버전을 MLflow에서 확인합니다.
   리비전 생성은 ML Serving CD만 담당합니다. CD가 `model-v<version>` 태그의 최신 Ready
   리비전을 0%로 준비한 뒤 승인 API를 호출해야 합니다. Backend는 모델명·버전·mlflow
   모드, digest 고정 이미지, 기존 운영 트래픽 100%를 검증해 그 리비전만 재사용하며,
   태그가 없거나 계약이 다르면 승인하지 않습니다. 검증 성공 뒤에만 MLflow 승인 태그와
   학습 실행 상태 `STAGED`를 기록합니다. 별도 Cloud Run 작업이 없으므로
   `operation_id`는 `null`입니다.
6. Ready 상태를 확인한 뒤 `POST /mlops/serving/promotions`로 실제 예측 스모크를
   실행하고, 성공한 경우에만 새 리비전으로 트래픽 100% 이동을 요청합니다. 요청자가
   모델 버전을 직접 지정하지 않습니다.
7. `POST /mlops/training/runs/{id}/deployment/complete`로 선택적인 비동기 operation과
   실제 Ready 리비전·100% 트래픽을 확인합니다. 성공하면 MLflow `champion` alias를
   바꾸고 학습 이력을 `PRODUCTION`으로 확정합니다.

핵심 요청 형태는 다음과 같습니다. 아래 `features`의 말줄임은 설명용 축약이며 실제
승격 스모크 요청에는 [`examples/transaction-request.json`](examples/transaction-request.json)
에서 `transaction_id`와 학습 메타데이터 4개를 제외한 raw59를 넣습니다.

```text
POST /mlops/datasets
{"version": "generated-v2",
 "gcs_uri": "gs://bucket/datasets/generated/v2/transactions.csv",
 "row_count": 210000}

POST /mlops/datasets/build
{"version": "generated-v2",
 "gcs_uri": "gs://bucket/datasets/generated/v2/transactions.csv"}

POST /mlops/training/runs
{"dataset_version_id": 2, "min_pr_auc": 0.75, "min_recall": 0.8}

POST /mlops/training/runs/12/result
{"status": "SUCCEEDED", "mlflow_run_id": "a1b2c3...",
 "cloud_run_execution_name": "fdshield-binary-training-abcde"}

GET /mlops/training/runs/12/model-details

POST /mlops/training/runs/12/decision
{"decision": "APPROVE", "reason": "동일 검증셋에서 Recall 상승, FPR 감소"}

POST /mlops/serving/promotions
{"training_run_id": 12, "transaction_id": "TX-SMOKE",
 "features": {...raw59 전체 필드...}}

POST /mlops/training/runs/12/deployment/complete
{"operation_id": "<serving promotion 응답의 operation_id>"}
```

`deployment/complete`의 `operation_id`는 선택 사항입니다. 유실됐거나 페이지를 다시
연 경우 `{}`로 호출하면 실제 Ready 리비전과 100% 트래픽 상태를 기준으로 복구 확인합니다.
학습 결과 callback이 유실됐다면 `POST /mlops/training/runs/{id}/reconcile`로 저장된
Cloud Run Execution의 종결 상태를 대조할 수 있습니다. Execution 실패는 `FAILED`로
정리하지만, 성공한 실행의 `mlflow_run_id`는 추측하지 않으므로 callback 설정을 고쳐야
   합니다. CD 후보 리비전이 교체되었거나 검증이 필요해진 `STAGED` 실행은 ML Serving
   CD를 정상 완료한 뒤 `{"decision":"APPROVE","restage":true}`로 다시 검증합니다.
   동일 모델 태그가 존재하지만 아직 reconciling 중이거나 Ready·환경변수·digest·트래픽
   검증을 통과하지 못하면 Backend는 중복 리비전을 만들지 않고 staging 요청을 거절합니다.

운영 재학습 데이터는 ML 담당자의 raw64 CSV 하나로 고정합니다. 데이터셋 버전의
`gcs_uri`를 `TRAINING_DATA_URI`로 전달하고 ML이 내부에서 raw59→model80 공용 전처리를
수행합니다. 별도의 전처리 결과 CSV나 보조 CSV 조합은 Backend 관리 API에서 지원하지
않습니다.
학습·검증 분리 정책과 모델별 임계값은 Training Job이 결정하고 MLflow에 기록합니다.
최신 Backend의 데이터셋 요청과 DB에는 `split_datetime`이 없습니다. 모델 버전, 후보·
champion 비교 지표와 추천 결과도 `training_runs`에 복제하지 않고 MLflow를 원본으로
조회합니다. alias와 Serving 트래픽 변경은 Backend 관리자 승인 API에서만 수행합니다.

`POST /mlops/datasets/build`는 고정 원본 CSV의 모든 행을 그대로 복사한 뒤
`transaction_labels`의 확정 이진 라벨 거래를 모두 추가합니다. DB 행은 `customers`,
출금·수취 `accounts`, `transactions`, `derived_features`를 한 번에 조인해 raw59와
학습 메타데이터를 재조립한 raw64 행입니다. 학습에서 `transaction_id`를 피처로 쓰지
않으므로 원본 ID와 DB ID를 비교하거나 변환하지 않습니다. 기준 객체는 수정하지 않으며
GCS generation precondition으로 목적 객체 덮어쓰기도 금지합니다. 병합 결과의 원본 행 수와
추가 라벨 수는 API 응답에 포함됩니다.

Training Job에는 다음 설정을 추가해야 합니다. callback token은 평문 환경변수가 아닌
Secret Manager로 주입합니다.

```text
TRAINING_RESULT_CALLBACK_URL=https://api.fdshield.cloud/mlops/training/runs/{training_run_id}/result
TRAINING_RESULT_CALLBACK_TOKEN=<MLOPS_ADMIN_TOKEN과 동일한 보호 값>
```

Backend가 모델 상세 조회·승인·최종 alias 변경을 하려면 `MLFLOW_TRACKING_URI`,
`MLFLOW_TRACKING_USERNAME`, `MLFLOW_TRACKING_PASSWORD`와 `MLOPS_MODEL_ALIAS`를 설정합니다.
실제 계정과 비밀번호는 `.env.example`이나 Git에 넣지 않습니다. 실패 callback은
`{"status":"FAILED","error_message":"..."}` 형태이며, 같은 결과 callback은 멱등하게
처리됩니다.
`model-details` 응답은 MLflow `artifact_uri`와 후보·champion 성능 비교 파일의 상대 경로
`model_comparison_artifact_path=metadata/model-comparison.json`을 제공합니다. 모델 지표·
파라미터·태그와 비교 결과는 MLflow를 원본으로 사용하며 Backend DB에 복제하지 않습니다.
운영 VM 서비스 계정에는 최소한 Cloud Run Job 실행·조회와 Service 조회·트래픽 수정
권한이 필요합니다. Serving 리비전 생성과 해당 런타임 서비스 계정에 대한
`iam.serviceAccounts.actAs` 권한은 ML Serving CD 배포 서비스 계정에만 부여합니다.
데이터셋 빌드 기능을 사용할 때는 기준 객체 읽기와 새 객체 생성에 필요한
`storage.objects.get`, `storage.objects.create` 권한도 학습 데이터 버킷에 필요합니다.
학습 Job 서비스 계정에는 GCS 학습 객체 읽기와 MLflow Secret 접근 권한이, Serving
서비스 계정에는 MLflow Secret 접근 권한이 필요합니다. CD의 새 리비전 준비나 승격
스모크 테스트가 실패하면 트래픽 전환이 호출되지 않으므로 기존 추론 리비전은 계속
서비스합니다.
Cloud Run Service는 요청이 없으면 자동 scale-to-zero 되므로 별도의 "서버 끄기" API는
두지 않습니다.

## 전체 흐름

```text
POST /transactions
  -> raw59를 고객·계좌·거래·파생 피처 네 테이블에 정규화 저장
  -> 네 테이블에서 동일한 raw59 재조립
  -> ML Serving /ml/predict에 flat raw60 호출
  -> ML 공용 전처리로 model80 생성·추론
  -> 사기 판정·확률·모델 이름·버전·지연시간 저장
  -> 사기 예측이면 활성 룰셋으로 최종 4개 유형 점수 계산·저장
  -> 거래와 최신 ML·룰 결과 응답

Agent / 고객 질문 스켈레톤
  -> 저장된 탐지 결과를 각 담당 영역에서 사용
  -> Fake 임베딩
  -> Fake VectorDB / 가이드 검색
  -> Fake LLM / 알림
```

## 프로젝트 구조

```text
.
├── main.py
├── app
│   ├── api
│   │   ├── transaction.py
│   │   ├── fraud_rule.py
│   │   └── mlops.py
│   ├── dto
│   │   ├── transaction.py
│   │   ├── ml_prediction.py
│   │   └── fraud_rule.py
│   ├── data
│   │   └── model
│   │       ├── customer.py
│   │       ├── account.py
│   │       ├── transaction.py
│   │       ├── derived_features.py
│   │       ├── ml_prediction_result.py
│   │       ├── mlops.py
│   │       └── fraud_rule.py
│   ├── repositories
│   │   └── transaction.py
│   ├── pipelines
│   │   └── fraud_detection_pipeline.py
│   ├── services
│   │   ├── ml_serving
│   │   │   └── client.py
│   │   └── rules
│   │       ├── engine.py
│   │       ├── feature_builder.py
│   │       └── scoring.py
│   └── ... Agent·챗봇 스켈레톤
├── migrations
├── examples
│   └── transaction-request.json
└── tests
```

## 팀 간 DTO 계약

| DTO | 생산자 | 소비자 |
|---|---|---|
| `TransactionCreateDTO` | 거래 API 클라이언트 | 사기 탐지 파이프라인 |
| `MLTransactionFeatures` | 거래 API 클라이언트 | ML Serving |
| `MLPredictionResponse` | ML Serving | 사기 탐지 파이프라인 |
| `TransactionResponseDTO` | 사기 탐지 파이프라인 | 거래 API 클라이언트 |
| `TransactionDTO` | 구형 Agent 스켈레톤 | 구형 Agent 스켈레톤 |
| `FraudAssessmentDTO` | 구형 Agent 스켈레톤 | Agent, 대시보드 |
| `RagQueryDTO` | Agent/RAG 쿼리 담당 | VectorDB 검색 담당 |
| `RetrievedContextDTO` | VectorDB 검색 담당 | LLM 답변 담당 |

실제 거래 탐지는 `TransactionCreateDTO`를 받아 ML Serving과 룰 점수를 차례로 실행합니다.
구형 Agent DTO는 실제 거래 탐지 결과에 맞춘 Agent 계약을 확정한 뒤 제거합니다.

## Agent Fake 구현 범위

- VectorDB: 어떤 쿼리에도 동일한 모니터링 문맥 반환
- LLM: 입력 DTO의 문맥을 문자열 템플릿으로 조합
- 이메일/대시보드: `print()`로 출력

거래 수신, ML Serving 호출, PostgreSQL 저장, 동적 룰 점수 계산은 Fake 범위가 아닙니다.

고객 대응 챗봇의 Fake 구현(`customer_chatbot_pipeline.py`, `FakeEmbedder`,
`FakeGuideRetriever`, `build_chatbot_chain`)은 제거했습니다. 이 영역은
[docs/customer-chatbot/](docs/customer-chatbot/) 설계에 따라 실제 구현으로 다시 만드는 중입니다.


# AI 기반 금융 이상거래 탐지 및 대응 파이프라인

실제 외부 API, 머신러닝 모델, VectorDB, RDB를 연결하지 않고 전체 데이터 흐름과 팀 간 DTO 계약을 확인하기 위한 Python 스켈레톤입니다.

## 실행

Python 표준 라이브러리만 사용합니다.

```bash
python3 main.py
```

테스트:

```bash
python3 -m unittest discover -s tests -v
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

### ML Stub 연동 확인

ML 저장소의 서빙 서버를 먼저 `localhost:8001`에 실행한 뒤 거래를 요청합니다.
`raw_data`는 새 전처리가 요구하는 원본 Feature 54개를 모두 포함해야 합니다.
Backend는 타입과 필수 컬럼을 검증해 원본 JSON을 저장하고, One-hot Encoding 없이
ML `/predict` 요청의 `features`로 그대로 전달합니다.

```bash
curl -X POST http://localhost:8000/transactions \
  -H "Content-Type: application/json" \
  --data @examples/transaction-request.json
```

요청 예시는 [`examples/transaction-request.json`](examples/transaction-request.json)에
있습니다. `Location`과 `Time Difference`는 필수이며, 이전 계약의
`Time_difference`, `Transaction_Failure_Status`,
`Customer_flag_terminal_malicious_behavior_4`는 허용하지 않습니다.

ML 응답이 정상 저장되면 `prediction_status`는 `COMPLETED`가 됩니다. ML 서버가
꺼져 있거나 응답 계약이 다르면 거래 원본은 유지되고 상태만 `FAILED`로 저장됩니다.

ML이 사기로 예측한 거래는 활성 룰셋으로 모든 사기유형 점수를 계산합니다.
Backend는 하나의 대표 유형을 확정하지 않으며 `rule_scores`에 유형별 점수를 전부
저장하고 응답합니다. 화면에서 필요한 상위 N개 선택과 정렬은 이 값을 사용하는
클라이언트가 담당합니다. 정상 거래이거나 점수를 계산하지 못한 경우에는
`rule_scores`가 `null`입니다.

```json
{
  "prediction_status": "COMPLETED",
  "ml_is_fraud": true,
  "fraud_probability": 0.9959,
  "rule_scores": {
    "VOICE_PHISHING": 0.70,
    "FRAUD_USED_ACCOUNT": 0.20,
    "ACCOUNT_TAKEOVER": 0.10,
    "MESSENGER_PHISHING": 0.05,
    "CARD_FRAUD": 0.00
  }
}
```

저장된 거래와 점수는 `GET /transactions/{transaction_id}`로 다시 조회할 수 있습니다.

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

1. `POST /mlops/training/runs`로 Cloud Run Training Job을 시작합니다.
2. 응답의 `operation_id`를 `GET /mlops/operations/{id}`로 조회하고,
   `GET /mlops/training/status`에서 최신 Execution 성공을 확인합니다.
3. MLflow에 등록된 정확한 숫자 버전으로 `POST /mlops/serving/revisions`를 호출합니다.
   이 단계는 기존 리비전의 트래픽 100%를 고정하고 새 리비전을 태그 URL에만 띄웁니다.
4. operation 완료와 `GET /mlops/serving/status`의 Ready 상태를 확인합니다.
5. 같은 버전과 원본 Feature 54개로 `POST /mlops/serving/promotions`를 호출합니다.
   Backend가 태그 URL에 실제 `/predict` 요청을 보내 모델명·버전을 검증한 경우에만
   새 리비전으로 트래픽 100% 이동을 요청합니다.

핵심 요청 형태는 다음과 같습니다. 승격의 `features`에는
[`examples/transaction-request.json`](examples/transaction-request.json)의 `raw_data`
54개 필드를 넣습니다.

```text
POST /mlops/training/runs
{"auto_promote": true, "min_pr_auc": 0.75, "min_recall": 0.8,
 "dataset_uri": "gs://bucket/datasets/generated/v1/transactions.csv",
 "split_datetime": "2026-04-01 00:00:00"}

POST /mlops/serving/revisions
{"model_version": "17"}

POST /mlops/serving/promotions
{"model_version": "17", "transaction_id": "TX-SMOKE", "features": {}}
```

기본 운영 학습은 생성형 원본 `transactions.csv`를 `dataset_uri` 하나로 지정하며,
ML이 내부에서 54→91 전처리를 수행합니다. 이미 전처리된 `train.csv`를 직접 지정하는
경우에만 행 수와 행 순서가 같은 원본 `transactions.csv`를 `transactions_uri`로 함께
보내야 합니다. Backend는 각각 `TRAINING_DATA_URI`, `TRAINING_TRANSACTIONS_URI`
override로 전달하고, 실제 데이터 종류와 두 파일의 정렬은 ML Job이 검증합니다.
`split_datetime`은 원본 `Transaction_Datetime` 기준 시간 분할 경계이며
`TRAINING_SPLIT_DATETIME`으로 전달됩니다. URI 필드를 생략하면 Cloud Run Job에 미리
설정된 값을 그대로 사용하므로 기존 `{}` 실행 요청은 호환됩니다. 의도하지 않은 모델
자동 승격을 막기 위해 `auto_promote` 기본값은 `false`입니다.

운영 VM 서비스 계정에는 최소한 Cloud Run Job 실행·조회, Service 조회·수정 권한과
Serving 리비전 서비스 계정에 대한 `iam.serviceAccounts.actAs` 권한이 필요합니다.
학습 Job 서비스 계정에는 GCS 학습 객체 읽기와 MLflow Secret 접근 권한이, Serving
서비스 계정에는 MLflow Secret 접근 권한이 필요합니다. 새 리비전 생성 또는 스모크
테스트가 실패하면 승격 API가 호출되지 않으므로 기존 추론 리비전은 계속 서비스합니다.
Cloud Run Service는 요청이 없으면 자동 scale-to-zero 되므로 별도의 "서버 끄기" API는
두지 않습니다.

## 전체 흐름

```text
거래정보
  -> Fake 이진 분류 모델
  -> 이상패턴 탐지
  -> 사기유형별 패턴 점수 가중합
  -> 위험등급 / 대표 사기유형 / 근거
  -> 위험등급 분기
       ├─ VERY_HIGH: 피해자 안내 이메일 전송(print)
       └─ 그 외: RAG 쿼리 -> Fake VectorDB -> Fake LLM -> 담당자 대시보드(print)

고객 질문
  -> Fake 임베딩
  -> Fake 고객 가이드 검색
  -> Fake RDB 거래 조회
  -> Fake LLM 챗봇 답변(print)
```

## 프로젝트 구조

```text
.
├── main.py
├── app
│   ├── domain
│   │   └── enums.py
│   ├── dto
│   │   ├── transaction.py
│   │   ├── fraud.py
│   │   ├── agent.py
│   │   └── chatbot.py
│   ├── data
│   │   └── fake_data.py
│   ├── services
│   │   ├── classification
│   │   │   └── fake_fraud_model.py
│   │   ├── analysis
│   │   │   ├── pattern_detector.py
│   │   │   ├── fraud_type_scorer.py
│   │   │   └── risk_grader.py
│   │   ├── rag
│   │   │   ├── query_builder.py
│   │   │   ├── fake_vector_db.py
│   │   │   └── fake_llm.py
│   │   ├── notification
│   │   │   └── fake_email_sender.py
│   │   └── chatbot
│   │       ├── fake_embedder.py
│   │       ├── fake_guide_retriever.py
│   │       └── fake_transaction_repository.py
│   ├── pipelines
│   │   ├── fraud_detection_pipeline.py
│   │   ├── monitoring_agent_pipeline.py
│   │   └── customer_chatbot_pipeline.py
│   └── presentation
│       └── console_renderer.py
└── tests
    └── test_pipelines.py
```

## 팀 간 DTO 계약

| DTO | 생산자 | 소비자 |
|---|---|---|
| `TransactionDTO` | 입력/데이터 담당 | 분류 모델, 패턴 분석, 챗봇 |
| `FraudPredictionDTO` | 머신러닝 담당 | 사기 탐지 파이프라인 |
| `PatternScoreDTO` | 이상패턴 담당 | 사기유형 점수 담당 |
| `FraudAssessmentDTO` | 사기 탐지 파이프라인 | Agent, 대시보드 |
| `RagQueryDTO` | Agent/RAG 쿼리 담당 | VectorDB 검색 담당 |
| `RetrievedContextDTO` | VectorDB 검색 담당 | LLM 답변 담당 |
| `ChatbotRequestDTO` | 고객 채널 담당 | 대응가이드 챗봇 |
| `ChatbotResponseDTO` | 대응가이드 챗봇 | 고객 채널 담당 |

DTO 필드를 먼저 합의하면 각 담당자는 다른 구현이 완성되지 않아도 Fake 서비스를 사용해 독립 개발할 수 있습니다.

## Fake 구현 범위

- 머신러닝: 금액, 사용자 평소 금액 대비 편차, 결제수단, 업종을 이용한 규칙 기반 확률
- VectorDB: 어떤 쿼리에도 동일한 모니터링 문맥 반환
- RDB: 코드에 하드코딩된 거래 리스트에서 사용자 거래 조회
- LLM: 입력 DTO의 문맥을 문자열 템플릿으로 조합
- 이메일/대시보드: `print()`로 출력
- 예외처리: 요구사항에 따라 의도적으로 구현하지 않음


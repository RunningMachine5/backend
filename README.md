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

```bash
docker compose up -d --build
curl http://localhost:8000/health
```

개발 VM 배포에서는 `docker-compose.prod.yml`을 VM 보안 오버레이로 병합하며,
DB 5432와 Backend 8000 포트는 호스트에 직접 공개하지 않습니다. Backend는
Nginx를 통해서만 노출합니다.

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


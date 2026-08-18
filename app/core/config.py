import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://root:1234@localhost:5432/fdshield-db",
)


ML_SERVING_URL = os.getenv("ML_SERVING_URL", "http://localhost:8001").rstrip("/")
ML_SERVING_TIMEOUT_SECONDS = float(os.getenv("ML_SERVING_TIMEOUT_SECONDS", "5"))
ML_SERVING_AUTH_MODE = os.getenv("ML_SERVING_AUTH_MODE", "none").strip().lower()
ML_SERVING_MAX_ATTEMPTS = max(1, int(os.getenv("ML_SERVING_MAX_ATTEMPTS", "2")))
ML_SERVING_RETRY_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("ML_SERVING_RETRY_DELAY_SECONDS", "0.25")),
)

CHAT_BASE_URL = os.getenv("CHAT_BASE_URL", "http://localhost:8000").rstrip("/")
CHAT_FALLBACK_EMAIL = os.getenv("CHAT_FALLBACK_EMAIL", "abcd@kosa.com").strip()
# 챗봇 LLM 타임아웃 설정
CHAT_LLM_TIMEOUT_SECONDS = float(os.getenv("CHAT_LLM_TIMEOUT_SECONDS", "30"))
# LLM 실패시 최대 재시도 횟수
CHAT_LLM_MAX_ATTEMPTS = max(1, int(os.getenv("CHAT_LLM_MAX_ATTEMPTS", "2")))
# 챗봇 고객응답 평가 추출 시 사용할 LLM 모델
CHAT_LLM_MODEL = os.getenv("CHAT_LLM_MODEL", "gpt-5.6-luna").strip()
# 챗봇 고객 응답시 사용할 LLM 모델
CHAT_RESPONSE_LLM_MODEL = os.getenv("CHAT_RESPONSE_LLM_MODEL", "gpt-5.6-luna").strip()
# 추론 모델의 노력을 low 로 설정해 응답속도를 빠르게 한다
CHAT_LLM_REASONING_EFFORT = os.getenv("CHAT_LLM_REASONING_EFFORT", "low").strip()

# 챗봇 접속 URL 안내 메일은 Agent 이상거래 안내 메일과 같은 SMTP 계정을 쓴다.
# 접속 정보(SMTP_HOST/PORT/PASSWORD/TIMEOUT)는 SmtpEmailMessageSender.from_env가 읽는다.
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "").strip()
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "FDShield").strip()

# Backend가 Cloud Run Training Job과 Serving Service를 제어할 때 사용하는 설정입니다.
# 운영 VM에서는 연결된 서비스 계정의 ADC(메타데이터 자격 증명)를 사용합니다.
GCP_PROJECT_ID = os.getenv(
    "GCP_PROJECT_ID",
    "project-4cc3406c-72d8-4907-a5d",
).strip()
GCP_REGION = os.getenv("GCP_REGION", "asia-northeast3").strip()
CLOUD_RUN_TRAINING_JOB = os.getenv(
    "CLOUD_RUN_TRAINING_JOB",
    "fdshield-binary-training",
).strip()
CLOUD_RUN_TRAINING_CONTAINER = os.getenv(
    "CLOUD_RUN_TRAINING_CONTAINER",
    "",
).strip()
CLOUD_RUN_SERVING_SERVICE = os.getenv(
    "CLOUD_RUN_SERVING_SERVICE",
    "fdshield-ml-serving",
).strip()
CLOUD_RUN_SERVING_CONTAINER = os.getenv(
    "CLOUD_RUN_SERVING_CONTAINER",
    "",
).strip()
CLOUD_RUN_ADMIN_TIMEOUT_SECONDS = float(
    os.getenv("CLOUD_RUN_ADMIN_TIMEOUT_SECONDS", "20")
)
MLOPS_MODEL_NAME = os.getenv(
    "MLOPS_MODEL_NAME",
    "fdshield-fraud-detector-v2",
).strip()
MLOPS_MODEL_ALIAS = os.getenv("MLOPS_MODEL_ALIAS", "champion").strip()

# Backend는 학습 실행 이력에 MLflow run ID만 저장합니다. 모델 버전과 학습
# 지표는 MLflow가 원본이므로 승인/상세 조회 시 Registry REST API에서 확인합니다.
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "").strip().rstrip("/")
MLFLOW_TRACKING_USERNAME = os.getenv("MLFLOW_TRACKING_USERNAME", "").strip()
MLFLOW_TRACKING_PASSWORD = os.getenv("MLFLOW_TRACKING_PASSWORD", "")
MLFLOW_TRACKING_TIMEOUT_SECONDS = float(
    os.getenv("MLFLOW_TRACKING_TIMEOUT_SECONDS", "10")
)

# 비어 있으면 /mlops 관리 API를 503으로 비활성화합니다. 운영 값은 Secret Manager
# 또는 VM의 보호된 .env 파일에서 주입하고 저장소에 커밋하지 않습니다.
MLOPS_ADMIN_TOKEN = os.getenv("MLOPS_ADMIN_TOKEN", "")

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

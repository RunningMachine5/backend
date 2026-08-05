import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://root:1234@localhost:5432/fdshield-db",
)

ML_SERVING_URL = os.getenv("ML_SERVING_URL", "http://localhost:8001").rstrip("/")
ML_SERVING_TIMEOUT_SECONDS = float(os.getenv("ML_SERVING_TIMEOUT_SECONDS", "5"))

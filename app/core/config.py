import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://root:1234@localhost:5432/fdshield-db",
)
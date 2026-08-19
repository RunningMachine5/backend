from datetime import date, datetime

from sqlalchemy import BigInteger, Column, DateTime
from sqlmodel import Field, SQLModel

from app.data.model.types import BIGINT_PRIMARY_KEY


class DatasetVersion(SQLModel, table=True):
    """학습에 사용한 GCS 데이터셋 버전."""

    __tablename__ = "dataset_versions"

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    version: str = Field(max_length=64, unique=True)
    gcs_uri: str = Field(max_length=2048)
    row_count: int = Field(ge=0, sa_type=BigInteger)
    period_start: date | None = Field(default=None)
    period_end: date | None = Field(default=None)
    period_normal_count: int = Field(default=0, ge=0, sa_type=BigInteger)
    period_fraud_count: int = Field(default=0, ge=0, sa_type=BigInteger)
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class TrainingRun(SQLModel, table=True):
    """Cloud Run Job과 MLflow를 연결하는 학습 실행 이력."""

    __tablename__ = "training_runs"

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BIGINT_PRIMARY_KEY,
            primary_key=True,
            autoincrement=True,
        ),
    )
    model_key: str = Field(max_length=128)
    dataset_version_id: int = Field(
        foreign_key="dataset_versions.id",
        ondelete="RESTRICT",
        index=True,
        sa_type=BIGINT_PRIMARY_KEY,
    )
    cloud_run_execution_name: str | None = Field(default=None, max_length=512)
    mlflow_run_id: str | None = Field(default=None, max_length=255)
    status: str = Field(max_length=32, index=True)
    error_message: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


__all__ = ["DatasetVersion", "TrainingRun"]

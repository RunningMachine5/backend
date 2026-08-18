"""서비스, 리포지토리 객체 자동 주입을 위한 의존성 정의 코드"""

from typing import Annotated
from fastapi import Depends, Request

from app.core.db import SessionDep
from app.pipelines.d_fraud_detection_pipline import DFraudDetectionPipeline
from app.repositories.feature_context import FeatureContextRepository
from app.repositories.transaction import TransactionRepository
from app.services.features.derived_features_service import DerivedFeatureService
from app.services.ml_serving.predict_client import MLServingClient
from app.services.transaction.transaction_service import TransactionService


def get_feature_context_repository(session: SessionDep) -> FeatureContextRepository:
    return FeatureContextRepository(session)

FeatureContextRepositoryDep = Annotated[
    FeatureContextRepository, Depends(get_feature_context_repository),
]

def get_derived_feature_service(repository: FeatureContextRepositoryDep) -> DerivedFeatureService:
    return DerivedFeatureService(repository)

DerivedFeatureServiceDep = Annotated[
    DerivedFeatureService, Depends(get_derived_feature_service)
]

def get_transaction_service(session: SessionDep) -> TransactionService:
    return TransactionService(TransactionRepository(session))

TransactionServiceDep = Annotated[
    TransactionService, Depends(get_transaction_service)
]

def get_ml_serving_client(request: Request) -> MLServingClient:
    return request.app.state.ml_serving_client

MLServingClientDep = Annotated[
    MLServingClient, Depends(get_ml_serving_client)
]

def get_fraud_detection_pipeline(derived_feature_service: DerivedFeatureServiceDep, client: MLServingClientDep, transaction_service: TransactionServiceDep) -> DFraudDetectionPipeline:
    return DFraudDetectionPipeline(derived_feature_service, client, transaction_service)

DFraudDetectionPipelineDep = Annotated[
    DFraudDetectionPipeline, Depends(get_fraud_detection_pipeline)
]


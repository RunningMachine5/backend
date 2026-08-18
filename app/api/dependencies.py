"""서비스와 파이프라인 의존성 정의."""

from typing import Annotated

from fastapi import Depends

from app.core.db import SessionDep
from app.pipelines.d_fraud_detection_pipline import DFraudDetectionPipeline
from app.repositories.derived_features import DerivedFeaturesRepository
from app.repositories.feature_context import FeatureContextRepository
from app.repositories.transaction import TransactionRepository
from app.services.features.derived_features_service import DerivedFeatureService
from app.services.ml_serving.client import MLServingClientDep
from app.services.transaction.detection_result_service import DetectionResultService
from app.services.transaction.transaction_service import TransactionService


def get_derived_features_repository(
    session: SessionDep,
) -> DerivedFeaturesRepository:
    return DerivedFeaturesRepository(session)


DerivedFeaturesRepositoryDep = Annotated[
    DerivedFeaturesRepository, Depends(get_derived_features_repository)
]


def get_feature_context_repository(
    session: SessionDep,
) -> FeatureContextRepository:
    return FeatureContextRepository(session)


FeatureContextRepositoryDep = Annotated[
    FeatureContextRepository,
    Depends(get_feature_context_repository),
]


def get_derived_feature_service(
    feature_context_repository: FeatureContextRepositoryDep,
    derived_features_repository: DerivedFeaturesRepositoryDep,
) -> DerivedFeatureService:
    return DerivedFeatureService(
        feature_context_repository,
        derived_features_repository,
    )


DerivedFeatureServiceDep = Annotated[
    DerivedFeatureService, Depends(get_derived_feature_service)
]


def get_transaction_service(session: SessionDep) -> TransactionService:
    return TransactionService(TransactionRepository(session))


TransactionServiceDep = Annotated[TransactionService, Depends(get_transaction_service)]


def get_detection_result_service(session: SessionDep) -> DetectionResultService:
    return DetectionResultService(session)


DetectionResultServiceDep = Annotated[
    DetectionResultService, Depends(get_detection_result_service)
]


def get_fraud_detection_pipeline(
    derived_feature_service: DerivedFeatureServiceDep,
    client: MLServingClientDep,
    transaction_service: TransactionServiceDep,
    detection_result_service: DetectionResultServiceDep,
) -> DFraudDetectionPipeline:
    return DFraudDetectionPipeline(
        derived_features_service=derived_feature_service,
        ml_serving_client=client,
        transaction_service=transaction_service,
        detection_result_service=detection_result_service,
    )


DFraudDetectionPipelineDep = Annotated[
    DFraudDetectionPipeline, Depends(get_fraud_detection_pipeline)
]


__all__ = ["DFraudDetectionPipelineDep", "DerivedFeatureServiceDep"]

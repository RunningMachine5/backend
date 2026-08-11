"""모든 SQLModel 엔티티를 여기서 import 해야 SQLModel.metadata 에 등록된다."""

from app.data.model.account import Account  # noqa: F401
from app.data.model.agent import (  # noqa: F401
    AgentCase,
    AgentChatMessage,
    AgentChatSession,
    AgentDashboardInsight,
    AgentExecutionStatus,
    AgentReview,
    ChatSenderType,
    ChatSessionStatus,
    FraudTypeScoreAfterChat,
)
from app.data.model.cs_guide_document import CsGuideDocument  # noqa: F401
from app.data.model.cs_guide_document_chunk import (  # noqa: F401
    CsGuideDocumentChunk,
)
from app.data.model.customer import Customer  # noqa: F401
from app.data.model.customer_event import (  # noqa: F401
    CustomerEvent,
    CustomerEventType,
)
from app.data.model.derived_features import DerivedFeatures  # noqa: F401
from app.data.model.document import Document, DocumentAudience  # noqa: F401
from app.data.model.document_chunk import DocumentChunk  # noqa: F401
from app.data.model.fraud_rule import (  # noqa: F401
    FraudRule,
    FraudRuleComponent,
    FraudRuleSet,
    FraudRuleSetStatus,
    FraudTypeScoreResult,
)
from app.data.model.ml_prediction_result import MLPredictionResult  # noqa: F401
from app.data.model.mlops import DatasetVersion, TrainingRun  # noqa: F401
from app.data.model.transaction import Transaction  # noqa: F401
from app.data.model.transaction_label import TransactionLabel  # noqa: F401

__all__ = [
    "Account",
    "AgentCase",
    "AgentChatMessage",
    "AgentChatSession",
    "AgentDashboardInsight",
    "AgentExecutionStatus",
    "AgentReview",
    "ChatSenderType",
    "ChatSessionStatus",
    "CsGuideDocument",
    "CsGuideDocumentChunk",
    "Customer",
    "CustomerEvent",
    "CustomerEventType",
    "DatasetVersion",
    "DerivedFeatures",
    "Document",
    "DocumentAudience",
    "DocumentChunk",
    "FraudRule",
    "FraudRuleComponent",
    "FraudRuleSet",
    "FraudRuleSetStatus",
    "FraudTypeScoreAfterChat",
    "FraudTypeScoreResult",
    "MLPredictionResult",
    "TrainingRun",
    "Transaction",
    "TransactionLabel",
]

# 프론트가 어떤 형식으로 최종 판정을 보내고 받는지 정의

from enum import Enum
from pydantic import BaseModel, Field, model_validator

class ReviewDecision(str, Enum):
    CONFIRMED_FRAUD = "CONFIRMED_FRAUD"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    ON_HOLD = "ON_HOLD"

class ReviewActionInput(BaseModel):
    action_code: str = Field(min_length=1, max_length=64)
    performed: bool

class ChecklistResultInput(BaseModel):
    item_code: str = Field(min_length=1, max_length=64)
    checked: bool

class CaseReviewUpsertRequest(BaseModel):
    decision: ReviewDecision

    # 사기 확정일 때만 입력
    confirmed_fraud_type: str | None = Field(
        default=None,
        max_length=64,
    )

    # 실제 수행한 조치
    performed_actions: list[ReviewActionInput] = Field(
        default_factory=list,
    )

    # 체크리스트 완료 상태
    checklist_results: list[ChecklistResultInput] = Field(
        default_factory=list,
    )

    # 담당자 최종 처리 근거
    resolution_summary: str | None = Field(
        default=None,
        max_length=2000,
    )

    @model_validator(mode="after")
    def validate_decision(self):
        if(
            self.decision == ReviewDecision.CONFIRMED_FRAUD
            and not self.confirmed_fraud_type
        ):
            raise ValueError(
                "사기 확정 시 confirmed_fraud_type은 필수입니다."
            )

        if(
            self.decision != ReviewDecision.CONFIRMED_FRAUD
            and self.confirmed_fraud_type is not None
        ):
            raise ValueError(
                "사기 확정이 아닌 경우 confirmed_fraud_type은 입력할 수 없습니다"
            )

        return self


class CaseReviewResponse(BaseModel):
    case_id: str
    reviewer_id: str
    decision: ReviewDecision
    confirmed_fraud_type: str | None = None
    performed_actions: list[ReviewActionInput] = Field(
        default_factory=list,
    )
    checklist_results: list[ChecklistResultInput] = Field(
        default_factory=list
    )
    resolution_summary: str | None=None
    reviewed_at: str
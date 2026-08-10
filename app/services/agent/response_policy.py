"""YAML로 관리하는 내부 대응 정책을 검증하고 조회한다."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.domain.response_policy import (
    RISK_GRADE_CODES,
    PolicyAction,
    PolicyChecklistItem,
    PolicyNotFoundError,
    PolicyValidationError,
    ResponsePolicy,
)


DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "agent"
    / "response_policies.yaml"
)

NonEmptyText = Annotated[StrictStr, Field(min_length=1)]
PositiveInteger = Annotated[StrictInt, Field(gt=0)]


class _PolicySchema(BaseModel):
    """YAML 정책 스키마가 공통으로 사용하는 엄격한 입력 규칙이다."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class _PolicyActionSchema(_PolicySchema):
    priority: PositiveInteger
    action_code: NonEmptyText
    action: NonEmptyText
    reason: NonEmptyText
    required: StrictBool


class _PolicyChecklistItemSchema(_PolicySchema):
    item_code: NonEmptyText
    label: NonEmptyText
    required: StrictBool


class _ResponsePolicySchema(_PolicySchema):
    policy_id: NonEmptyText
    fraud_type: NonEmptyText
    risk_grade: NonEmptyText
    notification_required: StrictBool
    notification_reason: NonEmptyText | None
    actions: tuple[_PolicyActionSchema, ...] = Field(min_length=1)
    checklist: tuple[_PolicyChecklistItemSchema, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_policy_rules(self) -> Self:
        """한 정책 안에서만 확인할 수 있는 업무 규칙을 검증한다."""

        if self.fraud_type not in FINAL_FRAUD_TYPE_CODES:
            raise ValueError(f"지원하지 않는 사기 유형이다: {self.fraud_type}")
        if self.risk_grade not in RISK_GRADE_CODES:
            raise ValueError(f"지원하지 않는 위험등급이다: {self.risk_grade}")
        if self.notification_required and self.notification_reason is None:
            raise ValueError(
                "고객 알림이 필요한 정책에는 notification_reason이 필요하다."
            )
        if not any(action.required for action in self.actions):
            raise ValueError("최소 한 개의 필수 조치가 필요하다.")
        if not any(item.required for item in self.checklist):
            raise ValueError("최소 한 개의 필수 항목이 필요하다.")

        priorities = [action.priority for action in self.actions]
        if len(priorities) != len(set(priorities)):
            raise ValueError("조치 priority가 중복되었다.")
        action_codes = [action.action_code for action in self.actions]
        if len(action_codes) != len(set(action_codes)):
            raise ValueError("조치 action_code가 중복되었다.")
        item_codes = [item.item_code for item in self.checklist]
        if len(item_codes) != len(set(item_codes)):
            raise ValueError("체크리스트 item_code가 중복되었다.")

        return self


class _PolicyDocumentSchema(_PolicySchema):
    policies: tuple[_ResponsePolicySchema, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document_rules(self) -> Self:
        """정책 문서 전체에서 ID와 조회 키의 중복을 검증한다."""

        policy_ids = [policy.policy_id for policy in self.policies]
        if len(policy_ids) != len(set(policy_ids)):
            raise ValueError("중복된 policy_id가 있다.")

        policy_keys = [
            (policy.fraud_type, policy.risk_grade) for policy in self.policies
        ]
        if len(policy_keys) != len(set(policy_keys)):
            raise ValueError("동일한 사기 유형과 위험등급의 정책이 중복되었다.")

        return self


class YamlPolicyRepository:
    """검증이 끝난 YAML 정책을 유형과 위험등급 조합으로 조회한다."""

    def __init__(self, policies: Sequence[ResponsePolicy]) -> None:
        self._policies = tuple(policies)
        if not self._policies:
            raise PolicyValidationError("최소 한 개 이상의 대응 정책이 필요하다.")

        self._by_key = {
            (policy.fraud_type, policy.risk_grade): policy
            for policy in self._policies
        }
        if len(self._by_key) != len(self._policies):
            raise PolicyValidationError(
                "동일한 사기 유형과 위험등급의 정책이 중복되었다."
            )

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_POLICY_PATH) -> YamlPolicyRepository:
        """YAML 파일을 읽고 검증된 정책 Repository를 생성한다."""

        return cls(load_response_policies(path))

    @property
    def policies(self) -> tuple[ResponsePolicy, ...]:
        """검증된 전체 정책을 변경할 수 없는 튜플로 반환한다."""

        return self._policies

    def get_response_policy(
        self,
        *,
        fraud_type: str,
        risk_grade: str,
    ) -> ResponsePolicy:
        """사기 유형과 위험등급에 정확히 일치하는 정책을 반환한다."""

        try:
            return self._by_key[(fraud_type, risk_grade)]
        except KeyError as error:
            raise PolicyNotFoundError(
                f"일치하는 대응 정책이 없다: {fraud_type}/{risk_grade}"
            ) from error


def load_response_policies(path: str | Path) -> tuple[ResponsePolicy, ...]:
    """YAML 파일을 안전하게 읽어 검증된 정책 목록으로 변환한다."""

    policy_path = Path(path)
    try:
        raw_document = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        validated = _PolicyDocumentSchema.model_validate(raw_document)
    except FileNotFoundError:
        raise
    except yaml.YAMLError as error:
        raise PolicyValidationError(
            f"대응 정책 YAML 형식이 올바르지 않다: {policy_path}"
        ) from error
    except ValidationError as error:
        # 구체적인 Pydantic 오류를 보존하여 어느 정책을 고쳐야 하는지 확인할 수 있게 한다.
        raise PolicyValidationError(
            f"대응 정책 값이 올바르지 않다: {error}"
        ) from error
    except (OSError, UnicodeError) as error:
        raise PolicyValidationError(
            f"대응 정책 파일을 읽을 수 없다: {policy_path}"
        ) from error

    return tuple(_to_domain_policy(policy) for policy in validated.policies)


def get_default_policy_repository() -> YamlPolicyRepository:
    """프로젝트 기본 정책 파일을 사용하는 Repository를 생성한다."""

    return YamlPolicyRepository.from_file(DEFAULT_POLICY_PATH)


def _to_domain_policy(policy: _ResponsePolicySchema) -> ResponsePolicy:
    """YAML 검증 모델을 Agent가 사용하는 불변 도메인 모델로 변환한다."""

    return ResponsePolicy(
        policy_id=policy.policy_id,
        fraud_type=policy.fraud_type,
        risk_grade=policy.risk_grade,
        notification_required=policy.notification_required,
        notification_reason=policy.notification_reason,
        actions=tuple(
            PolicyAction(
                priority=action.priority,
                action_code=action.action_code,
                action=action.action,
                reason=action.reason,
                required=action.required,
            )
            for action in sorted(policy.actions, key=lambda item: item.priority)
        ),
        checklist=tuple(
            PolicyChecklistItem(
                item_code=item.item_code,
                label=item.label,
                required=item.required,
            )
            for item in policy.checklist
        ),
    )


__all__ = [
    "DEFAULT_POLICY_PATH",
    "YamlPolicyRepository",
    "get_default_policy_repository",
    "load_response_policies",
]

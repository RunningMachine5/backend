"""YAML로 관리하는 내부 대응 정책을 검증하고 조회한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

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

_ROOT_FIELDS = frozenset({"policies"})
_POLICY_FIELDS = frozenset(
    {
        "policy_id",
        "fraud_type",
        "risk_grade",
        "notification_required",
        "notification_reason",
        "actions",
        "checklist",
    }
)
_ACTION_FIELDS = frozenset(
    {"priority", "action_code", "action", "reason", "required"}
)
_CHECKLIST_FIELDS = frozenset({"item_code", "label", "required"})


class YamlPolicyRepository:
    """검증이 끝난 YAML 정책을 유형과 위험등급 조합으로 조회한다."""

    def __init__(self, policies: Sequence[ResponsePolicy]) -> None:
        if isinstance(policies, (str, bytes)) or not isinstance(policies, Sequence):
            raise TypeError("policies는 ResponsePolicy 목록이어야 한다.")
        if not policies:
            raise PolicyValidationError("최소 한 개 이상의 대응 정책이 필요하다.")

        by_key: dict[tuple[str, str], ResponsePolicy] = {}
        policy_ids: set[str] = set()
        for policy in policies:
            if not isinstance(policy, ResponsePolicy):
                raise TypeError("policies의 모든 항목은 ResponsePolicy여야 한다.")
            if policy.policy_id in policy_ids:
                raise PolicyValidationError(
                    f"중복된 policy_id이다: {policy.policy_id}"
                )
            key = (policy.fraud_type, policy.risk_grade)
            if key in by_key:
                raise PolicyValidationError(
                    "동일한 사기 유형과 위험등급의 정책이 중복되었다: "
                    f"{policy.fraud_type}/{policy.risk_grade}"
                )
            policy_ids.add(policy.policy_id)
            by_key[key] = policy

        self._policies = tuple(policies)
        self._by_key = by_key

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

        validated_fraud_type = _validate_code(
            fraud_type,
            field_name="fraud_type",
        )
        validated_risk_grade = _validate_code(
            risk_grade,
            field_name="risk_grade",
        )
        try:
            return self._by_key[(validated_fraud_type, validated_risk_grade)]
        except KeyError as error:
            raise PolicyNotFoundError(
                "일치하는 대응 정책이 없다: "
                f"{validated_fraud_type}/{validated_risk_grade}"
            ) from error


def load_response_policies(path: str | Path) -> tuple[ResponsePolicy, ...]:
    """YAML 파일을 안전하게 읽어 검증된 정책 목록으로 변환한다."""

    policy_path = Path(path)
    try:
        raw_document = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError) as error:
        raise PolicyValidationError(
            f"대응 정책 파일을 읽을 수 없다: {policy_path}"
        ) from error
    except yaml.YAMLError as error:
        raise PolicyValidationError(
            f"대응 정책 YAML 형식이 올바르지 않다: {policy_path}"
        ) from error

    root = _require_mapping(raw_document, field_name="정책 문서")
    _validate_exact_fields(root, allowed=_ROOT_FIELDS, field_name="정책 문서")
    raw_policies = _require_sequence(root.get("policies"), field_name="policies")
    if not raw_policies:
        raise PolicyValidationError("policies에는 최소 한 개 이상의 정책이 필요하다.")

    policies = tuple(
        _parse_policy(raw_policy, index=index)
        for index, raw_policy in enumerate(raw_policies)
    )
    # 문서 전체에서만 확인할 수 있는 ID와 유형·등급 조합 중복을 검증한다.
    return YamlPolicyRepository(policies).policies


def get_default_policy_repository() -> YamlPolicyRepository:
    """프로젝트 기본 정책 파일을 사용하는 Repository를 생성한다."""

    return YamlPolicyRepository.from_file(DEFAULT_POLICY_PATH)


def _parse_policy(raw_policy: Any, *, index: int) -> ResponsePolicy:
    field_name = f"policies[{index}]"
    data = _require_mapping(raw_policy, field_name=field_name)
    _validate_exact_fields(data, allowed=_POLICY_FIELDS, field_name=field_name)

    policy_id = _require_text(data.get("policy_id"), field_name=f"{field_name}.policy_id")
    fraud_type = _require_text(
        data.get("fraud_type"),
        field_name=f"{field_name}.fraud_type",
    )
    if fraud_type not in FINAL_FRAUD_TYPE_CODES:
        raise PolicyValidationError(
            f"{field_name}.fraud_type에 지원하지 않는 사기 유형이 입력되었다: "
            f"{fraud_type}"
        )

    risk_grade = _require_text(
        data.get("risk_grade"),
        field_name=f"{field_name}.risk_grade",
    )
    if risk_grade not in RISK_GRADE_CODES:
        raise PolicyValidationError(
            f"{field_name}.risk_grade에 지원하지 않는 위험등급이 입력되었다: "
            f"{risk_grade}"
        )

    notification_required = _require_bool(
        data.get("notification_required"),
        field_name=f"{field_name}.notification_required",
    )
    notification_reason = _optional_text(
        data.get("notification_reason"),
        field_name=f"{field_name}.notification_reason",
    )
    if notification_required and notification_reason is None:
        raise PolicyValidationError(
            f"{field_name}은 고객 알림이 필요하므로 notification_reason이 필요하다."
        )

    actions = tuple(
        _parse_action(raw_action, field_name=f"{field_name}.actions[{action_index}]")
        for action_index, raw_action in enumerate(
            _require_sequence(data.get("actions"), field_name=f"{field_name}.actions")
        )
    )
    checklist = tuple(
        _parse_checklist_item(
            raw_item,
            field_name=f"{field_name}.checklist[{item_index}]",
        )
        for item_index, raw_item in enumerate(
            _require_sequence(
                data.get("checklist"),
                field_name=f"{field_name}.checklist",
            )
        )
    )
    _validate_actions(actions, field_name=f"{field_name}.actions")
    _validate_checklist(checklist, field_name=f"{field_name}.checklist")

    return ResponsePolicy(
        policy_id=policy_id,
        fraud_type=fraud_type,
        risk_grade=risk_grade,
        notification_required=notification_required,
        notification_reason=notification_reason,
        actions=tuple(sorted(actions, key=lambda action: action.priority)),
        checklist=checklist,
    )


def _parse_action(raw_action: Any, *, field_name: str) -> PolicyAction:
    data = _require_mapping(raw_action, field_name=field_name)
    _validate_exact_fields(data, allowed=_ACTION_FIELDS, field_name=field_name)
    priority = data.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int) or priority <= 0:
        raise PolicyValidationError(f"{field_name}.priority는 1 이상의 정수여야 한다.")

    return PolicyAction(
        priority=priority,
        action_code=_require_text(
            data.get("action_code"),
            field_name=f"{field_name}.action_code",
        ),
        action=_require_text(data.get("action"), field_name=f"{field_name}.action"),
        reason=_require_text(data.get("reason"), field_name=f"{field_name}.reason"),
        required=_require_bool(
            data.get("required"),
            field_name=f"{field_name}.required",
        ),
    )


def _parse_checklist_item(raw_item: Any, *, field_name: str) -> PolicyChecklistItem:
    data = _require_mapping(raw_item, field_name=field_name)
    _validate_exact_fields(data, allowed=_CHECKLIST_FIELDS, field_name=field_name)
    return PolicyChecklistItem(
        item_code=_require_text(
            data.get("item_code"),
            field_name=f"{field_name}.item_code",
        ),
        label=_require_text(data.get("label"), field_name=f"{field_name}.label"),
        required=_require_bool(
            data.get("required"),
            field_name=f"{field_name}.required",
        ),
    )


def _validate_actions(
    actions: tuple[PolicyAction, ...],
    *,
    field_name: str,
) -> None:
    if not actions:
        raise PolicyValidationError(f"{field_name}에는 최소 한 개의 조치가 필요하다.")
    if not any(action.required for action in actions):
        raise PolicyValidationError(f"{field_name}에는 최소 한 개의 필수 조치가 필요하다.")

    priorities = [action.priority for action in actions]
    if len(priorities) != len(set(priorities)):
        raise PolicyValidationError(f"{field_name}의 priority가 중복되었다.")
    action_codes = [action.action_code for action in actions]
    if len(action_codes) != len(set(action_codes)):
        raise PolicyValidationError(f"{field_name}의 action_code가 중복되었다.")


def _validate_checklist(
    checklist: tuple[PolicyChecklistItem, ...],
    *,
    field_name: str,
) -> None:
    if not checklist:
        raise PolicyValidationError(f"{field_name}에는 최소 한 개의 항목이 필요하다.")
    if not any(item.required for item in checklist):
        raise PolicyValidationError(f"{field_name}에는 최소 한 개의 필수 항목이 필요하다.")

    item_codes = [item.item_code for item in checklist]
    if len(item_codes) != len(set(item_codes)):
        raise PolicyValidationError(f"{field_name}의 item_code가 중복되었다.")


def _validate_exact_fields(
    data: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    field_name: str,
) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise PolicyValidationError(
            f"{field_name}에 알 수 없는 필드가 있다: {', '.join(sorted(unknown))}"
        )
    missing = allowed - set(data)
    if missing:
        raise PolicyValidationError(
            f"{field_name}에 필수 필드가 없다: {', '.join(sorted(missing))}"
        )


def _require_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyValidationError(f"{field_name}은 객체 형식이어야 한다.")
    if not all(isinstance(key, str) for key in value):
        raise PolicyValidationError(f"{field_name}의 모든 필드명은 문자열이어야 한다.")
    return value


def _require_sequence(value: Any, *, field_name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PolicyValidationError(f"{field_name}은 목록 형식이어야 한다.")
    return value


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyValidationError(f"{field_name}은 비어 있지 않은 문자열이어야 한다.")
    return value.strip()


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name=field_name)


def _require_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyValidationError(f"{field_name}은 true 또는 false여야 한다.")
    return value


def _validate_code(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}은 비어 있지 않은 문자열이어야 한다.")
    return value.strip()


__all__ = [
    "DEFAULT_POLICY_PATH",
    "YamlPolicyRepository",
    "get_default_policy_repository",
    "load_response_policies",
]

import tempfile
import textwrap
import unittest
from pathlib import Path

from app.domain.fraud_type_codes import ACCOUNT_TAKEOVER, VOICE_PHISHING
from app.domain.response_policy import (
    PolicyNotFoundError,
    PolicyValidationError,
)
from app.services.agent.response_policy import (
    DEFAULT_POLICY_PATH,
    YamlPolicyRepository,
    get_default_policy_repository,
    load_response_policies,
)


VALID_POLICY = """
policies:
  - policy_id: POLICY-ACCOUNT-TAKEOVER-VH
    fraud_type: ACCOUNT_TAKEOVER
    risk_grade: VERY_HIGH
    notification_required: true
    notification_reason: 고객 확인이 필요함
    actions:
      - priority: 2
        action_code: GUIDE_SECURITY_CHECK
        action: 보안 점검 절차 안내
        reason: 추가 계정탈취 방지
        required: true
      - priority: 1
        action_code: VERIFY_CUSTOMER_TRANSACTION
        action: 고객에게 본인 거래 여부 확인
        reason: 거래 진위 확인
        required: true
    checklist:
      - item_code: CHECK_REMOTE_CONTROL_APP
        label: 원격제어 앱 설치 여부 확인
        required: true
"""


class PolicyFileTestCase(unittest.TestCase):
    def write_policy(self, content: str) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "policies.yaml"
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path


class ResponsePolicyLoadingTest(PolicyFileTestCase):
    def test_loads_policy_and_sorts_actions_by_priority(self) -> None:
        policies = load_response_policies(self.write_policy(VALID_POLICY))

        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0].policy_id, "POLICY-ACCOUNT-TAKEOVER-VH")
        self.assertEqual(
            [action.priority for action in policies[0].actions],
            [1, 2],
        )
        self.assertTrue(policies[0].notification_required)

    def test_default_policy_file_covers_all_type_and_grade_combinations(self) -> None:
        repository = get_default_policy_repository()

        self.assertTrue(DEFAULT_POLICY_PATH.is_file())
        self.assertEqual(len(repository.policies), 16)
        self.assertEqual(
            repository.get_response_policy(
                fraud_type=ACCOUNT_TAKEOVER,
                risk_grade="VERY_HIGH",
            ).policy_id,
            "POLICY-ACCOUNT-TAKEOVER-VERY-HIGH",
        )
        self.assertEqual(
            repository.get_response_policy(
                fraud_type=VOICE_PHISHING,
                risk_grade="LOW",
            ).policy_id,
            "POLICY-VOICE-PHISHING-LOW",
        )

    def test_missing_file_is_reported(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_response_policies("missing-policy-file.yaml")

    def test_malformed_yaml_is_rejected(self) -> None:
        with self.assertRaisesRegex(PolicyValidationError, "YAML 형식"):
            load_response_policies(self.write_policy("policies: ["))


class ResponsePolicyLookupTest(PolicyFileTestCase):
    def test_returns_policy_for_exact_type_and_grade(self) -> None:
        repository = YamlPolicyRepository.from_file(self.write_policy(VALID_POLICY))

        result = repository.get_response_policy(
            fraud_type=ACCOUNT_TAKEOVER,
            risk_grade="VERY_HIGH",
        )

        self.assertEqual(result.policy_id, "POLICY-ACCOUNT-TAKEOVER-VH")

    def test_missing_policy_raises_domain_error(self) -> None:
        repository = YamlPolicyRepository.from_file(self.write_policy(VALID_POLICY))

        with self.assertRaisesRegex(PolicyNotFoundError, "일치하는 대응 정책"):
            repository.get_response_policy(
                fraud_type=VOICE_PHISHING,
                risk_grade="HIGH",
            )


class ResponsePolicyValidationTest(PolicyFileTestCase):
    def assert_invalid(self, content: str, message: str) -> None:
        with self.assertRaisesRegex(PolicyValidationError, message):
            load_response_policies(self.write_policy(content))

    def test_duplicate_policy_id_is_rejected(self) -> None:
        duplicate = VALID_POLICY + VALID_POLICY.replace("policies:\n", "", 1).replace(
            "risk_grade: VERY_HIGH",
            "risk_grade: HIGH",
            1,
        )
        self.assert_invalid(duplicate, "중복된 policy_id")

    def test_duplicate_type_and_grade_is_rejected(self) -> None:
        duplicate = VALID_POLICY + VALID_POLICY.replace("policies:\n", "", 1).replace(
            "POLICY-ACCOUNT-TAKEOVER-VH",
            "POLICY-ACCOUNT-TAKEOVER-VH-2",
            1,
        )
        self.assert_invalid(duplicate, "유형과 위험등급의 정책이 중복")

    def test_unknown_fraud_type_is_rejected(self) -> None:
        self.assert_invalid(
            VALID_POLICY.replace("ACCOUNT_TAKEOVER", "UNKNOWN_FRAUD", 1),
            "지원하지 않는 사기 유형",
        )

    def test_unknown_risk_grade_is_rejected(self) -> None:
        self.assert_invalid(
            VALID_POLICY.replace("VERY_HIGH", "CRITICAL", 1),
            "지원하지 않는 위험등급",
        )

    def test_duplicate_action_code_is_rejected(self) -> None:
        self.assert_invalid(
            VALID_POLICY.replace("GUIDE_SECURITY_CHECK", "VERIFY_CUSTOMER_TRANSACTION"),
            "action_code가 중복",
        )

    def test_duplicate_priority_is_rejected(self) -> None:
        self.assert_invalid(
            VALID_POLICY.replace("priority: 2", "priority: 1"),
            "priority가 중복",
        )

    def test_at_least_one_required_action_is_required(self) -> None:
        self.assert_invalid(
            VALID_POLICY.replace(
                "        required: true",
                "        required: false",
                2,
            ),
            "최소 한 개의 필수 조치",
        )

    def test_duplicate_checklist_code_is_rejected(self) -> None:
        policy = VALID_POLICY.replace(
            "    checklist:\n",
            "    checklist:\n"
            "      - item_code: CHECK_REMOTE_CONTROL_APP\n"
            "        label: 추가 확인 항목\n"
            "        required: true\n",
        )
        self.assert_invalid(policy, "item_code가 중복")

    def test_at_least_one_required_checklist_item_is_required(self) -> None:
        self.assert_invalid(
            VALID_POLICY.rsplit("required: true", 1)[0]
            + "required: false"
            + VALID_POLICY.rsplit("required: true", 1)[1],
            "최소 한 개의 필수 항목",
        )

    def test_notification_reason_is_required_when_notification_is_enabled(self) -> None:
        self.assert_invalid(
            VALID_POLICY.replace("notification_reason: 고객 확인이 필요함", "notification_reason:"),
            "notification_reason이 필요",
        )

    def test_unknown_field_is_rejected(self) -> None:
        self.assert_invalid(
            VALID_POLICY.replace(
                "    risk_grade: VERY_HIGH",
                "    risk_grade: VERY_HIGH\n    unexpected: value",
            ),
            "대응 정책 값이 올바르지 않다",
        )

    def test_boolean_must_not_be_integer(self) -> None:
        self.assert_invalid(
            VALID_POLICY.replace("notification_required: true", "notification_required: 1"),
            "대응 정책 값이 올바르지 않다",
        )


if __name__ == "__main__":
    unittest.main()

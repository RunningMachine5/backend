import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from app.repositories.dashboard_insight import DashboardInsightRepository
from app.services.dashboard.case_query_service import CaseQueryService


def _transaction() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        customer_id=3,
        source_account_number="source-0001",
        recipient_account_number="recipient-0001",
        transaction_datetime=datetime(2026, 8, 18, tzinfo=UTC),
        created_at=datetime(2026, 8, 21, 10, 7, tzinfo=UTC),
        transaction_amount=100_000,
        channel="mobile",
        access_medium="a",
        operating_system="android",
        ip_address="203.0.113.10",
        mac_address="00:1A:2B:3C:4D:5E",
        location_lat=37.5665,
        location_lon=126.978,
        num_connection_failure=0,
        rooting_jailbreak_indicator=False,
        mobile_roaming_indicator=False,
        vpn_indicator=True,
        flag_terminal_malicious_behavior_1=False,
        flag_terminal_malicious_behavior_2=False,
        flag_terminal_malicious_behavior_3=False,
        flag_terminal_malicious_behavior_5=False,
        flag_terminal_malicious_behavior_6=False,
    )


class DashboardQueryContractTest(unittest.TestCase):
    def test_list_cases_forwards_numeric_transaction_and_customer_ids(self) -> None:
        repository = Mock()
        repository.list_suspicious_cases.return_value = ([], 0)

        CaseQueryService(repository).list_cases(transaction_id=7, customer_id=3)

        repository.list_suspicious_cases.assert_called_once_with(
            transaction_id=7,
            period_start=None,
            period_end=None,
            customer_id=3,
            ip_address=None,
            recipient_account_number=None,
            min_amount=None,
            max_amount=None,
            risk_grades=None,
            sort_by="transaction_datetime",
            offset=0,
            limit=50,
        )

    def test_detail_uses_current_transaction_field_names(self) -> None:
        section = CaseQueryService(Mock())._to_transaction_section(_transaction())

        self.assertEqual(section.data.customer_id, 3)
        self.assertEqual(section.data.source_account_number, "source-0001")
        self.assertEqual(section.data.recipient_account_number, "recipient-0001")

    def test_insight_reads_account_owner_flag_from_derived_features(self) -> None:
        derived = SimpleNamespace(
            another_person_account=True,
            unused_terminal_status=False,
            unused_account_status=False,
            flag_deposit_more_than_ten_million=False,
            number_of_transaction_with_the_account=1,
            flag_change_of_authentication_1=False,
            flag_change_of_authentication_2=False,
            flag_change_of_authentication_3=False,
            flag_change_of_authentication_4=False,
            recipient_account_suspend_status=False,
        )
        score_result = SimpleNamespace(
            primary_fraud_type="VOICE_PHISHING",
            type_scores={"VOICE_PHISHING": 0.8},
            matched_components={"VOICE_PHISHING": ["remote_control"]},
        )

        record = DashboardInsightRepository._to_source_record(
            transaction=_transaction(),
            score_result=score_result,
            agent_case=None,
            derived=derived,
        )

        self.assertEqual(record.case_id, "7")
        self.assertTrue(record.transaction_features["another_person_account"])


if __name__ == "__main__":
    unittest.main()

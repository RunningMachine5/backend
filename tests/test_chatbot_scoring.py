import unittest

from app.domain.fraud_circumstance_codes import (
    CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,
    HIJACKED_ACCOUNT_SENDING_MESSAGES,
    INSTITUTION_IMPERSONATION_CALL_CHAIN,
    OFFICIAL_CALL_INTERCEPTED,
    SERVICE_CREDENTIALS_AND_2FA_CAPTURED,
)
from app.domain.fraud_type_codes import (
    ACCOUNT_TAKEOVER,
    FINAL_FRAUD_TYPE_CODES,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)
from app.services.chatbot.chat_scoring import score_chat_fraud_circumstances


class TestScoreChatFraudCircumstances(unittest.TestCase):
    def test_no_circumstance_keeps_all_four_types_at_zero(self) -> None:
        type_scores = score_chat_fraud_circumstances([])

        self.assertEqual(set(type_scores), set(FINAL_FRAUD_TYPE_CODES))
        self.assertEqual(set(type_scores.values()), {0})

    def test_scores_accumulate_across_types(self) -> None:
        # official_call_intercepted 는 한 정황이 두 유형에 점수를 준다.
        type_scores = score_chat_fraud_circumstances(
            [
                INSTITUTION_IMPERSONATION_CALL_CHAIN,  # 보이스피싱 4
                OFFICIAL_CALL_INTERCEPTED,  # 보이스피싱 4 + 계정탈취 1
            ]
        )

        self.assertEqual(
            type_scores,
            {
                VOICE_PHISHING: 8,
                MESSENGER_PHISHING: 0,
                ACCOUNT_TAKEOVER: 1,
                FRAUD_USED_ACCOUNT: 0,
            },
        )

    def test_scores_of_one_circumstance_split_across_two_types(self) -> None:
        # hijacked_account_sending_messages: 메신저피싱 2 + 계정탈취 4
        type_scores = score_chat_fraud_circumstances(
            [HIJACKED_ACCOUNT_SENDING_MESSAGES]
        )

        self.assertEqual(type_scores[MESSENGER_PHISHING], 2)
        self.assertEqual(type_scores[ACCOUNT_TAKEOVER], 4)
        self.assertEqual(type_scores[FRAUD_USED_ACCOUNT], 0)

    def test_repeated_types_sum_instead_of_overwriting(self) -> None:
        type_scores = score_chat_fraud_circumstances(
            [
                INSTITUTION_IMPERSONATION_CALL_CHAIN,  # 보이스피싱 4
                CRIMINAL_INVOLVEMENT_CLAIM_BY_PHONE,  # 보이스피싱 3
                SERVICE_CREDENTIALS_AND_2FA_CAPTURED,  # 계정탈취 4
            ]
        )

        self.assertEqual(type_scores[VOICE_PHISHING], 7)
        self.assertEqual(type_scores[ACCOUNT_TAKEOVER], 4)

    def test_unknown_circumstance_code_is_skipped_with_warning(self) -> None:
        with self.assertLogs(
            "app.services.chatbot.chat_scoring",
            level="WARNING",
        ) as captured:
            type_scores = score_chat_fraud_circumstances(
                [INSTITUTION_IMPERSONATION_CALL_CHAIN, "not_a_circumstance"]
            )

        self.assertEqual(type_scores[VOICE_PHISHING], 4)
        self.assertEqual(set(type_scores.values()), {0, 4})
        self.assertIn("not_a_circumstance", captured.output[0])


if __name__ == "__main__":
    unittest.main()

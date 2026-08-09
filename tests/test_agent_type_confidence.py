import math
import unittest

from app.domain.fraud_type_codes import (
    ACCOUNT_TAKEOVER,
    FINAL_FRAUD_TYPE_CODES,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
    get_fraud_type_display_name,
)
from app.services.agent.type_confidence import (
    ClassificationStatus,
    TypeConfidenceThresholds,
    calculate_type_confidence,
)


class FraudTypeCodeTest(unittest.TestCase):
    def test_final_four_type_codes_and_display_names(self) -> None:
        self.assertEqual(
            FINAL_FRAUD_TYPE_CODES,
            {
                VOICE_PHISHING,
                MESSENGER_PHISHING,
                ACCOUNT_TAKEOVER,
                FRAUD_USED_ACCOUNT,
            },
        )
        self.assertEqual(get_fraud_type_display_name(VOICE_PHISHING), "보이스피싱")
        self.assertEqual(
            get_fraud_type_display_name(MESSENGER_PHISHING),
            "메신저피싱",
        )
        self.assertEqual(get_fraud_type_display_name(ACCOUNT_TAKEOVER), "계정탈취")
        self.assertEqual(
            get_fraud_type_display_name(FRAUD_USED_ACCOUNT),
            "사기이용계좌",
        )

    def test_unknown_type_uses_code_as_display_name(self) -> None:
        self.assertEqual(
            get_fraud_type_display_name("NEW_FRAUD_TYPE"),
            "NEW_FRAUD_TYPE",
        )


class TypeConfidenceTest(unittest.TestCase):
    def test_confident_when_top_score_and_margin_meet_thresholds(self) -> None:
        result = calculate_type_confidence(
            {
                VOICE_PHISHING: 0.75,
                MESSENGER_PHISHING: 0.30,
                ACCOUNT_TAKEOVER: 0.20,
                FRAUD_USED_ACCOUNT: 0.10,
            }
        )

        self.assertEqual(result.top_type_code, VOICE_PHISHING)
        self.assertEqual(result.second_type_code, MESSENGER_PHISHING)
        self.assertEqual(result.score_margin, 0.45)
        self.assertEqual(
            result.classification_status,
            ClassificationStatus.CONFIDENT,
        )

    def test_ambiguous_when_margin_is_below_threshold(self) -> None:
        result = calculate_type_confidence(
            {
                VOICE_PHISHING: 0.20,
                MESSENGER_PHISHING: 0.55,
                ACCOUNT_TAKEOVER: 0.60,
                FRAUD_USED_ACCOUNT: 0.10,
            }
        )

        self.assertEqual(result.top_type_code, ACCOUNT_TAKEOVER)
        self.assertEqual(result.second_type_code, MESSENGER_PHISHING)
        self.assertEqual(result.score_margin, 0.05)
        self.assertEqual(
            result.classification_status,
            ClassificationStatus.AMBIGUOUS,
        )

    def test_ambiguous_when_top_score_is_below_threshold(self) -> None:
        result = calculate_type_confidence(
            {
                VOICE_PHISHING: 0.30,
                MESSENGER_PHISHING: 0.25,
                ACCOUNT_TAKEOVER: 0.20,
                FRAUD_USED_ACCOUNT: 0.15,
            }
        )

        self.assertEqual(result.top_type_code, VOICE_PHISHING)
        self.assertEqual(
            result.classification_status,
            ClassificationStatus.AMBIGUOUS,
        )

    def test_values_equal_to_thresholds_are_confident(self) -> None:
        result = calculate_type_confidence(
            {
                VOICE_PHISHING: 0.60,
                MESSENGER_PHISHING: 0.45,
            }
        )

        self.assertEqual(result.score_margin, 0.15)
        self.assertEqual(
            result.classification_status,
            ClassificationStatus.CONFIDENT,
        )

    def test_equal_scores_use_type_code_as_tie_breaker(self) -> None:
        result = calculate_type_confidence(
            {
                VOICE_PHISHING: 0.70,
                ACCOUNT_TAKEOVER: 0.70,
                MESSENGER_PHISHING: 0.20,
            }
        )

        self.assertEqual(result.top_type_code, ACCOUNT_TAKEOVER)
        self.assertEqual(result.second_type_code, VOICE_PHISHING)
        self.assertEqual(result.score_margin, 0.0)
        self.assertEqual(
            result.classification_status,
            ClassificationStatus.AMBIGUOUS,
        )

    def test_new_fraud_type_is_ranked_without_logic_change(self) -> None:
        result = calculate_type_confidence(
            {
                VOICE_PHISHING: 0.20,
                "NEW_FRAUD_TYPE": 0.90,
                ACCOUNT_TAKEOVER: 0.30,
            }
        )

        self.assertEqual(result.top_type_code, "NEW_FRAUD_TYPE")
        self.assertEqual(
            result.classification_status,
            ClassificationStatus.CONFIDENT,
        )

    def test_custom_thresholds_are_applied(self) -> None:
        result = calculate_type_confidence(
            {
                VOICE_PHISHING: 0.55,
                MESSENGER_PHISHING: 0.40,
            },
            thresholds=TypeConfidenceThresholds(
                minimum_top_score=0.50,
                minimum_margin=0.10,
            ),
        )

        self.assertEqual(
            result.classification_status,
            ClassificationStatus.CONFIDENT,
        )

    def test_at_least_two_scores_are_required(self) -> None:
        for scores in ({}, {VOICE_PHISHING: 0.8}):
            with self.subTest(scores=scores):
                with self.assertRaisesRegex(ValueError, "최소 두 개"):
                    calculate_type_confidence(scores)

    def test_invalid_type_code_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "비어 있지 않은 문자열"):
            calculate_type_confidence(
                {
                    "": 0.8,
                    VOICE_PHISHING: 0.2,
                }
            )

    def test_non_numeric_and_boolean_scores_are_rejected(self) -> None:
        invalid_scores = ("0.7", True)
        for invalid_score in invalid_scores:
            with self.subTest(invalid_score=invalid_score):
                with self.assertRaisesRegex(TypeError, "숫자"):
                    calculate_type_confidence(
                        {
                            VOICE_PHISHING: invalid_score,
                            ACCOUNT_TAKEOVER: 0.2,
                        }
                    )

    def test_out_of_range_scores_are_rejected(self) -> None:
        for invalid_score in (-0.01, 1.01):
            with self.subTest(invalid_score=invalid_score):
                with self.assertRaisesRegex(ValueError, "0.0~1.0"):
                    calculate_type_confidence(
                        {
                            VOICE_PHISHING: invalid_score,
                            ACCOUNT_TAKEOVER: 0.2,
                        }
                    )

    def test_non_finite_scores_are_rejected(self) -> None:
        for invalid_score in (math.nan, math.inf, -math.inf):
            with self.subTest(invalid_score=invalid_score):
                with self.assertRaisesRegex(ValueError, "유한한 숫자"):
                    calculate_type_confidence(
                        {
                            VOICE_PHISHING: invalid_score,
                            ACCOUNT_TAKEOVER: 0.2,
                        }
                    )

    def test_invalid_thresholds_are_rejected(self) -> None:
        for field_name, kwargs in (
            ("minimum_top_score", {"minimum_top_score": 1.01}),
            ("minimum_margin", {"minimum_margin": -0.01}),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    TypeConfidenceThresholds(**kwargs)


if __name__ == "__main__":
    unittest.main()

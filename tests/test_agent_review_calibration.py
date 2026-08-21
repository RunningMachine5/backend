import unittest

from app.domain.fraud_type_codes import (
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)
from app.services.agent.review_calibration import (
    CalibrationCandidate,
    ReviewedCase,
    evaluate_review_calibration,
)


def reviewed_case(
    case_id: str,
    *,
    confirmed_fraud_type: str,
    type_scores: dict[str, float],
    decision: str = "CONFIRMED_FRAUD",
) -> ReviewedCase:
    return ReviewedCase(
        case_id=case_id,
        type_scores=type_scores,
        matched_components={confirmed_fraud_type: ["same_evidence"]},
        risk_score=80,
        risk_grade="HIGH",
        decision=decision,
        confirmed_fraud_type=confirmed_fraud_type,
    )


class ReviewCalibrationTest(unittest.TestCase):
    def test_compares_confidence_and_similarity_candidates(self) -> None:
        scores = {
            VOICE_PHISHING: 0.10,
            MESSENGER_PHISHING: 0.10,
            ACCOUNT_TAKEOVER: 0.70,
            FRAUD_USED_ACCOUNT: 0.10,
        }
        cases = [
            reviewed_case("CASE-1", confirmed_fraud_type=ACCOUNT_TAKEOVER, type_scores=scores),
            reviewed_case("CASE-2", confirmed_fraud_type=ACCOUNT_TAKEOVER, type_scores=scores),
            reviewed_case(
                "CASE-3",
                confirmed_fraud_type=VOICE_PHISHING,
                type_scores={
                    VOICE_PHISHING: 0.55,
                    MESSENGER_PHISHING: 0.50,
                    ACCOUNT_TAKEOVER: 0.10,
                    FRAUD_USED_ACCOUNT: 0.00,
                },
            ),
        ]
        candidate = CalibrationCandidate("baseline", 0.60, 0.15, 0.60)

        result = evaluate_review_calibration(cases, candidates=(candidate,))[0]

        self.assertEqual(result.reviewed_case_count, 3)
        self.assertEqual(result.confirmed_case_count, 3)
        self.assertEqual(result.ambiguous_rate, 0.3333)
        self.assertEqual(result.confident_type_agreement_rate, 1.0)
        self.assertEqual(result.unsafe_confident_count, 0)
        self.assertEqual(result.unnecessary_investigation_rate, 0.3333)
        self.assertEqual(result.similarity_coverage_rate, 0.6667)
        self.assertEqual(result.similarity_precision_at_1, 1.0)

    def test_ignores_reviews_without_confirmed_fraud_type_for_type_metrics(self) -> None:
        cases = [
            reviewed_case(
                "CASE-FALSE-POSITIVE",
                confirmed_fraud_type=VOICE_PHISHING,
                decision="FALSE_POSITIVE",
                type_scores={
                    VOICE_PHISHING: 0.80,
                    MESSENGER_PHISHING: 0.10,
                    ACCOUNT_TAKEOVER: 0.10,
                    FRAUD_USED_ACCOUNT: 0.00,
                },
            )
        ]
        candidate = CalibrationCandidate("baseline", 0.60, 0.15, 0.60)

        result = evaluate_review_calibration(cases, candidates=(candidate,))[0]

        self.assertEqual(result.reviewed_case_count, 1)
        self.assertEqual(result.confirmed_case_count, 0)
        self.assertIsNone(result.ambiguous_rate)
        self.assertIsNone(result.similarity_coverage_rate)


if __name__ == "__main__":
    unittest.main()

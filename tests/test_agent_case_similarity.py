import math
import unittest

from app.domain.fraud_type_codes import (
    ACCOUNT_TAKEOVER,
    FRAUD_USED_ACCOUNT,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)
from app.services.agent.case_similarity import (
    CaseSimilarityConfig,
    CaseSimilarityFeatures,
    CaseSimilarityWeights,
    calculate_case_similarity,
    calculate_evidence_similarity,
    calculate_risk_grade_similarity,
    calculate_risk_score_similarity,
    calculate_score_vector_similarity,
    normalize_evidence_codes,
    rank_similar_cases,
)


TYPE_SCORES = {
    VOICE_PHISHING: 0.10,
    MESSENGER_PHISHING: 0.20,
    ACCOUNT_TAKEOVER: 0.70,
    FRAUD_USED_ACCOUNT: 0.00,
}


def make_case(
    case_id: str,
    *,
    type_scores: dict[str, float] | None = None,
    matched_components: dict[str, list[str]] | None = None,
    risk_score: float = 80.0,
    risk_grade: str = "VERY_HIGH",
) -> CaseSimilarityFeatures:
    return CaseSimilarityFeatures(
        case_id=case_id,
        type_scores=TYPE_SCORES if type_scores is None else type_scores,
        matched_components=(
            {
                ACCOUNT_TAKEOVER: ["remote_control", "authentication_changed"],
            }
            if matched_components is None
            else matched_components
        ),
        risk_score=risk_score,
        risk_grade=risk_grade,
    )


class SimilarityComponentTest(unittest.TestCase):
    def test_evidence_codes_include_fraud_type(self) -> None:
        result = normalize_evidence_codes(
            {
                ACCOUNT_TAKEOVER: ["remote_control"],
                MESSENGER_PHISHING: ["remote_control"],
            }
        )

        self.assertEqual(
            result,
            {
                f"{ACCOUNT_TAKEOVER}:remote_control",
                f"{MESSENGER_PHISHING}:remote_control",
            },
        )

    def test_evidence_similarity_uses_jaccard_index(self) -> None:
        self.assertAlmostEqual(
            calculate_evidence_similarity(
                frozenset({"A:x", "A:y"}),
                frozenset({"A:y", "A:z"}),
            ),
            1 / 3,
        )

    def test_empty_evidence_has_zero_similarity(self) -> None:
        self.assertEqual(
            calculate_evidence_similarity(frozenset(), frozenset()),
            0.0,
        )

    def test_identical_score_vectors_have_full_similarity(self) -> None:
        self.assertEqual(
            calculate_score_vector_similarity(TYPE_SCORES, TYPE_SCORES),
            1.0,
        )

    def test_zero_score_vector_has_zero_similarity(self) -> None:
        zero_scores = {type_code: 0.0 for type_code in TYPE_SCORES}
        self.assertEqual(
            calculate_score_vector_similarity(zero_scores, zero_scores),
            0.0,
        )

    def test_different_score_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "점수 항목이 동일"):
            calculate_score_vector_similarity(
                TYPE_SCORES,
                {VOICE_PHISHING: 0.5, ACCOUNT_TAKEOVER: 0.5},
            )

    def test_risk_grade_requires_exact_match(self) -> None:
        self.assertEqual(calculate_risk_grade_similarity("HIGH", "HIGH"), 1.0)
        self.assertEqual(calculate_risk_grade_similarity("HIGH", "MEDIUM"), 0.0)

    def test_risk_score_similarity_uses_normalized_distance(self) -> None:
        self.assertEqual(calculate_risk_score_similarity(80, 60), 0.8)
        self.assertEqual(calculate_risk_score_similarity(0, 100), 0.0)


class CaseSimilarityTest(unittest.TestCase):
    def test_weighted_similarity_exposes_component_scores(self) -> None:
        current = make_case("CASE-CURRENT")
        candidate = make_case(
            "CASE-001",
            matched_components={ACCOUNT_TAKEOVER: ["remote_control"]},
            risk_score=70,
        )

        result = calculate_case_similarity(current, candidate)

        self.assertEqual(result.case_id, "CASE-001")
        self.assertEqual(result.evidence_similarity, 0.5)
        self.assertEqual(result.score_vector_similarity, 1.0)
        self.assertEqual(result.risk_grade_similarity, 1.0)
        self.assertEqual(result.risk_score_similarity, 0.9)
        self.assertEqual(result.similarity_score, 0.74)
        self.assertEqual(
            result.common_evidence_codes,
            (f"{ACCOUNT_TAKEOVER}:remote_control",),
        )

    def test_custom_weights_are_applied(self) -> None:
        config = CaseSimilarityConfig(
            weights=CaseSimilarityWeights(
                evidence=1.0,
                score_vector=0.0,
                risk_grade=0.0,
                risk_score=0.0,
            )
        )
        result = calculate_case_similarity(
            make_case("CASE-CURRENT"),
            make_case(
                "CASE-001",
                matched_components={ACCOUNT_TAKEOVER: ["remote_control"]},
            ),
            config=config,
        )

        self.assertEqual(result.similarity_score, 0.5)

    def test_other_fraud_type_evidence_does_not_penalize_candidate(self) -> None:
        current = make_case(
            "CASE-CURRENT",
            type_scores={
                VOICE_PHISHING: 0.50,
                MESSENGER_PHISHING: 0.15,
                ACCOUNT_TAKEOVER: 0.70,
                FRAUD_USED_ACCOUNT: 0.00,
            },
            matched_components={
                VOICE_PHISHING: [
                    "loan_escalation_context",
                    "severe_amount_context",
                    "recipient_transfer_with_severe_amount",
                ],
                MESSENGER_PHISHING: ["vulnerable_mobile_recipient_transfer"],
                ACCOUNT_TAKEOVER: [
                    "unused_terminal_with_device_compromise",
                    "device_compromise_2plus",
                    "impossible_travel",
                    "vpn_or_roaming_with_impossible_travel",
                    "connection_failures",
                ],
            },
            risk_score=70,
            risk_grade="HIGH",
        )
        candidate = make_case(
            "CASE-ACCOUNT-HIGH",
            type_scores={
                VOICE_PHISHING: 0.10,
                MESSENGER_PHISHING: 0.10,
                ACCOUNT_TAKEOVER: 0.63,
                FRAUD_USED_ACCOUNT: 0.55,
            },
            matched_components={
                ACCOUNT_TAKEOVER: [
                    "remote_control",
                    "impossible_travel",
                    "vpn_or_roaming_with_impossible_travel",
                    "connection_failures",
                ]
            },
            risk_score=76,
            risk_grade="HIGH",
        )

        result = calculate_case_similarity(current, candidate)

        self.assertEqual(result.evidence_similarity, 0.5)
        self.assertGreaterEqual(result.similarity_score, 0.60)
        self.assertEqual(
            result.common_evidence_codes,
            (
                f"{ACCOUNT_TAKEOVER}:connection_failures",
                f"{ACCOUNT_TAKEOVER}:impossible_travel",
                f"{ACCOUNT_TAKEOVER}:vpn_or_roaming_with_impossible_travel",
            ),
        )

    def test_same_case_is_rejected_for_direct_comparison(self) -> None:
        with self.assertRaisesRegex(ValueError, "동일한 사건"):
            calculate_case_similarity(
                make_case("CASE-001"),
                make_case("CASE-001"),
            )

    def test_rule_evidence_type_must_exist_in_score_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "유형 점수 항목에 포함"):
            calculate_case_similarity(
                make_case("CASE-CURRENT"),
                make_case(
                    "CASE-001",
                    matched_components={"UNKNOWN_TYPE": ["unknown_evidence"]},
                ),
            )


class SimilarCaseRankingTest(unittest.TestCase):
    def test_ranking_filters_current_case_threshold_and_top_k(self) -> None:
        current = make_case("CASE-CURRENT")
        candidates = [
            make_case("CASE-CURRENT"),
            make_case("CASE-HIGH"),
            make_case(
                "CASE-MEDIUM",
                matched_components={ACCOUNT_TAKEOVER: ["remote_control"]},
                risk_score=70,
            ),
            make_case(
                "CASE-LOW",
                type_scores={
                    VOICE_PHISHING: 0.80,
                    MESSENGER_PHISHING: 0.10,
                    ACCOUNT_TAKEOVER: 0.05,
                    FRAUD_USED_ACCOUNT: 0.05,
                },
                matched_components={VOICE_PHISHING: ["loan_related"]},
                risk_score=20,
                risk_grade="LOW",
            ),
        ]

        results = rank_similar_cases(
            current,
            candidates,
            top_k=2,
            config=CaseSimilarityConfig(minimum_similarity=0.60),
        )

        self.assertEqual(
            [result.case_id for result in results],
            ["CASE-HIGH", "CASE-MEDIUM"],
        )

    def test_equal_scores_use_case_id_as_tie_breaker(self) -> None:
        results = rank_similar_cases(
            make_case("CASE-CURRENT"),
            [make_case("CASE-B"), make_case("CASE-A")],
        )

        self.assertEqual(
            [result.case_id for result in results],
            ["CASE-A", "CASE-B"],
        )

    def test_no_candidate_above_threshold_returns_empty_list(self) -> None:
        results = rank_similar_cases(
            make_case("CASE-CURRENT"),
            [
                make_case(
                    "CASE-LOW",
                    matched_components={VOICE_PHISHING: ["loan_related"]},
                    risk_grade="LOW",
                    risk_score=0,
                )
            ],
            config=CaseSimilarityConfig(minimum_similarity=0.99),
        )

        self.assertEqual(results, [])

    def test_invalid_top_k_is_rejected(self) -> None:
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "1 이상"):
                    rank_similar_cases(make_case("CASE-CURRENT"), [], top_k=value)


class SimilarityValidationTest(unittest.TestCase):
    def test_weights_must_sum_to_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "합은 1.0"):
            CaseSimilarityWeights(evidence=0.4)

    def test_invalid_scores_are_rejected(self) -> None:
        for invalid_score in (-0.01, 1.01, math.nan, True):
            with self.subTest(invalid_score=invalid_score):
                expected_exception = TypeError if invalid_score is True else ValueError
                with self.assertRaises(expected_exception):
                    calculate_case_similarity(
                        make_case("CASE-CURRENT"),
                        make_case(
                            "CASE-001",
                            type_scores={
                                **TYPE_SCORES,
                                ACCOUNT_TAKEOVER: invalid_score,
                            },
                        ),
                    )

    def test_invalid_risk_score_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "0.0~100.0"):
            calculate_case_similarity(
                make_case("CASE-CURRENT"),
                make_case("CASE-001", risk_score=101),
            )

    def test_invalid_evidence_collection_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "근거 코드 목록"):
            normalize_evidence_codes({ACCOUNT_TAKEOVER: "remote_control"})


if __name__ == "__main__":
    unittest.main()

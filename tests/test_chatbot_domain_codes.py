import re
import unittest
from pathlib import Path

from app.domain.customer_action_codes import (
    CUSTOMER_ACTION_DESCRIPTIONS,
    CUSTOMER_ACTION_SEARCH_QUERIES,
    FINAL_CUSTOMER_ACTION_CODES,
)
from app.domain.fraud_circumstance_codes import (
    FINAL_FRAUD_CIRCUMSTANCE_CODES,
    FRAUD_CIRCUMSTANCE_DESCRIPTIONS,
    FRAUD_CIRCUMSTANCE_SCORES,
)
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCORING_DOCUMENT = REPOSITORY_ROOT / "docs/customer-chatbot/scoring.md"
SCHEMA_DOCUMENT = REPOSITORY_ROOT / "docs/customer-chatbot/schema.md"
SCORE_ROW_PATTERN = re.compile(
    r"^\| `(?P<code>[a-z0-9_]+)` "
    r"\| (?P<voice>\d+) "
    r"\| (?P<messenger>\d+) "
    r"\| (?P<takeover>\d+) "
    r"\| (?P<used_account>\d+) \|$"
)
SEARCH_QUERY_ROW_PATTERN = re.compile(
    r"^\| `(?P<code>[a-z0-9_]+)` \| (?P<query>.+) \|$"
)


class TestCustomerActionCodes(unittest.TestCase):
    def test_has_exactly_nineteen_codes(self) -> None:
        self.assertEqual(len(FINAL_CUSTOMER_ACTION_CODES), 19)
        self.assertEqual(
            set(CUSTOMER_ACTION_DESCRIPTIONS),
            FINAL_CUSTOMER_ACTION_CODES,
        )

    def test_every_code_has_a_korean_search_query(self) -> None:
        self.assertEqual(
            set(CUSTOMER_ACTION_SEARCH_QUERIES),
            FINAL_CUSTOMER_ACTION_CODES,
        )
        for query in CUSTOMER_ACTION_SEARCH_QUERIES.values():
            self.assertTrue(query.endswith("대응 방법"))
            self.assertRegex(query, r"[가-힣]")

    def test_search_queries_match_the_documented_table(self) -> None:
        documented_queries: dict[str, str] = {}
        for line in SCHEMA_DOCUMENT.read_text(encoding="utf-8").splitlines():
            match = SEARCH_QUERY_ROW_PATTERN.fullmatch(line)
            if match is None:
                continue
            code = match.group("code")
            if code in FINAL_CUSTOMER_ACTION_CODES:
                documented_queries[code] = match.group("query")

        self.assertEqual(dict(CUSTOMER_ACTION_SEARCH_QUERIES), documented_queries)


class TestFraudCircumstanceCodes(unittest.TestCase):
    def test_has_exactly_twenty_codes(self) -> None:
        self.assertEqual(len(FINAL_FRAUD_CIRCUMSTANCE_CODES), 20)
        self.assertEqual(
            set(FRAUD_CIRCUMSTANCE_DESCRIPTIONS),
            FINAL_FRAUD_CIRCUMSTANCE_CODES,
        )
        self.assertEqual(
            set(FRAUD_CIRCUMSTANCE_SCORES),
            FINAL_FRAUD_CIRCUMSTANCE_CODES,
        )

    def test_every_score_row_contains_all_fraud_types(self) -> None:
        for scores in FRAUD_CIRCUMSTANCE_SCORES.values():
            self.assertEqual(set(scores), FINAL_FRAUD_TYPE_CODES)

    def test_scores_match_the_documented_table(self) -> None:
        documented_scores: dict[str, tuple[int, int, int, int]] = {}
        for line in SCORING_DOCUMENT.read_text(encoding="utf-8").splitlines():
            match = SCORE_ROW_PATTERN.fullmatch(line)
            if match is None:
                continue
            documented_scores[match.group("code")] = (
                int(match.group("voice")),
                int(match.group("messenger")),
                int(match.group("takeover")),
                int(match.group("used_account")),
            )

        actual_scores = {
            code: tuple(scores[fraud_type] for fraud_type in (
                "VOICE_PHISHING",
                "MESSENGER_PHISHING",
                "ACCOUNT_TAKEOVER",
                "FRAUD_USED_ACCOUNT",
            ))
            for code, scores in FRAUD_CIRCUMSTANCE_SCORES.items()
        }
        self.assertEqual(actual_scores, documented_scores)


if __name__ == "__main__":
    unittest.main()

"""테스트 데이터 시드 스크립트의 인자 계약과 조립 결과 검증.

DB 는 부르지 않는다. ``build_seed`` 는 SQLModel 인스턴스를 만들기만 하므로
세션 없이 검증할 수 있다. 세션 생성 자체는
[test_chat_session_creator.py](test_chat_session_creator.py)가 검증한다.
"""

import random
import unittest
from datetime import UTC, datetime

from app.domain.fraud_type_codes import (
    FINAL_FRAUD_TYPE_CODES,
    MESSENGER_PHISHING,
    VOICE_PHISHING,
)
from app.services.chatbot.session_creator import OLDER_CUSTOMER_AGE
from scripts.seed_chat_session import (
    ACCOUNT_ID_PREFIX,
    CUSTOMER_ID_PREFIX,
    IDENTIFICATION_NUMBER_PREFIX,
    build_seed,
    conflicting_seeding_options,
    parse_args,
)


NOW = datetime(2026, 8, 17, tzinfo=UTC)


def seed(argv: list[str], *, rng_seed: int = 0):
    return build_seed(parse_args(argv), rng=random.Random(rng_seed), now=NOW)


class ParseArgsTest(unittest.TestCase):
    def test_defaults(self) -> None:
        args = parse_args([])

        self.assertIsNone(args.seed)
        self.assertIsNone(args.top_fraud_types)
        self.assertIsNone(args.email)
        self.assertIsNone(args.amount)
        self.assertFalse(args.no_fraud_types)
        self.assertFalse(args.older)
        self.assertFalse(args.younger)
        self.assertFalse(args.cleanup)
        self.assertFalse(args.yes)

    def test_rejects_unknown_fraud_type(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--top-fraud-types", VOICE_PHISHING, "NOT_A_CODE"])

    def test_older_and_younger_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--older", "--younger"])


class CleanupOptionTest(unittest.TestCase):
    """``--cleanup`` 은 삭제 전용 모드라 생성 옵션과 섞이면 안 된다."""

    def test_cleanup_alone_has_no_conflict(self) -> None:
        self.assertEqual(conflicting_seeding_options(parse_args(["--cleanup"])), [])
        self.assertEqual(
            conflicting_seeding_options(parse_args(["--cleanup", "--yes"])),
            [],
        )

    def test_reports_every_seeding_option_given_with_cleanup(self) -> None:
        args = parse_args(
            [
                "--cleanup",
                "--seed",
                "1",
                "--older",
                "--no-fraud-types",
                "--email",
                "a@b.com",
                "--amount",
                "10000",
            ]
        )

        self.assertEqual(
            conflicting_seeding_options(args),
            ["--seed", "--no-fraud-types", "--older", "--email", "--amount"],
        )

    def test_zero_and_empty_values_still_count_as_given(self) -> None:
        # --seed 0 은 falsy 하지만 사용자가 준 값이다.
        args = parse_args(["--cleanup", "--seed", "0", "--email", ""])

        self.assertEqual(conflicting_seeding_options(args), ["--seed", "--email"])


class SeededIdentifierPrefixTest(unittest.TestCase):
    """``--cleanup`` 은 접두어로만 찾으므로 조립 결과가 접두어를 지켜야 한다."""

    def test_identifiers_use_cleanup_prefixes(self) -> None:
        result = seed([])

        self.assertTrue(result.customer.id.startswith(CUSTOMER_ID_PREFIX))
        self.assertTrue(
            result.customer.identification_number.startswith(
                IDENTIFICATION_NUMBER_PREFIX
            )
        )
        self.assertTrue(result.source_account.id.startswith(ACCOUNT_ID_PREFIX))
        self.assertTrue(result.recipient_account.id.startswith(ACCOUNT_ID_PREFIX))
        # 거래는 customer_id 로 찾으므로 시드 고객에 붙어 있어야 한다.
        self.assertTrue(result.transaction.customer_id.startswith(CUSTOMER_ID_PREFIX))


class BuildSeedTest(unittest.TestCase):
    def test_transaction_links_seeded_customer_and_accounts(self) -> None:
        result = seed([])

        self.assertEqual(result.transaction.customer_id, result.customer.id)
        self.assertEqual(
            result.transaction.source_account_number,
            result.source_account.account_number,
        )
        self.assertEqual(
            result.transaction.recipient_account_number,
            result.recipient_account.account_number,
        )
        self.assertEqual(result.source_account.customer_id, result.customer.id)
        # 수취 계좌는 외부에서 처음 관측되는 계좌라 고객을 붙이지 않는다.
        self.assertIsNone(result.recipient_account.customer_id)

    def test_withdrawal_amount_is_negative_and_balance_stays_nonnegative(self) -> None:
        result = seed(["--amount", "1234000"])

        self.assertEqual(result.transaction.transaction_amount, -1_234_000)
        self.assertGreaterEqual(result.transaction.initial_balance, 0)
        self.assertGreaterEqual(result.transaction.balance, 0)

    def test_older_flag_puts_birth_year_at_or_before_boundary(self) -> None:
        result = seed(["--older"])

        age = NOW.year - result.customer.birth_date.year
        self.assertGreaterEqual(age, OLDER_CUSTOMER_AGE)

    def test_younger_flag_puts_birth_year_after_boundary(self) -> None:
        result = seed(["--younger"])

        age = NOW.year - result.customer.birth_date.year
        self.assertLess(age, OLDER_CUSTOMER_AGE)

    def test_top_fraud_types_default_to_two_distinct_codes(self) -> None:
        result = seed([])

        self.assertEqual(len(result.top_fraud_types), 2)
        self.assertNotEqual(result.top_fraud_types[0], result.top_fraud_types[1])
        for code in result.top_fraud_types:
            self.assertIn(code, FINAL_FRAUD_TYPE_CODES)

    def test_explicit_top_fraud_types_are_kept(self) -> None:
        result = seed(["--top-fraud-types", VOICE_PHISHING, MESSENGER_PHISHING])

        self.assertEqual(
            result.top_fraud_types,
            [VOICE_PHISHING, MESSENGER_PHISHING],
        )

    def test_no_fraud_types_clears_them(self) -> None:
        result = seed(["--no-fraud-types"])

        self.assertIsNone(result.top_fraud_types)

    def test_email_defaults_to_none_for_fallback_path(self) -> None:
        self.assertIsNone(seed([]).customer.email)
        self.assertEqual(seed(["--email", "a@b.com"]).customer.email, "a@b.com")

    def test_same_seed_reproduces_the_same_values(self) -> None:
        first = seed([], rng_seed=7)
        second = seed([], rng_seed=7)

        self.assertEqual(first.customer.name, second.customer.name)
        self.assertEqual(first.customer.birth_date, second.customer.birth_date)
        self.assertEqual(
            first.transaction.transaction_amount,
            second.transaction.transaction_amount,
        )
        self.assertEqual(first.transaction.location, second.transaction.location)
        # 식별자는 시드와 무관하게 매번 새로 만든다. 같은 --seed 로 두 번 실행해도
        # PK 와 UNIQUE 컬럼이 겹치지 않아야 두 번째 실행이 살아남는다.
        self.assertNotEqual(first.customer.id, second.customer.id)
        self.assertNotEqual(
            first.customer.identification_number,
            second.customer.identification_number,
        )
        self.assertNotEqual(
            first.source_account.account_number,
            second.source_account.account_number,
        )
        self.assertNotEqual(
            first.recipient_account.account_number,
            second.recipient_account.account_number,
        )

    def test_source_and_recipient_account_numbers_differ(self) -> None:
        result = seed([])

        self.assertNotEqual(
            result.source_account.account_number,
            result.recipient_account.account_number,
        )

    def test_different_runs_vary(self) -> None:
        names = {seed([], rng_seed=index).customer.name for index in range(20)}

        self.assertGreater(len(names), 1)

    def test_generated_values_satisfy_table_check_constraints(self) -> None:
        for index in range(30):
            with self.subTest(index=index):
                result = seed([], rng_seed=index)
                transaction = result.transaction

                self.assertIn(result.customer.gender, ("male", "female"))
                self.assertIn(result.customer.credit_rating, range(1, 10))
                self.assertIn(result.customer.loan_type, tuple("abcde"))
                self.assertIn(
                    transaction.channel,
                    ("mobile", "internet", "atm", "others"),
                )
                self.assertIn(
                    transaction.type_general_automatic,
                    ("general", "automatic"),
                )
                self.assertIn(transaction.access_medium, tuple("abcdefgh"))
                self.assertGreaterEqual(transaction.num_connection_failure, 0)
                self.assertGreaterEqual(
                    transaction.remaining_amount_daily_limit_exceeded, 0
                )
                # 한국 좌표 범위(ML 계약)를 벗어나지 않는다.
                self.assertTrue(33 <= transaction.location_lat <= 39)
                self.assertTrue(124 <= transaction.location_lon <= 132)


if __name__ == "__main__":
    unittest.main()

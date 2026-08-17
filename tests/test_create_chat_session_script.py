"""로컬 테스트용 세션 생성 스크립트의 인자 계약 검증.

DB·SMTP 는 부르지 않는다. 세션 생성 자체는
[test_chat_session_creator.py](test_chat_session_creator.py)가 검증한다.
"""

import unittest

from pydantic import ValidationError

from app.domain.fraud_type_codes import MESSENGER_PHISHING, VOICE_PHISHING
from scripts.create_chat_session import build_request, parse_args


class ParseArgsTest(unittest.TestCase):
    def test_transaction_id_only(self) -> None:
        args = parse_args(["42"])

        self.assertEqual(args.transaction_id, 42)
        self.assertIsNone(args.top_fraud_types)
        self.assertFalse(args.recreate)

    def test_flags(self) -> None:
        args = parse_args(
            [
                "42",
                "--top-fraud-types",
                VOICE_PHISHING,
                MESSENGER_PHISHING,
                "--recreate",
            ]
        )

        self.assertEqual(
            args.top_fraud_types,
            [VOICE_PHISHING, MESSENGER_PHISHING],
        )
        self.assertTrue(args.recreate)


class BuildRequestTest(unittest.TestCase):
    def test_builds_create_chat_request(self) -> None:
        request = build_request(
            parse_args(
                ["7", "--top-fraud-types", VOICE_PHISHING, MESSENGER_PHISHING]
            )
        )

        self.assertEqual(request.transaction_id, 7)
        self.assertEqual(
            request.top_fraud_types,
            [VOICE_PHISHING, MESSENGER_PHISHING],
        )

    def test_rejects_duplicate_top_fraud_types(self) -> None:
        """DTO 검증을 그대로 쓰므로 같은 유형 두 번은 거부된다(PRD 2.4)."""

        with self.assertRaises(ValidationError):
            build_request(
                parse_args(
                    ["7", "--top-fraud-types", VOICE_PHISHING, VOICE_PHISHING]
                )
            )

    def test_rejects_unknown_fraud_type_code(self) -> None:
        with self.assertRaises(ValidationError):
            build_request(
                parse_args(["7", "--top-fraud-types", "NOPE", VOICE_PHISHING])
            )

if __name__ == "__main__":
    unittest.main()

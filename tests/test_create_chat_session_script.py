"""로컬 테스트용 세션 생성 스크립트의 인자 계약 검증.

DB·SMTP 는 부르지 않는다. 세션 생성 자체는
[test_chat_session_creator.py](test_chat_session_creator.py)가 검증한다.
"""

import logging
import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.domain.fraud_type_codes import MESSENGER_PHISHING, VOICE_PHISHING
from scripts.create_chat_session import (
    LoggingOnlyNotifier,
    build_request,
    parse_args,
)


class ParseArgsTest(unittest.TestCase):
    def test_transaction_id_only(self) -> None:
        args = parse_args(["42"])

        self.assertEqual(args.transaction_id, 42)
        self.assertIsNone(args.top_fraud_types)
        self.assertFalse(args.no_email)
        self.assertFalse(args.recreate)

    def test_flags(self) -> None:
        args = parse_args(
            [
                "42",
                "--top-fraud-types",
                VOICE_PHISHING,
                MESSENGER_PHISHING,
                "--no-email",
                "--recreate",
            ]
        )

        self.assertEqual(
            args.top_fraud_types,
            [VOICE_PHISHING, MESSENGER_PHISHING],
        )
        self.assertTrue(args.no_email)
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


class LoggingOnlyNotifierTest(unittest.TestCase):
    def test_logs_url_without_sending(self) -> None:
        notifier = LoggingOnlyNotifier()

        with self.assertLogs(
            "scripts.create_chat_session", level=logging.INFO
        ) as captured:
            notifier.send(
                chat_session_id="CHAT-TEST",
                recipient_email="hong@example.com",
                customer_name="홍길동",
                transaction_datetime=datetime(2026, 8, 16, tzinfo=UTC),
                transaction_amount=-1_000_000,
                used_fallback_email=False,
            )

        self.assertIn("CHAT-TEST", captured.output[0])
        self.assertIn("/chat/CHAT-TEST", captured.output[0])


if __name__ == "__main__":
    unittest.main()

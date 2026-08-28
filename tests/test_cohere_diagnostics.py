import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.services.rag.cohere_diagnostics import diagnose_cohere_reranker


class CohereDiagnosticsTest(unittest.TestCase):
    def test_success_logs_metadata_without_key(self) -> None:
        client = Mock()
        client.rerank.return_value = SimpleNamespace(
            results=[SimpleNamespace(index=0, relevance_score=0.9)],
            meta=SimpleNamespace(
                billed_units=SimpleNamespace(search_units=1),
            ),
        )

        with self.assertLogs(
            "app.services.rag.cohere_diagnostics",
            level="INFO",
        ) as logs:
            succeeded = diagnose_cohere_reranker(
                api_key="secret-key",
                model="rerank-v4.0-fast",
                timeout_seconds=5,
                client=client,
            )

        self.assertTrue(succeeded)
        self.assertNotIn("secret-key", "\n".join(logs.output))
        client.rerank.assert_called_once_with(
            model="rerank-v4.0-fast",
            query="계좌 지급정지 방법",
            documents=["금융회사에 피해 사실을 신고하고 계좌 지급정지를 요청합니다."],
            top_n=1,
        )

    def test_failure_redacts_key_and_logs_original_exception(self) -> None:
        api_key = "secret-key"
        client = Mock()
        client.rerank.side_effect = RuntimeError(f"401 invalid {api_key}")

        with self.assertLogs(
            "app.services.rag.cohere_diagnostics",
            level="ERROR",
        ) as logs:
            succeeded = diagnose_cohere_reranker(
                api_key=api_key,
                model="rerank-v4.0-fast",
                timeout_seconds=5,
                client=client,
            )

        output = "\n".join(logs.output)
        self.assertFalse(succeeded)
        self.assertIn("RuntimeError", output)
        self.assertIn("***", output)
        self.assertNotIn(api_key, output)

    def test_missing_key_skips_client_call(self) -> None:
        client = Mock()

        with self.assertLogs(
            "app.services.rag.cohere_diagnostics",
            level="ERROR",
        ):
            succeeded = diagnose_cohere_reranker(
                api_key="",
                model="rerank-v4.0-fast",
                timeout_seconds=5,
                client=client,
            )

        self.assertFalse(succeeded)
        client.rerank.assert_not_called()


if __name__ == "__main__":
    unittest.main()

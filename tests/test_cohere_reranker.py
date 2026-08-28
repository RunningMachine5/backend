import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.dto.chatbot import RetrievedChatbotGuideChunkDTO
from app.services.rag.cohere_reranker import CohereRerankError, CohereReranker


def _chunk(content: str) -> RetrievedChatbotGuideChunkDTO:
    return RetrievedChatbotGuideChunkDTO(
        content=content,
        source_title="F01.pdf",
        page=1,
        distance=0.1,
    )


class CohereRerankerTest(unittest.TestCase):
    def test_sends_only_query_and_chunk_contents_and_applies_returned_order(self) -> None:
        client = Mock()
        client.rerank.return_value = SimpleNamespace(
            results=[
                SimpleNamespace(index=2, relevance_score=0.9),
                SimpleNamespace(index=0, relevance_score=0.7),
            ]
        )
        candidates = [_chunk("첫 번째"), _chunk("두 번째"), _chunk("세 번째")]
        reranker = CohereReranker(
            api_key="test-key",
            model="rerank-v4.0-fast",
            client=client,
        )

        result = reranker.rerank("검색 질의", candidates, top_k=2)

        self.assertEqual(result, [candidates[2], candidates[0]])
        client.rerank.assert_called_once_with(
            model="rerank-v4.0-fast",
            query="검색 질의",
            documents=["첫 번째", "두 번째", "세 번째"],
            top_n=2,
        )

    def test_caps_requested_top_k_to_candidate_count(self) -> None:
        client = Mock()
        client.rerank.return_value = SimpleNamespace(
            results=[SimpleNamespace(index=0, relevance_score=0.8)]
        )
        candidate = _chunk("한 개")

        result = CohereReranker(client=client).rerank(
            "검색 질의",
            [candidate],
            top_k=5,
        )

        self.assertEqual(result, [candidate])
        self.assertEqual(client.rerank.call_args.kwargs["top_n"], 1)

    def test_empty_candidates_do_not_create_or_call_client(self) -> None:
        with patch("app.services.rag.cohere_reranker.cohere.ClientV2") as client_type:
            result = CohereReranker(api_key="test-key").rerank(
                "검색 질의",
                [],
                top_k=5,
            )

        self.assertEqual(result, [])
        client_type.assert_not_called()

    def test_missing_api_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(CohereRerankError, "COHERE_API_KEY"):
            CohereReranker(api_key="").rerank(
                "검색 질의",
                [_chunk("후보")],
                top_k=1,
            )

    @patch("app.services.rag.cohere_reranker.cohere.ClientV2")
    def test_creates_sdk_client_with_timeout(self, client_type: Mock) -> None:
        client_type.return_value.rerank.return_value = SimpleNamespace(
            results=[SimpleNamespace(index=0, relevance_score=0.8)]
        )
        reranker = CohereReranker(
            api_key="secret-key",
            timeout_seconds=5,
        )

        reranker.rerank("검색 질의", [_chunk("후보")], top_k=1)

        client_type.assert_called_once_with(
            api_key="secret-key",
            timeout=5,
            client_name="fdshield-backend",
        )

    def test_non_retryable_sdk_failure_is_wrapped_without_retry(self) -> None:
        client = Mock()
        client.rerank.side_effect = ValueError("invalid request")

        with self.assertRaisesRegex(CohereRerankError, "ValueError"):
            CohereReranker(client=client, max_retries=1).rerank(
                "검색 질의",
                [_chunk("후보")],
                top_k=1,
            )

        client.rerank.assert_called_once()

    @patch("app.services.rag.cohere_reranker.time.sleep")
    def test_retryable_failure_is_retried_once(self, sleep: Mock) -> None:
        error = RuntimeError("rate limited")
        error.status_code = 429
        client = Mock()
        before_request = Mock()
        client.rerank.side_effect = [
            error,
            SimpleNamespace(
                results=[SimpleNamespace(index=0, relevance_score=0.8)]
            ),
        ]

        result = CohereReranker(
            client=client,
            max_retries=1,
            before_request=before_request,
        ).rerank(
            "검색 질의",
            [_chunk("후보")],
            top_k=1,
        )

        self.assertEqual([chunk.content for chunk in result], ["후보"])
        self.assertEqual(client.rerank.call_count, 2)
        self.assertEqual(before_request.call_count, 2)
        sleep.assert_called_once_with(0.25)

    def test_malformed_results_are_rejected(self) -> None:
        malformed_results = (
            [
                SimpleNamespace(index=0, relevance_score=0.9),
                SimpleNamespace(index=0, relevance_score=0.8),
            ],
            [SimpleNamespace(index=3, relevance_score=0.9)],
            [SimpleNamespace(index="0", relevance_score=0.9)],
            [SimpleNamespace(index=0, relevance_score=float("nan"))],
            [],
        )
        candidates = [_chunk("첫 번째"), _chunk("두 번째")]

        for results in malformed_results:
            with self.subTest(results=results):
                client = Mock()
                client.rerank.return_value = SimpleNamespace(results=results)
                with self.assertRaises(CohereRerankError):
                    CohereReranker(client=client).rerank(
                        "검색 질의",
                        candidates,
                        top_k=min(2, len(results) or 1),
                    )


if __name__ == "__main__":
    unittest.main()

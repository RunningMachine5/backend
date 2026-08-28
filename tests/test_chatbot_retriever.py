import unittest
from unittest.mock import Mock, patch

from app.dto.chatbot import RetrievedChatbotGuideChunkDTO
from app.services.rag.chatbot_retriever import (
    MAX_DISTANCE,
    _log_rerank_disabled,
    retriever_source,
)
from app.services.rag.cohere_reranker import CohereRerankError, CohereReranker


class TestChatbotRetriever(unittest.TestCase):
    @patch("app.services.rag.chatbot_retriever.query_embedding")
    def test_returns_empty_list_when_no_chunks_are_found(
        self,
        query_embedding: Mock,
    ) -> None:
        query_embedding.return_value = [0.1, 0.2]
        session = Mock()
        session.exec.return_value.all.return_value = []

        result = retriever_source("검색 질문", session)

        self.assertEqual(result, [])
        query_embedding.assert_called_once_with("검색 질문")

    @patch("app.services.rag.chatbot_retriever.query_embedding")
    def test_filters_chunks_by_max_distance_in_query(
        self,
        query_embedding: Mock,
    ) -> None:
        query_embedding.return_value = [0.1, 0.2]
        session = Mock()
        session.exec.return_value.all.return_value = [
            ("포함 문서", 3, "포함할 내용", MAX_DISTANCE),
        ]

        reranker = Mock()
        reranker.rerank.side_effect = lambda question, candidates, top_k: candidates

        result = retriever_source(
            "검색 질문",
            session,
            top_k=2,
            reranker=reranker,
        )

        self.assertEqual(
            result,
            [
                RetrievedChatbotGuideChunkDTO(
                    content="포함할 내용",
                    source_title="포함 문서",
                    page=3,
                    distance=MAX_DISTANCE,
                )
            ],
        )
        stmt = session.exec.call_args.args[0]
        self.assertIsNotNone(stmt.whereclause)
        self.assertIn("<=", str(stmt.whereclause))

    @patch("app.services.rag.chatbot_retriever.query_embedding")
    def test_retrieves_thirty_candidates_then_returns_reranked_top_k(
        self,
        query_embedding: Mock,
    ) -> None:
        query_embedding.return_value = [0.1, 0.2]
        session = Mock()
        session.exec.return_value.all.return_value = [
            ("첫 문서", 1, "첫 내용", 0.1),
            ("둘째 문서", 2, "둘째 내용", 0.2),
            ("셋째 문서", 3, "셋째 내용", 0.3),
        ]
        reranker = Mock()
        reranker.rerank.side_effect = (
            lambda question, candidates, top_k: [candidates[2], candidates[0]]
        )

        result = retriever_source(
            "검색 질문",
            session,
            top_k=2,
            reranker=reranker,
        )

        stmt = session.exec.call_args.args[0]
        self.assertEqual(stmt._limit_clause.value, 30)
        passed_candidates = reranker.rerank.call_args.args[1]
        self.assertEqual(len(passed_candidates), 3)
        self.assertEqual([chunk.content for chunk in result], ["셋째 내용", "첫 내용"])

    @patch("app.services.rag.chatbot_retriever.query_embedding")
    def test_empty_candidates_skip_reranker(
        self,
        query_embedding: Mock,
    ) -> None:
        query_embedding.return_value = [0.1, 0.2]
        session = Mock()
        session.exec.return_value.all.return_value = []
        reranker = Mock()

        result = retriever_source("검색 질문", session, reranker=reranker)

        self.assertEqual(result, [])
        reranker.rerank.assert_not_called()

    @patch("app.services.rag.chatbot_retriever.query_embedding")
    def test_reranker_failure_falls_back_to_vector_order(
        self,
        query_embedding: Mock,
    ) -> None:
        query_embedding.return_value = [0.1, 0.2]
        session = Mock()
        session.exec.return_value.all.return_value = [
            ("첫 문서", 1, "첫 내용", 0.1),
            ("둘째 문서", 2, "둘째 내용", 0.2),
            ("셋째 문서", 3, "셋째 내용", 0.3),
        ]
        reranker = Mock()
        reranker.rerank.side_effect = CohereRerankError("timeout")

        with self.assertLogs(
            "app.services.rag.chatbot_retriever",
            level="WARNING",
        ):
            result = retriever_source(
                "검색 질문",
                session,
                top_k=2,
                reranker=reranker,
            )

        self.assertEqual([chunk.content for chunk in result], ["첫 내용", "둘째 내용"])

    @patch("app.services.rag.chatbot_retriever._default_reranker")
    @patch("app.services.rag.chatbot_retriever.query_embedding")
    def test_missing_api_key_falls_back_to_vector_order(
        self,
        query_embedding: Mock,
        default_reranker: Mock,
    ) -> None:
        query_embedding.return_value = [0.1, 0.2]
        session = Mock()
        session.exec.return_value.all.return_value = [
            ("첫 문서", 1, "첫 내용", 0.1),
            ("둘째 문서", 2, "둘째 내용", 0.2),
        ]
        default_reranker.return_value = CohereReranker(api_key="")

        with self.assertLogs(
            "app.services.rag.chatbot_retriever",
            level="WARNING",
        ):
            result = retriever_source("검색 질문", session, top_k=1)

        self.assertEqual([chunk.content for chunk in result], ["첫 내용"])

    @patch("app.services.rag.chatbot_retriever.COHERE_RERANK_ENABLED", False)
    @patch("app.services.rag.chatbot_retriever.query_embedding")
    def test_disabled_reranker_uses_original_top_k(
        self,
        query_embedding: Mock,
    ) -> None:
        query_embedding.return_value = [0.1, 0.2]
        session = Mock()
        session.exec.return_value.all.return_value = [
            ("첫 문서", 1, "첫 내용", 0.1),
            ("둘째 문서", 2, "둘째 내용", 0.2),
        ]
        reranker = Mock()
        # 안내 로그는 프로세스당 한 번만 남으므로 캐시를 비우고 검증한다.
        _log_rerank_disabled.cache_clear()

        with self.assertLogs(
            "app.services.rag.chatbot_retriever",
            level="INFO",
        ):
            result = retriever_source(
                "검색 질문",
                session,
                top_k=2,
                reranker=reranker,
            )

        stmt = session.exec.call_args.args[0]
        self.assertEqual(stmt._limit_clause.value, 2)
        self.assertEqual([chunk.content for chunk in result], ["첫 내용", "둘째 내용"])
        reranker.rerank.assert_not_called()


if __name__ == "__main__":
    unittest.main()

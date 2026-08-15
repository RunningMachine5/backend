import unittest
from unittest.mock import Mock, patch

from app.dto.chatbot import RetrievedChatbotGuideChunkDTO
from app.services.chatbot.customer_chatbot import _render_retrieved_context
from app.services.rag.chatbot_retriever import MAX_DISTANCE, retriever_source


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

        result = retriever_source("검색 질문", session, top_k=2)

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


class TestRetrievedContextRendering(unittest.TestCase):
    def test_renders_empty_context_without_fallback_sentence(self) -> None:
        self.assertEqual(_render_retrieved_context([]), "")

    def test_renders_source_title_page_and_content(self) -> None:
        context = _render_retrieved_context(
            [
                RetrievedChatbotGuideChunkDTO(
                    content="대응 가이드 내용",
                    source_title="금융사기 피해 예방 안내서",
                    page=7,
                    distance=0.2,
                )
            ]
        )

        self.assertEqual(
            context,
            "[출처: 금융사기 피해 예방 안내서 7p]\n대응 가이드 내용",
        )
        self.assertNotIn("관련 문서를 찾지 못했습니다.", context)


if __name__ == "__main__":
    unittest.main()

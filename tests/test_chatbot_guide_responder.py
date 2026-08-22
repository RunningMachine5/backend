import unittest

from app.dto.chatbot import (
    ExtractedGuideSearchQuery,
    RetrievedChatbotGuideChunkDTO,
)
from app.services.chatbot.guide_responder import GuideResponder
from app.services.chatbot.messages import UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE


def _chunk(content: str) -> RetrievedChatbotGuideChunkDTO:
    return RetrievedChatbotGuideChunkDTO(
        content=content,
        source_title="고객 대응 가이드",
        page=12,
        distance=0.1,
    )


class FakeRetriever:
    def __init__(
        self,
        chunks_by_query: dict[str, list[RetrievedChatbotGuideChunkDTO]],
    ) -> None:
        self.chunks_by_query = chunks_by_query
        self.queries: list[str] = []
        self.top_ks: list[int] = []

    def __call__(self, query, session, top_k=3):
        self.queries.append(query)
        self.top_ks.append(top_k)
        return list(self.chunks_by_query.get(query, []))


class FakeStreamingLLM:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.prompts: list[str] = []

    def stream(self, prompt: str):
        self.prompts.append(prompt)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        for chunk in result:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class GuideResponderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.session = object()
        self.call_query = ExtractedGuideSearchQuery(
            title="모르는 사람의 전화 수신",
            search_query="모르는 사람의 전화를 받은 경우 보안 대응 방법",
            evidence="모르는 사람한테 전화가 와서 받았어",
        )
        self.phone_query = ExtractedGuideSearchQuery(
            title="전화번호 제공",
            search_query="모르는 사람에게 전화번호를 제공한 경우 대응 방법",
            evidence="그 사람에게 전화번호를 전송해줬어",
        )


class TestRetrieveStep(GuideResponderTestCase):
    def test_uses_each_standalone_query_with_top_k_five(self) -> None:
        retriever = FakeRetriever({})
        responder = GuideResponder(
            llm=FakeStreamingLLM([]),
            retriever=retriever,
        )

        responder.respond(
            guide_search_queries=[self.call_query, self.phone_query],
            session=self.session,
        )

        self.assertEqual(
            retriever.queries,
            [self.call_query.search_query, self.phone_query.search_query],
        )
        self.assertEqual(retriever.top_ks, [5, 5])

    def test_duplicate_normalized_query_is_retrieved_once(self) -> None:
        duplicate = ExtractedGuideSearchQuery(
            title="중복",
            search_query="  모르는 사람의 전화를 받은 경우  보안 대응 방법 ",
            evidence="전화를 받았어",
        )
        retriever = FakeRetriever({})
        responder = GuideResponder(
            llm=FakeStreamingLLM([]),
            retriever=retriever,
        )

        responder.respond(
            guide_search_queries=[self.call_query, duplicate],
            session=self.session,
        )

        self.assertEqual(retriever.queries, [self.call_query.search_query])

    def test_retriever_failure_only_makes_that_query_ungrounded(self) -> None:
        def failing_retriever(query, session, top_k=3):
            raise RuntimeError("pgvector down")

        llm = FakeStreamingLLM([])
        responder = GuideResponder(
            llm=llm,
            retriever=failing_retriever,
        )

        with self.assertLogs(
            "app.services.chatbot.guide_responder",
            level="WARNING",
        ):
            response = responder.respond(
                guide_search_queries=[self.call_query],
                session=self.session,
            )

        self.assertEqual(
            response.message_text,
            f"■ {self.call_query.title}\n{UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE}",
        )
        self.assertEqual(response.ungrounded_query_positions, (1,))
        self.assertEqual(llm.prompts, [])


class TestGenerateStep(GuideResponderTestCase):
    def test_llm_is_called_once_with_all_queries_when_any_is_grounded(self) -> None:
        retriever = FakeRetriever(
            {self.call_query.search_query: [_chunk("발신자를 공식 채널로 확인하세요")]}
        )
        generated = (
            f"■ {self.call_query.title}\n공식 채널로 확인해주세요.\n\n"
            f"■ {self.phone_query.title}\n{UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE}"
        )
        llm = FakeStreamingLLM([[generated]])
        responder = GuideResponder(llm=llm, retriever=retriever)

        response = responder.respond(
            guide_search_queries=[self.call_query, self.phone_query],
            session=self.session,
        )

        self.assertEqual(len(llm.prompts), 1)
        prompt = llm.prompts[0]
        self.assertIn(self.call_query.title, prompt)
        self.assertIn(self.call_query.search_query, prompt)
        self.assertIn(self.phone_query.title, prompt)
        self.assertIn(UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE, prompt)
        self.assertIn("공식 채널로 확인해주세요.", response.message_text)
        self.assertEqual(response.grounded_query_positions, (1,))
        self.assertEqual(response.ungrounded_query_positions, (2,))

    def test_generation_retries_then_succeeds(self) -> None:
        retriever = FakeRetriever(
            {self.call_query.search_query: [_chunk("공식 채널로 확인하세요")]}
        )
        llm = FakeStreamingLLM(
            [
                TimeoutError("timeout"),
                [f"■ {self.call_query.title}\n", "확인해주세요."],
            ]
        )
        responder = GuideResponder(
            llm=llm,
            retriever=retriever,
            max_attempts=2,
        )

        response = responder.respond(
            guide_search_queries=[self.call_query],
            session=self.session,
        )

        self.assertEqual(len(llm.prompts), 2)
        self.assertIn("확인해주세요.", response.message_text)

    def test_generation_failure_uses_fixed_message(self) -> None:
        retriever = FakeRetriever(
            {self.call_query.search_query: [_chunk("공식 채널로 확인하세요")]}
        )
        llm = FakeStreamingLLM(
            [TimeoutError("timeout"), TimeoutError("timeout")]
        )
        responder = GuideResponder(
            llm=llm,
            retriever=retriever,
            max_attempts=2,
        )

        with self.assertLogs(
            "app.services.chatbot.guide_responder",
            level="WARNING",
        ):
            response = responder.respond(
                guide_search_queries=[self.call_query],
                session=self.session,
            )

        self.assertIn(
            UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE,
            response.message_text,
        )
        self.assertEqual(response.grounded_query_positions, (1,))

    def test_streams_korean_chunks_as_cumulative_snapshots(self) -> None:
        retriever = FakeRetriever(
            {self.call_query.search_query: [_chunk("공식 채널로 확인하세요")]}
        )
        llm = FakeStreamingLLM(
            [[f"■ {self.call_query.title}\n공", "식 채널", "로 확인해주세요."]]
        )
        responder = GuideResponder(llm=llm, retriever=retriever)
        snapshots: list[str] = []

        response = responder.respond(
            guide_search_queries=[self.call_query],
            session=self.session,
            on_snapshot=snapshots.append,
        )

        self.assertEqual(
            snapshots,
            [
                f"■ {self.call_query.title}\n공",
                f"■ {self.call_query.title}\n공식 채널",
                f"■ {self.call_query.title}\n공식 채널로 확인해주세요.",
            ],
        )
        self.assertEqual(snapshots[-1], response.message_text)

    def test_retry_resets_partial_snapshot_before_second_attempt(self) -> None:
        retriever = FakeRetriever(
            {self.call_query.search_query: [_chunk("공식 채널로 확인하세요")]}
        )
        llm = FakeStreamingLLM(
            [
                ["부분 안내", TimeoutError("timeout")],
                [f"■ {self.call_query.title}\n최종 안내"],
            ]
        )
        responder = GuideResponder(llm=llm, retriever=retriever, max_attempts=2)
        snapshots: list[str] = []

        response = responder.respond(
            guide_search_queries=[self.call_query],
            session=self.session,
            on_snapshot=snapshots.append,
        )

        self.assertEqual(snapshots[0], "부분 안내")
        self.assertEqual(snapshots[1], "")
        self.assertEqual(snapshots[-1], response.message_text)

    def test_empty_generation_retries_then_uses_fixed_message(self) -> None:
        retriever = FakeRetriever(
            {self.call_query.search_query: [_chunk("공식 채널로 확인하세요")]}
        )
        llm = FakeStreamingLLM([[], []])
        responder = GuideResponder(llm=llm, retriever=retriever, max_attempts=2)

        response = responder.respond(
            guide_search_queries=[self.call_query],
            session=self.session,
        )

        self.assertEqual(len(llm.prompts), 2)
        self.assertEqual(
            response.message_text,
            f"■ {self.call_query.title}\n{UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE}",
        )


class TestAssembleStep(GuideResponderTestCase):
    def test_partial_grounding_keeps_each_heading_in_order(self) -> None:
        retriever = FakeRetriever(
            {self.call_query.search_query: [_chunk("공식 채널로 확인하세요")]}
        )
        expected = (
            f"■ {self.call_query.title}\n확인해주세요.\n\n"
            f"■ {self.phone_query.title}\n"
            f"{UNGROUNDED_GUIDE_SEARCH_QUERY_MESSAGE}"
        )
        llm = FakeStreamingLLM([[expected]])
        responder = GuideResponder(llm=llm, retriever=retriever)

        response = responder.respond(
            guide_search_queries=[self.call_query, self.phone_query],
            session=self.session,
        )

        self.assertEqual(response.message_text, expected)

    def test_all_ungrounded_skips_llm_and_keeps_every_heading(self) -> None:
        llm = FakeStreamingLLM([])
        responder = GuideResponder(
            llm=llm,
            retriever=FakeRetriever({}),
        )

        response = responder.respond(
            guide_search_queries=[self.call_query, self.phone_query],
            session=self.session,
        )

        self.assertEqual(response.grounded_query_positions, ())
        self.assertEqual(response.ungrounded_query_positions, (1, 2))
        self.assertIn(f"■ {self.call_query.title}", response.message_text)
        self.assertIn(f"■ {self.phone_query.title}", response.message_text)
        self.assertEqual(llm.prompts, [])

    def test_no_guide_search_query_returns_empty_text(self) -> None:
        llm = FakeStreamingLLM([])
        responder = GuideResponder(
            llm=llm,
            retriever=FakeRetriever({}),
        )

        response = responder.respond(
            guide_search_queries=[],
            session=self.session,
        )

        self.assertEqual(response.message_text, "")
        self.assertEqual(response.ungrounded_query_positions, ())
        self.assertEqual(llm.prompts, [])


if __name__ == "__main__":
    unittest.main()

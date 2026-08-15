import unittest

from app.domain.customer_action_codes import (
    CUSTOMER_ACTION_DESCRIPTIONS,
    CUSTOMER_ACTION_SEARCH_QUERIES,
    IDENTITY_DOCUMENT_SHARED,
    OTP_OR_AUTHENTICATION_CODE_SHARED,
    PHISHING_LINK_OPENED,
)
from app.dto.chatbot import (
    ExtractedCustomerAction,
    RetrievedChatbotGuideChunkDTO,
)
from app.services.chatbot.guide_responder import GuideResponder
from app.services.chatbot.messages import UNGROUNDED_ACTION_MESSAGE


def _chunk(content: str) -> RetrievedChatbotGuideChunkDTO:
    return RetrievedChatbotGuideChunkDTO(
        content=content,
        source_title="고객 대응 가이드",
        page=12,
        distance=0.1,
    )


class FakeRetriever:
    """액션별 검색 질의를 기록하고 미리 정해둔 청크를 돌려준다."""

    def __init__(
        self,
        chunks_by_query_prefix: dict[str, list[RetrievedChatbotGuideChunkDTO]],
    ) -> None:
        self.chunks_by_query_prefix = chunks_by_query_prefix
        self.queries: list[str] = []
        self.top_ks: list[int] = []

    def __call__(self, query, session, top_k=3):
        self.queries.append(query)
        self.top_ks.append(top_k)
        for prefix, chunks in self.chunks_by_query_prefix.items():
            if query.startswith(prefix):
                return list(chunks)
        return []


class FakeStructuredLLM:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> object:
        self.prompts.append(prompt)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class GuideResponderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.session = object()  # 리트리버를 모킹하므로 DB 세션은 쓰이지 않는다
        self.link_action = ExtractedCustomerAction(
            type=PHISHING_LINK_OPENED,
            evidence="문자로 온 링크를 눌렀어요",
        )
        self.identity_action = ExtractedCustomerAction(
            type=IDENTITY_DOCUMENT_SHARED,
            evidence="신분증 사진을 보냈어요",
        )


class TestRetrieveStep(GuideResponderTestCase):
    def test_query_combines_search_mapping_and_evidence(self) -> None:
        retriever = FakeRetriever({})
        responder = GuideResponder(
            structured_llm=FakeStructuredLLM([]),
            retriever=retriever,
        )

        responder.respond(actions=[self.link_action], session=self.session)

        self.assertEqual(
            retriever.queries,
            [
                f"{CUSTOMER_ACTION_SEARCH_QUERIES[PHISHING_LINK_OPENED]}"
                f" {self.link_action.evidence}"
            ],
        )

    def test_each_action_is_retrieved_independently_with_top_k_three(
        self,
    ) -> None:
        retriever = FakeRetriever(
            {
                CUSTOMER_ACTION_SEARCH_QUERIES[PHISHING_LINK_OPENED]: [
                    _chunk("링크 대응 안내")
                ],
                CUSTOMER_ACTION_SEARCH_QUERIES[IDENTITY_DOCUMENT_SHARED]: [
                    _chunk("신분증 대응 안내")
                ],
            }
        )
        responder = GuideResponder(
            structured_llm=FakeStructuredLLM(
                [
                    {
                        "guides": [
                            {"type": PHISHING_LINK_OPENED, "guidance": "가"},
                            {"type": IDENTITY_DOCUMENT_SHARED, "guidance": "나"},
                        ]
                    }
                ]
            ),
            retriever=retriever,
        )

        responder.respond(
            actions=[self.link_action, self.identity_action],
            session=self.session,
        )

        self.assertEqual(len(retriever.queries), 2)
        self.assertEqual(retriever.top_ks, [3, 3])

    def test_duplicate_action_type_is_retrieved_once(self) -> None:
        retriever = FakeRetriever({})
        responder = GuideResponder(
            structured_llm=FakeStructuredLLM([]),
            retriever=retriever,
        )

        responder.respond(
            actions=[
                self.link_action,
                ExtractedCustomerAction(
                    type=PHISHING_LINK_OPENED,
                    evidence="링크를 또 눌렀어요",
                ),
            ],
            session=self.session,
        )

        self.assertEqual(len(retriever.queries), 1)

    def test_retriever_failure_makes_the_action_ungrounded(self) -> None:
        def failing_retriever(query, session, top_k=3):
            raise RuntimeError("pgvector down")

        llm = FakeStructuredLLM([])
        responder = GuideResponder(
            structured_llm=llm,
            retriever=failing_retriever,
        )

        with self.assertLogs(
            "app.services.chatbot.guide_responder",
            level="WARNING",
        ):
            response = responder.respond(
                actions=[self.link_action],
                session=self.session,
            )

        self.assertEqual(
            response.message_text,
            f"■ {CUSTOMER_ACTION_DESCRIPTIONS[PHISHING_LINK_OPENED]}\n"
            f"{UNGROUNDED_ACTION_MESSAGE}",
        )
        self.assertEqual(llm.prompts, [])


class TestGenerateStep(GuideResponderTestCase):
    def test_llm_is_called_once_with_grounded_actions_only(self) -> None:
        retriever = FakeRetriever(
            {
                CUSTOMER_ACTION_SEARCH_QUERIES[PHISHING_LINK_OPENED]: [
                    _chunk("링크를 누른 경우 즉시 기기를 점검하세요")
                ]
            }
        )
        llm = FakeStructuredLLM(
            [
                {
                    "guides": [
                        {
                            "type": PHISHING_LINK_OPENED,
                            "guidance": "기기를 점검해주세요.",
                        }
                    ]
                }
            ]
        )
        responder = GuideResponder(structured_llm=llm, retriever=retriever)

        responder.respond(
            actions=[self.link_action, self.identity_action],
            session=self.session,
        )

        self.assertEqual(len(llm.prompts), 1)
        prompt = llm.prompts[0]
        self.assertIn(PHISHING_LINK_OPENED, prompt)
        self.assertIn("링크를 누른 경우 즉시 기기를 점검하세요", prompt)
        # 0건 액션은 교차 오염을 막기 위해 프롬프트에 넣지 않는다.
        self.assertNotIn(IDENTITY_DOCUMENT_SHARED, prompt)
        self.assertNotIn(self.identity_action.evidence, prompt)

    def test_generation_is_retried_within_max_attempts(self) -> None:
        retriever = FakeRetriever(
            {
                CUSTOMER_ACTION_SEARCH_QUERIES[PHISHING_LINK_OPENED]: [
                    _chunk("링크 대응 안내")
                ]
            }
        )
        llm = FakeStructuredLLM(
            [
                TimeoutError("first call timed out"),
                {
                    "guides": [
                        {
                            "type": PHISHING_LINK_OPENED,
                            "guidance": "기기를 점검해주세요.",
                        }
                    ]
                },
            ]
        )
        responder = GuideResponder(
            structured_llm=llm,
            retriever=retriever,
            max_attempts=2,
        )

        response = responder.respond(
            actions=[self.link_action],
            session=self.session,
        )

        self.assertEqual(len(llm.prompts), 2)
        self.assertIn("기기를 점검해주세요.", response.message_text)

    def test_generation_failure_falls_back_to_ungrounded_message(self) -> None:
        retriever = FakeRetriever(
            {
                CUSTOMER_ACTION_SEARCH_QUERIES[PHISHING_LINK_OPENED]: [
                    _chunk("링크 대응 안내")
                ]
            }
        )
        llm = FakeStructuredLLM(
            [TimeoutError("timed out"), TimeoutError("timed out")]
        )
        responder = GuideResponder(
            structured_llm=llm,
            retriever=retriever,
            max_attempts=2,
        )

        with self.assertLogs(
            "app.services.chatbot.guide_responder",
            level="WARNING",
        ):
            response = responder.respond(
                actions=[self.link_action],
                session=self.session,
            )

        # 생성에 실패해도 상담사로 넘기지 않고 B.5 문구로 채워 상담을 계속한다.
        self.assertEqual(
            response.message_text,
            f"■ {CUSTOMER_ACTION_DESCRIPTIONS[PHISHING_LINK_OPENED]}\n"
            f"{UNGROUNDED_ACTION_MESSAGE}",
        )
        self.assertEqual(
            response.grounded_action_codes, (PHISHING_LINK_OPENED,)
        )

    def test_guidance_for_action_outside_prompt_is_dropped(self) -> None:
        retriever = FakeRetriever(
            {
                CUSTOMER_ACTION_SEARCH_QUERIES[PHISHING_LINK_OPENED]: [
                    _chunk("링크 대응 안내")
                ]
            }
        )
        llm = FakeStructuredLLM(
            [
                {
                    "guides": [
                        {
                            "type": PHISHING_LINK_OPENED,
                            "guidance": "기기를 점검해주세요.",
                        },
                        {
                            "type": OTP_OR_AUTHENTICATION_CODE_SHARED,
                            "guidance": "근거 없이 지어낸 안내",
                        },
                    ]
                }
            ]
        )
        responder = GuideResponder(structured_llm=llm, retriever=retriever)

        with self.assertLogs(
            "app.services.chatbot.guide_responder",
            level="WARNING",
        ):
            response = responder.respond(
                actions=[self.link_action],
                session=self.session,
            )

        self.assertNotIn("근거 없이 지어낸 안내", response.message_text)
        self.assertNotIn(
            CUSTOMER_ACTION_DESCRIPTIONS[OTP_OR_AUTHENTICATION_CODE_SHARED],
            response.message_text,
        )


class TestAssembleStep(GuideResponderTestCase):
    def test_ungrounded_action_keeps_heading_and_uses_fixed_message(
        self,
    ) -> None:
        retriever = FakeRetriever(
            {
                CUSTOMER_ACTION_SEARCH_QUERIES[PHISHING_LINK_OPENED]: [
                    _chunk("링크 대응 안내")
                ]
            }
        )
        llm = FakeStructuredLLM(
            [
                {
                    "guides": [
                        {
                            "type": PHISHING_LINK_OPENED,
                            "guidance": "기기를 점검해주세요.",
                        }
                    ]
                }
            ]
        )
        responder = GuideResponder(structured_llm=llm, retriever=retriever)

        response = responder.respond(
            actions=[self.link_action, self.identity_action],
            session=self.session,
        )

        expected = (
            f"■ {CUSTOMER_ACTION_DESCRIPTIONS[PHISHING_LINK_OPENED]}\n"
            "기기를 점검해주세요.\n\n"
            f"■ {CUSTOMER_ACTION_DESCRIPTIONS[IDENTITY_DOCUMENT_SHARED]}\n"
            f"{UNGROUNDED_ACTION_MESSAGE}"
        )
        self.assertEqual(response.message_text, expected)
        self.assertEqual(
            response.grounded_action_codes, (PHISHING_LINK_OPENED,)
        )
        self.assertEqual(
            response.ungrounded_action_codes, (IDENTITY_DOCUMENT_SHARED,)
        )

    def test_empty_guidance_is_replaced_with_fixed_message(self) -> None:
        retriever = FakeRetriever(
            {
                CUSTOMER_ACTION_SEARCH_QUERIES[PHISHING_LINK_OPENED]: [
                    _chunk("링크 대응 안내")
                ],
                CUSTOMER_ACTION_SEARCH_QUERIES[IDENTITY_DOCUMENT_SHARED]: [
                    _chunk("신분증 대응 안내")
                ],
            }
        )
        llm = FakeStructuredLLM(
            [
                {
                    "guides": [
                        {
                            "type": PHISHING_LINK_OPENED,
                            "guidance": "기기를 점검해주세요.",
                        },
                        {"type": IDENTITY_DOCUMENT_SHARED, "guidance": "   "},
                    ]
                }
            ]
        )
        responder = GuideResponder(structured_llm=llm, retriever=retriever)

        response = responder.respond(
            actions=[self.link_action, self.identity_action],
            session=self.session,
        )

        self.assertIn(UNGROUNDED_ACTION_MESSAGE, response.message_text)

    def test_all_actions_ungrounded_keeps_every_heading(self) -> None:
        llm = FakeStructuredLLM([])
        responder = GuideResponder(
            structured_llm=llm,
            retriever=FakeRetriever({}),
        )

        response = responder.respond(
            actions=[self.link_action, self.identity_action],
            session=self.session,
        )

        # 전체 0건이어도 상담사로 넘기지 않고 두 액션 모두 B.5 문구로 답한다.
        expected = (
            f"■ {CUSTOMER_ACTION_DESCRIPTIONS[PHISHING_LINK_OPENED]}\n"
            f"{UNGROUNDED_ACTION_MESSAGE}\n\n"
            f"■ {CUSTOMER_ACTION_DESCRIPTIONS[IDENTITY_DOCUMENT_SHARED]}\n"
            f"{UNGROUNDED_ACTION_MESSAGE}"
        )
        self.assertEqual(response.message_text, expected)
        self.assertEqual(response.grounded_action_codes, ())
        self.assertEqual(
            response.ungrounded_action_codes,
            (PHISHING_LINK_OPENED, IDENTITY_DOCUMENT_SHARED),
        )
        # 근거가 없으면 LLM 을 호출하지 않는다.
        self.assertEqual(llm.prompts, [])

    def test_no_action_returns_empty_text_without_llm_call(self) -> None:
        llm = FakeStructuredLLM([])
        responder = GuideResponder(
            structured_llm=llm,
            retriever=FakeRetriever({}),
        )

        response = responder.respond(actions=[], session=self.session)

        # 할 말이 없으면 빈 본문을 돌려주고, 파이프라인이 메시지를 보내지 않는다.
        self.assertEqual(response.message_text, "")
        self.assertEqual(response.ungrounded_action_codes, ())
        self.assertEqual(llm.prompts, [])


if __name__ == "__main__":
    unittest.main()

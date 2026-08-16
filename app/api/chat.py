"""고객 대응 챗봇 라우터.

설계는 docs/customer-chatbot/README.md 의 2.2~2.3, 2.7 이다. 라우터는 두 개다.

- ``/chat`` — 고객이 쓰는 경로(접속·본인인증, 버튼, 답변 송수신)
- ``/agent`` — 담당자 화면이 쓰는 경로(거래별 세션 상태 조회, 상태 변경 SSE)

**세션 생성은 HTTP 로 열지 않는다.** PRD 2.1 대로 FDS 파이프라인이
[session_creator.py](../services/chatbot/session_creator.py)를 함수로 호출하고, 로컬에서
접속 URL 이 필요하면 `scripts/create_chat_session.py` 를 쓴다.

트랜잭션은 이 라우터가 소유한다(``get_session`` 은 commit 하지 않는다). 턴 실행은
[customer_chatbot_pipeline.py](../pipelines/customer_chatbot_pipeline.py)가 커밋과 상태
변경 발행을 함께 처리한다.

본인인증은 출생연도 4자리 대조뿐이고 토큰을 발급하지 않는다. 실패 횟수 제한·URL 토큰·
세션 TTL 은 MVP 범위 밖이다(PRD 3.3).
"""

import json
from collections.abc import Iterator
from queue import Empty

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app.core.common_response import ApiResponse, success_response
from app.core.db import SessionDep
from app.data.model.chatbot import ChatMessage, ChatSession, ChatSessionStatus
from app.dto.chatbot import (
    ChatButtonActionRequest,
    ChatMessageResponse,
    ChatSessionDetailResponse,
    ChatTurnResponse,
    ChatVerifyRequest,
    SendChatMessageRequest,
    TransactionChatSessionStatusResponse,
)
from app.pipelines.customer_chatbot_pipeline import (
    ChatTurnRejectedError,
    ChatTurnResult,
    CustomerChatbotPipeline,
)
from app.repositories.chat_session import ChatSessionRepository
from app.services.chatbot.identity_verifier import verify_birth_year
from app.services.chatbot.session_event_broker import (
    ChatSessionEvent,
    chat_session_event_broker,
)


router = APIRouter(prefix="/chat", tags=["chat"])
agent_router = APIRouter(prefix="/agent", tags=["agent-chat-sessions"])

# SSE 연결에서 이벤트를 기다리는 시간. 넘기면 keep-alive 주석을 한 줄 보낸다.
SSE_KEEP_ALIVE_SECONDS = 15


# ----------------------------------------------------------------------
# 고객 경로
# ----------------------------------------------------------------------


@router.post(
    "/{chat_session_id}/verify",
    response_model=ApiResponse[ChatSessionDetailResponse],
)
def verify_chat_session(
    chat_session_id: str,
    payload: ChatVerifyRequest,
    session: SessionDep,
) -> ApiResponse[ChatSessionDetailResponse]:
    """출생연도 4자리로 본인을 확인하고 챗봇 화면 진입 상태를 돌려준다(PRD 2.2).

    첫 진입이면 B.1 최초 알림을 출력한다. 이미 대화가 시작된 세션은 이력만 돌려주므로
    재인증해도 알림이 다시 쌓이지 않는다.
    """

    chat_session = _require_session(session, chat_session_id)
    if not verify_birth_year(session, chat_session, payload.birth_year):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="본인인증에 실패했습니다.",
        )

    if (
        chat_session.status == ChatSessionStatus.URL_SENT.value
        and chat_session.last_message_id is None
    ):
        _run_turn(
            lambda: _pipeline(session, chat_session).send_initial_notification()
        )

    return success_response(_session_detail(session, chat_session))


@router.get(
    "/{chat_session_id}",
    response_model=ApiResponse[ChatSessionDetailResponse],
)
def get_chat_session(
    chat_session_id: str,
    session: SessionDep,
) -> ApiResponse[ChatSessionDetailResponse]:
    """세션 상태와 대화 이력을 조회한다(새로고침·재접속)."""

    chat_session = _require_session(session, chat_session_id)
    return success_response(_session_detail(session, chat_session))


@router.post(
    "/{chat_session_id}/actions",
    response_model=ApiResponse[ChatTurnResponse],
)
def send_chat_button_action(
    chat_session_id: str,
    payload: ChatButtonActionRequest,
    session: SessionDep,
) -> ApiResponse[ChatTurnResponse]:
    """최초 알림 뒤의 버튼 3종을 처리한다(PRD 2.3)."""

    chat_session = _require_session(session, chat_session_id)
    result = _run_turn(
        lambda: _pipeline(session, chat_session).handle_button(payload.action)
    )
    return success_response(_turn_response(chat_session_id, result))


@router.post(
    "/{chat_session_id}/messages",
    response_model=ApiResponse[ChatTurnResponse],
)
def send_chat_message(
    chat_session_id: str,
    payload: SendChatMessageRequest,
    session: SessionDep,
) -> ApiResponse[ChatTurnResponse]:
    """고객 답변 한 건을 평가하고 그 턴의 챗봇 응답을 돌려준다(PRD 2.4~2.6)."""

    chat_session = _require_session(session, chat_session_id)
    result = _run_turn(
        lambda: _pipeline(session, chat_session).handle_message(
            payload.message_text
        )
    )
    return success_response(_turn_response(chat_session_id, result))


# ----------------------------------------------------------------------
# 담당자 경로 (PRD 2.7)
# ----------------------------------------------------------------------


@agent_router.get(
    "/chat-sessions/events",
    # StreamingResponse 라 response_model 을 두지 않는다. 이벤트 본문은
    # ChatSessionStatusChangedEventPayload 다.
    response_model=None,
)
def stream_chat_session_events() -> StreamingResponse:
    """대시보드 연결 하나로 모든 세션의 상태 변경을 수신한다.

    전체 세션 스냅샷은 보내지 않는다. 최초 접속·재연결 시의 현재값은 거래별 상태
    조회로 복구한다.
    """

    def event_stream() -> Iterator[str]:
        subscriber_queue = chat_session_event_broker.subscribe()
        try:
            # 연결이 끊기면 3초 뒤 재연결한다.
            yield "retry: 3000\n\n"

            while True:
                try:
                    event = subscriber_queue.get(
                        timeout=SSE_KEEP_ALIVE_SECONDS
                    )
                except Empty:
                    yield ": keep-alive\n\n"
                    continue

                yield _format_sse_event(event)
        finally:
            chat_session_event_broker.unsubscribe(subscriber_queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@agent_router.get(
    "/transactions/{transaction_id}/chat-session",
    response_model=ApiResponse[TransactionChatSessionStatusResponse],
)
def get_transaction_chat_session_status(
    transaction_id: int,
    session: SessionDep,
) -> ApiResponse[TransactionChatSessionStatusResponse]:
    """거래 목록 항목 하나에 연결된 채팅 세션의 현재 상태를 조회한다.

    세션이 없는 거래도 목록에 그대로 남아야 하므로 404 가 아니라 빈 값을 돌려준다.
    """

    chat_session = ChatSessionRepository(session).find_by_transaction(
        transaction_id
    )
    if chat_session is None:
        return success_response(
            TransactionChatSessionStatusResponse(transaction_id=transaction_id)
        )

    return success_response(
        TransactionChatSessionStatusResponse(
            transaction_id=transaction_id,
            chat_session_id=chat_session.chat_session_id,
            status=chat_session.status,
        )
    )


# ----------------------------------------------------------------------
# 공통 보조
# ----------------------------------------------------------------------


def _pipeline(
    session: SessionDep,
    chat_session: ChatSession,
) -> CustomerChatbotPipeline:
    return CustomerChatbotPipeline(
        session=session,
        chat_session=chat_session,
    )


def _run_turn(run) -> ChatTurnResult:
    """턴 실행 중 거절된 입력을 409 로 옮긴다.

    커밋·롤백과 상태 변경 발행은 파이프라인이 턴 단위로 처리한다.
    """

    try:
        return run()
    except ChatTurnRejectedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


def _require_session(
    session: SessionDep,
    chat_session_id: str,
) -> ChatSession:
    chat_session = ChatSessionRepository(session).get(chat_session_id)
    if chat_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="채팅 세션을 찾을 수 없습니다.",
        )
    return chat_session


def _session_detail(
    session: SessionDep,
    chat_session: ChatSession,
) -> ChatSessionDetailResponse:
    messages = ChatSessionRepository(session).list_messages(chat_session)
    return ChatSessionDetailResponse(
        chat_session_id=chat_session.chat_session_id,
        transaction_id=chat_session.transaction_id,
        status=chat_session.status,
        is_older=chat_session.is_older,
        question_step=chat_session.question_step,
        messages=[_message_response(message) for message in messages],
    )


def _message_response(message: ChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse(
        message_id=message.message_id,
        sender_type=message.sender_type,
        message_text=message.message_text,
        sent_at=message.sent_at,
    )


def _turn_response(
    chat_session_id: str,
    result: ChatTurnResult,
) -> ChatTurnResponse:
    return ChatTurnResponse(
        chat_session_id=chat_session_id,
        status=result.status.value,
        question_step=result.question_step,
        messages=list(result.messages),
    )


def _format_sse_event(event: ChatSessionEvent) -> str:
    data = json.dumps(event.payload.model_dump(), ensure_ascii=False)
    return f"event: {event.event}\ndata: {data}\n\n"

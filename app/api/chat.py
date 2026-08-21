"""고객용 채팅 API와 담당자용 상담 조회 API."""

import json
import logging
from collections.abc import Callable, Iterator
from queue import Empty
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, status
from fastapi.responses import StreamingResponse

from app.core.common_response import ApiResponse, success_response
from app.core.db import SessionDep
from app.data.model.chatbot import (
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
)
from app.dto.chatbot import (
    ChatButtonActionRequest,
    ChatMessageResponse,
    ChatSessionDetailResponse,
    ChatTurnResponse,
    ChatVerifyRequest,
    SendChatMessageRequest,
    TransactionChatSessionDetailResponse,
    TransactionChatSessionStatusResponse,
)
from app.pipelines.customer_chatbot_pipeline import (
    ChatTurnRejectedError,
    ChatTurnResult,
    ChatTurnStreamEvent,
    CustomerChatbotPipeline,
)
from app.repositories.chat_session import ChatSessionRepository
from app.services.chatbot.chat_score_event_broker import chat_score_event_broker
from app.services.chatbot.chat_score_publisher import (
    build_type_score_responses,
    publish_chat_score_update,
)
from app.services.chatbot.fraud_circumstance_task_runner import (
    FraudCircumstanceTaskRunnerDep,
)
from app.services.chatbot.identity_verifier import verify_birth_year


router = APIRouter(prefix="/chat", tags=["chat"])
transaction_chat_router = APIRouter(
    prefix="/transactions", tags=["chat-sessions"]
)
logger = logging.getLogger(__name__)
T = TypeVar("T")
SSE_RESPONSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ----------------------------------------------------------------------
# Swagger 표기 (경로 파라미터·오류 응답)
# ----------------------------------------------------------------------

ChatSessionIdPath = Annotated[
    str,
    Path(
        description=(
            "FDS 파이프라인이 발급한 채팅 세션 id. 고객에게 보낸 접속 URL 에 들어 있다."
        ),
        examples=["CHAT-20260816-A1B2C3D4"],
    ),
]

TransactionIdPath = Annotated[
    int,
    Path(
        description="채팅 세션이 연결된 거래 id(``transactions.id``).",
        examples=[1024],
    ),
]


def _error_response(
    description: str,
    *,
    status_code: int,
    message: str,
) -> dict[str, Any]:
    """실패 응답 하나를 Swagger 예시로 만든다.

    본문은 ``register_exception_handlers`` 가 공통 ``ApiResponse`` 봉투로 감싸며,
    ``error.code`` 는 ``HTTP_<상태코드>`` 다. 실제 응답의 ``data``/``details`` 는
    ``null`` 이지만 FastAPI 가 스키마를 만들 때 null 값을 버리므로 예시에는 넣지 않는다.
    """

    return {
        "model": ApiResponse[None],
        "description": description,
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "error": {
                        "code": f"HTTP_{status_code}",
                        "message": message,
                    },
                }
            }
        },
    }


_SESSION_NOT_FOUND_RESPONSE = _error_response(
    "존재하지 않는 채팅 세션 id",
    status_code=status.HTTP_404_NOT_FOUND,
    message="채팅 세션을 찾을 수 없습니다.",
)


# ----------------------------------------------------------------------
# 고객 경로
# ----------------------------------------------------------------------


@router.post(
    "/{chat_session_id}/verify",
    response_model=ApiResponse[ChatSessionDetailResponse],
    summary="본인인증 후 챗봇 첫 진입",
    responses={
        status.HTTP_401_UNAUTHORIZED: _error_response(
            "출생연도가 일치하지 않음",
            status_code=status.HTTP_401_UNAUTHORIZED,
            message="본인인증에 실패했습니다.",
        ),
        status.HTTP_404_NOT_FOUND: _SESSION_NOT_FOUND_RESPONSE,
    },
)
def verify_chat_session(
    chat_session_id: ChatSessionIdPath,
    payload: ChatVerifyRequest,
    session: SessionDep,
) -> ApiResponse[ChatSessionDetailResponse]:
    """출생연도를 확인하고 최초 알림을 포함한 세션 상태를 반환한다."""

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
    summary="세션 상태·대화 이력 조회(새로고침·재접속)",
    responses={status.HTTP_404_NOT_FOUND: _SESSION_NOT_FOUND_RESPONSE},
)
def get_chat_session(
    chat_session_id: ChatSessionIdPath,
    session: SessionDep,
) -> ApiResponse[ChatSessionDetailResponse]:
    """새로고침과 재접속에 필요한 세션 상태와 대화 이력을 조회한다."""

    chat_session = _require_session(session, chat_session_id)
    return success_response(_session_detail(session, chat_session))


@router.post(
    "/{chat_session_id}/actions",
    response_model=ApiResponse[ChatTurnResponse],
    summary="최초 알림 뒤 버튼 선택",
    responses={
        status.HTTP_404_NOT_FOUND: _SESSION_NOT_FOUND_RESPONSE,
        status.HTTP_409_CONFLICT: _error_response(
            "현재 세션 상태에서 받을 수 없는 입력",
            status_code=status.HTTP_409_CONFLICT,
            message="버튼 선택은 URL_SENT 상태에서만 처리할 수 있습니다",
        ),
    },
)
def send_chat_button_action(
    chat_session_id: ChatSessionIdPath,
    payload: ChatButtonActionRequest,
    session: SessionDep,
) -> ApiResponse[ChatTurnResponse]:
    """최초 알림 화면의 상담 시작·상담사 연결·종료 액션을 처리한다."""

    chat_session = _require_session(session, chat_session_id)
    result = _run_turn(
        lambda: _pipeline(session, chat_session).handle_button(payload.action)
    )
    return success_response(_turn_response(chat_session_id, result))


@router.post(
    "/{chat_session_id}/messages",
    summary="고객 답변 전송(SSE)",
    responses={
        status.HTTP_200_OK: {
            "description": "챗봇 턴 이벤트 스트림",
            "content": {
                "text/event-stream": {
                    "example": (
                        "event: chat_turn_started\n"
                        'data: {"chat_session_id":"CHAT-20260816-A1B2C3D4"}\n\n'
                        "event: chat_message_snapshot\n"
                        'data: {"message_index":0,"message_text":"■ 안내\\n공식"}\n\n'
                        "event: chat_turn_completed\n"
                        'data: {"chat_session_id":"CHAT-20260816-A1B2C3D4",'
                        '"status":"IN_PROGRESS","question_step":2,'
                        '"messages":["■ 안내\\n공식 금융회사에 확인해 주세요.",'
                        '"다음 질문"]}\n\n'
                    )
                }
            },
        },
        status.HTTP_404_NOT_FOUND: _SESSION_NOT_FOUND_RESPONSE,
        status.HTTP_409_CONFLICT: _error_response(
            "상담 중이 아니거나 답변을 기다리는 질문이 없음",
            status_code=status.HTTP_409_CONFLICT,
            message="고객 답변은 IN_PROGRESS 상태에서만 처리할 수 있습니다",
        ),
    },
)
def send_chat_message(
    chat_session_id: ChatSessionIdPath,
    payload: SendChatMessageRequest,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    fraud_circumstance_task_runner: FraudCircumstanceTaskRunnerDep,
) -> StreamingResponse:
    """고객 답변을 처리하고 가이드 스냅샷과 최종 결과를 SSE로 반환한다.

    사기 정황 추출은 턴 커밋 후 백그라운드에서 실행한다.
    """

    chat_session = _require_session(session, chat_session_id)
    transaction_id = chat_session.transaction_id
    # handle_message_stream은 iterator를 만들기 전에 상태를 검증한다. 409는 SSE 응답
    # 헤더를 보내기 전 기존 ApiResponse JSON으로 유지된다.
    stream = _run_turn(
        lambda: _pipeline(session, chat_session).handle_message_stream(
            payload.message_text,
        )
    )

    def event_stream() -> Iterator[str]:
        yield _sse_event(
            "chat_turn_started",
            {"chat_session_id": chat_session_id},
        )

        try:
            for item in stream:
                if isinstance(item, ChatTurnStreamEvent):
                    yield _sse_event(item.event, item.data)
                    continue

                # 이 지점에는 턴 커밋과 세션 refresh가 끝나 있다. 아직 이번 답변의
                # 정황이 반영되기 전 점수를 발행하고, 추출은 응답 종료 뒤 예약한다.
                publish_chat_score_update(session, transaction_id)
                if item.pending_extraction is not None:
                    background_tasks.add_task(
                        fraud_circumstance_task_runner,
                        item.pending_extraction,
                    )
                yield _sse_event(
                    "chat_turn_completed",
                    _turn_response(chat_session_id, item).model_dump(mode="json"),
                )
        except Exception:
            # 파이프라인이 아직 커밋 전이라면 자체적으로 rollback한다. 응답 헤더가 이미
            # 전송됐으므로 HTTP 상태를 바꾸는 대신 명시적인 오류 이벤트로 끝낸다.
            logger.exception(
                "챗봇 턴 스트림 처리에 실패했습니다: session=%s",
                chat_session_id,
            )
            yield _sse_event(
                "chat_turn_error",
                {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "서버 내부 오류가 발생했습니다.",
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
        background=background_tasks,
    )


# ----------------------------------------------------------------------
# 담당자 경로
# ----------------------------------------------------------------------


@transaction_chat_router.get(
    "/{transaction_id}/chat-session",
    response_model=ApiResponse[TransactionChatSessionStatusResponse],
    summary="거래별 채팅 세션 상태 조회",
)
def get_transaction_chat_session_status(
    transaction_id: TransactionIdPath,
    session: SessionDep,
) -> ApiResponse[TransactionChatSessionStatusResponse]:
    """거래에 연결된 채팅 세션의 상태를 조회한다."""

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


@transaction_chat_router.get("/{transaction_id}/chat-session/score-events")
def stream_transaction_chat_score_events(
    transaction_id: TransactionIdPath,
) -> StreamingResponse:
    """거래의 사기유형 점수 변경을 SSE로 전송한다.

    세션 상태는 포함하지 않으며 이벤트가 없을 때는 keep-alive를 보낸다.
    """

    def event_stream() -> Iterator[str]:
        subscriber_queue = chat_score_event_broker.subscribe(transaction_id)

        try:
            # 브라우저가 SSE 연결이 끊겼을 때 3초 후 재연결
            yield "retry: 3000\n\n"

            while True:
                try:
                    chat_score_event = subscriber_queue.get(timeout=15)
                except Empty:
                    # 연결 유지용 메시지
                    yield ": keep-alive\n\n"
                    continue

                yield _sse_event(
                    chat_score_event.event,
                    chat_score_event.data,
                )

        finally:
            chat_score_event_broker.unsubscribe(transaction_id, subscriber_queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )


@transaction_chat_router.get(
    "/{transaction_id}/chat-session/detail",
    response_model=ApiResponse[TransactionChatSessionDetailResponse],
    summary="거래별 채팅 상담 내역 조회",
)
def get_transaction_chat_session_detail(
    transaction_id: TransactionIdPath,
    session: SessionDep,
) -> ApiResponse[TransactionChatSessionDetailResponse]:
    """담당자가 거래 한 건의 상담 내용을 열었을 때 필요한 것을 한 번에 돌려준다"""

    repository = ChatSessionRepository(session)
    chat_session = repository.find_by_transaction(transaction_id)
    if chat_session is None:
        return success_response(
            TransactionChatSessionDetailResponse(transaction_id=transaction_id)
        )

    return success_response(
        _transaction_session_detail(repository, transaction_id, chat_session)
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


def _run_turn(run: Callable[[], T]) -> T:
    """파이프라인이 거절한 입력을 HTTP 409로 변환한다."""

    try:
        return run()
    except ChatTurnRejectedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


def _sse_event(event: str, data: dict[str, Any]) -> str:
    """한 SSE 이벤트를 UTF-8 JSON data 한 줄로 직렬화한다."""

    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


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


def _transaction_session_detail(
    repository: ChatSessionRepository,
    transaction_id: int,
    chat_session: ChatSession,
) -> TransactionChatSessionDetailResponse:
    """대화 이력에 추출·채점 결과를 붙여 담당자 화면용 상세 응답을 만든다."""

    return TransactionChatSessionDetailResponse(
        transaction_id=transaction_id,
        chat_session_id=chat_session.chat_session_id,
        status=chat_session.status,
        completed_at=chat_session.completed_at,
        messages=[
            _message_response(message)
            for message in repository.list_messages(chat_session)
        ],
        type_scores=build_type_score_responses(
            repository.get_fraud_type_scores(transaction_id)
        ),
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

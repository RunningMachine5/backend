"""고객 대응 챗봇 라우터.

설계는 docs/customer-chatbot/README.md 의 2.2~2.3, 2.7 이다. 라우터는 두 개다.

- ``/chat`` — 고객이 쓰는 경로(접속·본인인증, 버튼, 답변 송수신)
- ``/transactions`` — 담당자 화면이 쓰는 경로(거래별 세션 상태 조회, 상담 내역 조회)

**세션 생성은 HTTP 로 열지 않는다.** PRD 2.1 대로 FDS 파이프라인이
[session_creator.py](../services/chatbot/session_creator.py)를 함수로 호출하고, 로컬에서
접속 URL 이 필요하면 `scripts/seed_chat_session.py` 를 쓴다.

트랜잭션은 이 라우터가 소유한다(``get_session`` 은 commit 하지 않는다). 턴 실행은
[customer_chatbot_pipeline.py](../pipelines/customer_chatbot_pipeline.py)가 커밋과 상태
변경 발행을 함께 처리한다.

본인인증은 출생연도 4자리 대조뿐이고 토큰을 발급하지 않는다. 실패 횟수 제한·URL 토큰·
세션 TTL 은 MVP 범위 밖이다(PRD 3.3).
"""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, status

from app.core.common_response import ApiResponse, success_response
from app.core.db import SessionDep
from app.data.model.chatbot import (
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
    FraudTypeScoreAfterChat,
)
from app.domain.fraud_type_codes import get_fraud_type_display_name
from app.dto.chatbot import (
    ChatButtonActionRequest,
    ChatFraudTypeScoreResponse,
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
    CustomerChatbotPipeline,
)
from app.repositories.chat_session import ChatSessionRepository
from app.services.chatbot.identity_verifier import verify_birth_year


router = APIRouter(prefix="/chat", tags=["chat"])
transaction_chat_router = APIRouter(
    prefix="/transactions", tags=["chat-sessions"]
)


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
    """출생연도 4자리로 본인을 확인하고 챗봇 화면 진입 상태를 돌려준다(PRD 2.2).

    고객이 이메일의 접속 URL 을 열면 화면은 출생연도 입력만 띄우고 이 API 를 부른다.
    **고객이 처음 접속할 때 가장 먼저 호출하는 API** 이며, 조회 전용인
    `GET /chat/{chat_session_id}` 는 인증을 마친 뒤의 새로고침·재접속에만 쓴다.

    - 첫 진입(`status` 가 `URL_SENT` 이고 아직 메시지가 없는 세션)이면 최초 알림을
      생성해 `messages` 에 담아 돌려준다. 프론트는 이 메시지 뒤에 버튼 3종을 표시한다.
    - 이미 대화가 시작된 세션은 이력만 돌려주므로 재인증해도 알림이 다시 쌓이지 않는다.
    - `is_older` 가 참이면 고령자 전용 UI 로 분기한다(화면 분기는 프론트가 한다).

    인증 토큰은 발급하지 않는다. 실패 횟수 제한·URL 토큰·세션 TTL 은 MVP 범위 밖이다(PRD 3.3).
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
    summary="세션 상태·대화 이력 조회(새로고침·재접속)",
    responses={status.HTTP_404_NOT_FOUND: _SESSION_NOT_FOUND_RESPONSE},
)
def get_chat_session(
    chat_session_id: ChatSessionIdPath,
    session: SessionDep,
) -> ApiResponse[ChatSessionDetailResponse]:
    """세션 상태와 대화 이력을 조회한다(새로고침·재접속).

    인증을 마친 화면이 대화를 다시 그릴 때 쓴다. 응답 형태가 본인인증 API 와 같아
    렌더링 코드를 그대로 재사용할 수 있다.

    최초 알림을 생성하지 않으므로 **첫 접속 경로로 쓰지 않는다**. 첫 접속은
    `POST /chat/{chat_session_id}/verify` 다.
    """

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
    """최초 알림 뒤의 버튼 3종을 처리한다(PRD 2.3).

    `status` 가 `URL_SENT` 인 동안에만 받는다. 이미 상담이 시작됐거나 끝난 세션에 다시
    보내면 409 다.

    | `action` | 동작 | 처리 후 `status` |
    | --- | --- | --- |
    | `START_CHAT` | 안내 문구 없이 첫 질문을 출력 | `IN_PROGRESS` |
    | `REQUEST_HANDOFF` | 상담사 연결 대기 안내 출력 | `HANDOFF_REQUESTED` |
    | `END_CHAT` | 상담 종료 안내 출력 | `DONE` |

    응답의 `messages` 는 **이번 턴에 챗봇이 보낸 메시지 본문만** 담는다(누적 이력이
    아니다).
    """

    chat_session = _require_session(session, chat_session_id)
    result = _run_turn(
        lambda: _pipeline(session, chat_session).handle_button(payload.action)
    )
    return success_response(_turn_response(chat_session_id, result))


@router.post(
    "/{chat_session_id}/messages",
    response_model=ApiResponse[ChatTurnResponse],
    summary="고객 답변 전송",
    responses={
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
) -> ApiResponse[ChatTurnResponse]:
    """고객 답변 한 건을 평가하고 그 턴의 챗봇 응답을 돌려준다(PRD 2.4~2.6).

    `status` 가 `IN_PROGRESS` 이고 답변을 기다리는 질문이 있을 때만 받는다. 그 외에는 409 다.

    답변 충실도 판정에 따라 그 턴의 출력이 갈린다.

    | 판정 | 이번 턴 출력 | 처리 후 `status` |
    | --- | --- | --- |
    | `SUFFICIENT` | 대응 가이드(RAG) 안내 + 다음 질문 | `IN_PROGRESS` |
    | `TOO_VAGUE` | 재질문 안내(`question_step` 유지) | `IN_PROGRESS` |
    | `WANT_END` | 사기 정황 채점을 집계한 뒤 상담 종료 안내 | `DONE` |

    한 질문에서 허용하는 응답은 최초 1회 + 재질문 2회다. `TOO_VAGUE` 가 3회째까지
    이어지면 마지막 답변을 채택하고 전환 안내와 함께 다음 질문으로 넘어간다.

    평가·추출 LLM 이 실패해도 턴은 실패하지 않는다. 그 결과만 건너뛰고 다음 질문으로 진행한다.
    """

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


@transaction_chat_router.get(
    "/{transaction_id}/chat-session",
    response_model=ApiResponse[TransactionChatSessionStatusResponse],
    summary="거래별 채팅 세션 상태 조회",
)
def get_transaction_chat_session_status(
    transaction_id: TransactionIdPath,
    session: SessionDep,
) -> ApiResponse[TransactionChatSessionStatusResponse]:
    """거래 목록 항목 하나에 연결된 채팅 세션의 현재 상태를 조회한다(PRD 2.7).

    담당자 화면이 주기적으로 폴링해 현재값을 확인하는 경로다.
    거래 한 건에 세션은 하나뿐이다.

    세션이 없는 거래도 목록에 그대로 남아야 하므로 404 가 아니라 빈 값을 돌려준다
    (`chat_session_id` 와 `status` 가 모두 `null`).
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
        type_scores=_type_score_responses(
            repository.get_fraud_type_scores(transaction_id)
        ),
    )


def _type_score_responses(
    scores: FraudTypeScoreAfterChat | None,
) -> list[ChatFraudTypeScoreResponse]:
    """점수 내림차순으로 정렬한다. 동점이면 코드 오름차순이라 순서가 흔들리지 않는다."""

    if scores is None:
        return []

    return [
        ChatFraudTypeScoreResponse(
            type_code=type_code,
            display_name=get_fraud_type_display_name(type_code),
            score=int(score),
        )
        for type_code, score in sorted(
            scores.type_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


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

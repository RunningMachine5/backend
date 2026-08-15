"""고객 대응 챗봇 라우터

기존 `POST /chat/ask`(세션 개념 없이 질문 문자열 하나만 받던 Fake 체인)는
제거했다. 세션 생성·메시지 송수신·본인인증·SSE 반환 경로는
[PRD 2.7](../../docs/customer-chatbot/README.md#27-상담사-반환-경로-거래별-상태-조회--sse)에
따라 이 파일에 다시 추가한다.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/chat", tags=["chat"])

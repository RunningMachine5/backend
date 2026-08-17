"""로컬 테스트용 챗봇 세션 생성 스크립트.

PRD 2.1 대로 세션 생성은 FDS 파이프라인이 함수로 호출하는 경로뿐이고 HTTP 로 열려 있지
않다. 사기 판정을 기다리지 않고 임의 거래의 접속 URL 을 뽑아야 하는 로컬·데모 상황을
위해 [session_creator.py](../app/services/chatbot/session_creator.py)를 직접 호출한다.

레포 루트에서 모듈로 실행한다(``python scripts/...`` 형태는 ``app`` 을 찾지 못한다).

```bash
uv run --env-file .env python -m scripts.create_chat_session 42
uv run --env-file .env python -m scripts.create_chat_session 42 \
    --top-fraud-types VOICE_PHISHING MESSENGER_PHISHING
```

**운영에서 쓰지 않는다.** 서버와 다른 프로세스라 in-process 브로커(PRD 2.7)에 상태 변경을
발행하지 못하므로 담당자 화면에는 SSE 이벤트가 뜨지 않는다. 거래별 상태 조회
(``GET /agent/transactions/{id}/chat-session``)로 새로고침하면 보인다.
"""

from __future__ import annotations

import argparse
import sys

from pydantic import ValidationError

from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.dto.chatbot import CreateChatRequest


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="create_chat_session",
        description="거래 한 건의 챗봇 세션을 만들고 접속 URL 을 출력한다(로컬 전용).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "거래당 세션은 하나이므로 멱등하다. 이미 세션이 있으면 기존 URL 을 그대로\n"
            "출력한다. 새 세션으로 다시 시작하려면\n"
            "--recreate 를 쓴다(기존 대화 이력이 함께 지워진다)."
        ),
    )
    parser.add_argument(
        "transaction_id",
        type=int,
        help="세션을 만들 transactions.id",
    )
    parser.add_argument(
        "--top-fraud-types",
        nargs=2,
        metavar=("FIRST", "SECOND"),
        help=(
            "유형판별 질문에 쓸 상위 2개 사기유형 코드(서로 달라야 한다). "
            f"가능한 값: {', '.join(sorted(FINAL_FRAUD_TYPE_CODES))}"
        ),
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="기존 세션과 그 대화 이력을 지우고 새로 만든다.",
    )
    return parser.parse_args(argv)


def build_request(args: argparse.Namespace) -> CreateChatRequest:
    """스크립트 인자를 PRD 2.1 의 생성 입력으로 검증한다."""

    return CreateChatRequest(
        transaction_id=args.transaction_id,
        top_fraud_types=args.top_fraud_types,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        request = build_request(args)
    except ValidationError as error:
        print(f"인자가 올바르지 않습니다:\n{error}", file=sys.stderr)
        return 2

    # 무거운 import 는 인자 검증 뒤로 미룬다(엔진 생성 포함).
    from sqlmodel import Session

    import app.data.model  # noqa: F401  모든 테이블을 메타데이터에 등록한다
    from app.core.db import engine
    from app.repositories.chat_session import ChatSessionRepository
    from app.services.chatbot.session_creator import (
        ChatSessionCreator,
        ChatSessionTargetNotFoundError,
    )
    from app.services.chatbot.session_url import build_chat_url

    with Session(engine) as session:
        if args.recreate:
            existing = ChatSessionRepository(session).find_by_transaction(
                request.transaction_id
            )
            if existing is not None:
                # 자식 테이블(chat_messages·chat_answers·추출·채점)은 DB 의
                # ON DELETE CASCADE 로 함께 지워진다.
                print(
                    f"[--recreate] 기존 세션과 대화 이력을 삭제합니다: "
                    f"{existing.chat_session_id}"
                )
                session.delete(existing)
                session.flush()

        creator = ChatSessionCreator(session)
        try:
            result = creator.create(
                transaction_id=request.transaction_id,
                top_fraud_types=request.top_fraud_types,
            )
        except ChatSessionTargetNotFoundError as error:
            session.rollback()
            print(str(error), file=sys.stderr)
            return 1
        except Exception:
            session.rollback()
            raise

        session.commit()
        chat_session_id = result.chat_session.chat_session_id
        status = result.chat_session.status

    print("")
    print(f"세션 id   : {chat_session_id}")
    print(f"상태      : {status}")
    print(f"신규 생성 : {'예' if result.created else '아니오 (기존 세션)'}")
    print(
        f"수신 예정 주소: {result.notified_email}"
        f"{' (기본 주소 폴백)' if result.used_fallback_email else ''}"
    )
    print(f"접속 URL  : {build_chat_url(chat_session_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

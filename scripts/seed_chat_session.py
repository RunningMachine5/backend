"""로컬 테스트용 고객·계좌·거래를 한 번에 만들고 챗봇 세션까지 여는 스크립트.

운영 세션 생성 경로는 FDS 파이프라인뿐이라(PRD 2.1) 로컬에서 화면을 보려면 이상거래
판정을 기다려야 한다. `transactions` 가 비어 있는 로컬 DB 에서 매번 psql 로 고객·계좌·거래를
넣는 수고까지 없애려고, 원장 삽입부터 접속 URL 출력까지 한 번에 한다.

값은 매 실행마다 조금씩 달라진다. 같은 화면만 반복해서 보면 이름·금액·지역·단말 플래그가
화면에 어떻게 흘러가는지 확인할 수 없기 때문이다. 재현이 필요하면 ``--seed`` 를 준다.

```bash
uv run --env-file .env python -m scripts.seed_chat_session
uv run --env-file .env python -m scripts.seed_chat_session --older --seed 42
uv run --env-file .env python -m scripts.seed_chat_session \
    --top-fraud-types VOICE_PHISHING MESSENGER_PHISHING
uv run --env-file .env python -m scripts.seed_chat_session --cleanup
```

쌓인 시드 데이터는 ``--cleanup`` 으로 지운다. 이 스크립트가 붙인 식별자 접두어로만
찾으므로 손으로 넣었거나 파이프라인이 만든 데이터는 건드리지 않는다.

고객·계좌·거래 삽입과 세션 생성이 **한 트랜잭션**이라, 세션 생성이 실패하면 넣던 데이터도
함께 롤백된다. 쓰다 버린 고객이 쌓이지 않는다.

**운영에서 쓰지 않는다.** 지어낸 고객 원장을 그대로 밀어 넣고, 서버와 다른 프로세스라
in-process 브로커(PRD 2.7)에 상태 변경도 발행하지 못한다.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.data.model.account import Account
from app.data.model.customer import Customer
from app.data.model.transaction import Transaction
from app.domain.fraud_type_codes import FINAL_FRAUD_TYPE_CODES
from app.services.chatbot.session_creator import OLDER_CUSTOMER_AGE


KST = ZoneInfo("Asia/Seoul")

# 이 스크립트가 만든 행만 --cleanup 이 지울 수 있도록 식별자에 접두어를 붙인다.
# 접두어를 바꾸면 그 전에 만든 데이터는 --cleanup 대상에서 빠진다.
CUSTOMER_ID_PREFIX = "CUST-SEED-"
ACCOUNT_ID_PREFIX = "ACCT-SEED-"
IDENTIFICATION_NUMBER_PREFIX = "ID-SEED-"

# 흔한 성 + 이름 조합. 동명이인은 스키마가 허용하므로 중복돼도 문제없다.
FAMILY_NAMES = ("김", "이", "박", "최", "정", "강", "조", "윤", "장", "임")
GIVEN_NAMES = (
    "영수", "미영", "지훈", "서연", "준호", "은정", "성민", "다은",
    "현우", "수빈", "태호", "혜진", "동현", "지원", "상철", "민지",
)

# 거래 위치. 한국 좌표 범위(위도 33~39, 경도 124~132) 안이어야 ML 계약과 어긋나지 않는다.
# 지역 이름은 컬럼이 없어 저장하지 않고 출력에만 쓴다(transactions 는 좌표만 남긴다).
LOCATIONS = (
    ("Seoul", 37.5665, 126.9780),
    ("Busan", 35.1796, 129.0756),
    ("Incheon", 37.4563, 126.7052),
    ("Daegu", 35.8714, 128.6014),
    ("Daejeon", 36.3504, 127.3845),
    ("Gwangju", 35.1595, 126.8526),
    ("Ulsan", 35.5384, 129.3114),
    ("Suwon", 37.2636, 127.0286),
    ("Chuncheon", 37.8813, 127.7300),
    ("Jeju", 33.4996, 126.5312),
)

# 챗봇은 모바일 앱 알림에서 들어오는 흐름이라 mobile 을 크게 잡는다.
CHANNELS = ("mobile", "mobile", "mobile", "internet", "atm")
ACCESS_MEDIUMS = ("a", "b", "c", "d", "e", "f", "g", "h")
LOAN_TYPES = ("a", "b", "c", "d", "e")


@dataclass(frozen=True, slots=True)
class SeedResult:
    """이번 실행에서 만든 원장 세 건과 세션 생성에 넘길 값."""

    customer: Customer
    source_account: Account
    recipient_account: Account
    transaction: Transaction
    top_fraud_types: list[str] | None
    # transactions 에는 좌표만 남으므로 지역 이름은 출력용으로만 들고 있는다.
    location_name: str


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seed_chat_session",
        description=(
            "테스트용 고객·계좌·거래를 만들고 그 거래의 챗봇 세션 URL 까지 출력한다"
            "(로컬 전용)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "실행할 때마다 이름·금액·지역·단말 플래그가 달라진다.\n"
            "같은 데이터를 다시 만들려면 --seed 에 같은 값을 준다."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="난수 시드. 주면 같은 고객·거래 값이 재현된다(식별자는 매번 새로 만든다).",
    )
    parser.add_argument(
        "--top-fraud-types",
        nargs=2,
        metavar=("FIRST", "SECOND"),
        choices=sorted(FINAL_FRAUD_TYPE_CODES),
        default=None,
        help=(
            "유형판별 질문에 쓸 상위 2개 사기유형(서로 달라야 한다). "
            "생략하면 무작위로 두 개를 고른다."
        ),
    )
    parser.add_argument(
        "--no-fraud-types",
        action="store_true",
        help="상위 사기유형을 비운다. 일반 질문 폴백 경로를 보려면 쓴다.",
    )

    age = parser.add_mutually_exclusive_group()
    age.add_argument(
        "--older",
        action="store_true",
        help=f"{OLDER_CUSTOMER_AGE}세 이상 고객으로 만든다(is_older = true).",
    )
    age.add_argument(
        "--younger",
        action="store_true",
        help=f"{OLDER_CUSTOMER_AGE}세 미만 고객으로 만든다.",
    )

    parser.add_argument(
        "--email",
        default=None,
        help=(
            "고객 이메일. 생략하면 NULL 이라 CHAT_FALLBACK_EMAIL 로 폴백한다"
            "(폴백 경로 확인용 기본값)."
        ),
    )
    parser.add_argument(
        "--amount",
        type=int,
        default=None,
        help="출금액(원, 양수로 준다). 생략하면 30만~2,000만 원 사이에서 고른다.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help=(
            "새로 만들지 않고, 이 스크립트가 만든 고객·계좌·거래를 모두 지운다"
            f"(식별자가 {CUSTOMER_ID_PREFIX}/{ACCOUNT_ID_PREFIX} 로 시작하는 행만)."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="--cleanup 의 확인 질문을 건너뛴다.",
    )
    return parser.parse_args(argv)


# --cleanup 은 삭제만 하므로 생성 옵션과 함께 쓰면 사용자의 의도가 갈린다.
SEEDING_ONLY_OPTIONS = (
    ("--seed", lambda args: args.seed is not None),
    ("--top-fraud-types", lambda args: args.top_fraud_types is not None),
    ("--no-fraud-types", lambda args: args.no_fraud_types),
    ("--older", lambda args: args.older),
    ("--younger", lambda args: args.younger),
    ("--email", lambda args: args.email is not None),
    ("--amount", lambda args: args.amount is not None),
)


def conflicting_seeding_options(args: argparse.Namespace) -> list[str]:
    """``--cleanup`` 과 함께 주어진 생성 전용 옵션 이름을 모은다."""

    return [name for name, is_set in SEEDING_ONLY_OPTIONS if is_set(args)]


def build_seed(
    args: argparse.Namespace,
    *,
    rng: random.Random,
    now: datetime,
) -> SeedResult:
    """인자와 난수로 삽입할 원장 세 건을 조립한다(DB 를 건드리지 않는다)."""

    suffix = uuid4().hex[:8].upper()
    birth_year = _pick_birth_year(args, rng=rng, now=now)

    customer = Customer(
        id=f"{CUSTOMER_ID_PREFIX}{suffix}",
        name=rng.choice(FAMILY_NAMES) + rng.choice(GIVEN_NAMES),
        birth_date=date(birth_year, rng.randint(1, 12), rng.randint(1, 28)),
        gender=rng.choice(("male", "female")),
        identification_number=f"{IDENTIFICATION_NUMBER_PREFIX}{suffix}",
        phone_number=f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
        email=args.email,
        registration_datetime=now - timedelta(days=rng.randint(180, 3650)),
        credit_rating=rng.randint(1, 9),
        loan_type=rng.choice(LOAN_TYPES),
    )

    source_account = Account(
        id=f"{ACCOUNT_ID_PREFIX}{suffix}-SRC",
        customer_id=customer.id,
        account_number=_unique_account_number(),
        account_type=rng.choice(LOAN_TYPES),
        creation_datetime=customer.registration_datetime,
        suspend_status=False,
    )
    # 수취 계좌는 외부에서 처음 관측되는 계좌라 고객을 붙이지 않는다(accounts 주석).
    recipient_account = Account(
        id=f"{ACCOUNT_ID_PREFIX}{suffix}-DST",
        customer_id=None,
        account_number=_unique_account_number(),
        account_type=None,
        creation_datetime=None,
        suspend_status=False,
    )

    amount = args.amount if args.amount is not None else _random_amount(rng)
    # 출금은 음수다. 최초 알림 문구가 이 부호로 출금/입금을 가른다.
    signed_amount = -abs(amount)
    # 잔액 스냅샷이 음수가 되지 않도록 출금액 위에서 시작 잔액을 잡는다.
    initial_balance = abs(signed_amount) + rng.randrange(100_000, 30_000_000, 10_000)
    location_name, lat, lon = rng.choice(LOCATIONS)

    transaction = Transaction(
        customer_id=customer.id,
        source_account_number=source_account.account_number,
        recipient_account_number=recipient_account.account_number,
        # 알림이 "방금 일어난 거래"로 읽히도록 최근 3일 안에서 고른다.
        transaction_datetime=(
            now.astimezone(KST) - timedelta(minutes=rng.randint(5, 3 * 24 * 60))
        ),
        transaction_amount=signed_amount,
        channel=rng.choice(CHANNELS),
        type_general_automatic="general",
        access_medium=rng.choice(ACCESS_MEDIUMS),
        num_connection_failure=_weighted_connection_failure(rng),
        another_person_account=True,
        initial_balance=initial_balance,
        balance=initial_balance + signed_amount,
        remaining_amount_daily_limit_exceeded=rng.randrange(0, 50_000_000, 100_000),
        location_lat=lat,
        location_lon=lon,
        rooting_jailbreak_indicator=_rare(rng),
        mobile_roaming_indicator=_rare(rng),
        vpn_indicator=_rare(rng),
        flag_terminal_malicious_behavior_1=_rare(rng),
        flag_terminal_malicious_behavior_2=_rare(rng),
        flag_terminal_malicious_behavior_3=_rare(rng),
        flag_terminal_malicious_behavior_5=_rare(rng),
        flag_terminal_malicious_behavior_6=_rare(rng),
    )

    return SeedResult(
        customer=customer,
        source_account=source_account,
        recipient_account=recipient_account,
        transaction=transaction,
        top_fraud_types=_pick_top_fraud_types(args, rng=rng),
        location_name=location_name,
    )


def _pick_birth_year(
    args: argparse.Namespace,
    *,
    rng: random.Random,
    now: datetime,
) -> int:
    """``--older``/``--younger`` 가 없으면 고령자 여부도 무작위로 고른다."""

    # is_older 는 출생연도만으로 판정한다(session_creator._is_older_customer).
    older_boundary = now.year - OLDER_CUSTOMER_AGE
    if args.older:
        older = True
    elif args.younger:
        older = False
    else:
        older = rng.random() < 0.5

    if older:
        return rng.randint(older_boundary - 30, older_boundary)
    return rng.randint(older_boundary + 1, now.year - 19)


def _pick_top_fraud_types(
    args: argparse.Namespace,
    *,
    rng: random.Random,
) -> list[str] | None:
    """생략하면 서로 다른 두 유형을 무작위로 고른다."""

    if args.no_fraud_types:
        return None
    if args.top_fraud_types is not None:
        return list(args.top_fraud_types)
    return rng.sample(sorted(FINAL_FRAUD_TYPE_CODES), 2)


def _unique_account_number() -> str:
    """12자리 계좌번호.

    ``accounts.account_number`` 가 UNIQUE 라서 난수 시드에서 뽑으면 ``--seed`` 를 준
    두 번째 실행이 중복 키로 죽는다. 계좌번호만은 시드와 무관하게 만든다.
    """

    return f"{uuid4().int % 10**12:012d}"


def _random_amount(rng: random.Random) -> int:
    """1,000원 단위 30만~2,000만 원."""

    return rng.randrange(300_000, 20_000_000, 1_000)


def _weighted_connection_failure(rng: random.Random) -> int:
    """대부분 0이고 가끔 몇 번 실패한 접속으로 만든다."""

    return rng.choice((0, 0, 0, 0, 1, 1, 2, 3))


def _rare(rng: random.Random, probability: float = 0.15) -> bool:
    """단말 이상 플래그는 드물게만 켠다."""

    return rng.random() < probability


def _run_cleanup(*, assume_yes: bool) -> int:
    """이 스크립트가 만든 원장을 지운다. 세션·대화 이력은 FK CASCADE 가 따라 지운다."""

    from sqlmodel import Session, col, delete, func, select

    import app.data.model  # noqa: F401  모든 테이블을 메타데이터에 등록한다
    from app.core.db import engine
    from app.data.model.chatbot import ChatSession

    customer_pattern = f"{CUSTOMER_ID_PREFIX}%"
    account_pattern = f"{ACCOUNT_ID_PREFIX}%"

    with Session(engine) as session:
        customers = session.exec(
            select(func.count()).select_from(Customer).where(
                col(Customer.id).like(customer_pattern)
            )
        ).one()
        accounts = session.exec(
            select(func.count()).select_from(Account).where(
                col(Account.id).like(account_pattern)
            )
        ).one()
        transactions = session.exec(
            select(func.count()).select_from(Transaction).where(
                col(Transaction.customer_id).like(customer_pattern)
            )
        ).one()
        chat_sessions = session.exec(
            select(func.count())
            .select_from(ChatSession)
            .join(Transaction, col(ChatSession.transaction_id) == col(Transaction.id))
            .where(col(Transaction.customer_id).like(customer_pattern))
        ).one()

        if customers == 0 and accounts == 0 and transactions == 0:
            print("지울 시드 데이터가 없습니다.")
            return 0

        print("── 지울 데이터 ──")
        print(f"고객      : {customers}명")
        print(f"계좌      : {accounts}개")
        print(f"거래      : {transactions}건")
        print(f"챗봇 세션 : {chat_sessions}개 (대화 이력은 FK CASCADE 로 함께 지워진다)")

        if not assume_yes:
            if not sys.stdin.isatty():
                print(
                    "확인을 받을 수 없는 환경입니다. 그래도 지우려면 --yes 를 붙이세요.",
                    file=sys.stderr,
                )
                return 2
            answer = input("정말 지울까요? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("취소했습니다.")
                return 1

        try:
            # 거래를 먼저 지워야 한다. customers·accounts 로 향한 FK 가 RESTRICT 라
            # 부모부터 지우면 거부당한다. 챗봇 세션은 거래에서 CASCADE 로 따라 지워진다.
            session.exec(
                delete(Transaction).where(
                    col(Transaction.customer_id).like(customer_pattern)
                )
            )
            session.exec(
                delete(Account).where(col(Account.id).like(account_pattern))
            )
            session.exec(
                delete(Customer).where(col(Customer.id).like(customer_pattern))
            )
            session.commit()
        except Exception:
            session.rollback()
            raise

    print("")
    print(f"지웠습니다: 고객 {customers}명 / 계좌 {accounts}개 / 거래 {transactions}건")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.cleanup:
        conflicts = conflicting_seeding_options(args)
        if conflicts:
            print(
                f"--cleanup 은 삭제만 합니다. 함께 쓸 수 없는 옵션: "
                f"{', '.join(conflicts)}",
                file=sys.stderr,
            )
            return 2
        return _run_cleanup(assume_yes=args.yes)

    if (
        args.top_fraud_types is not None
        and args.top_fraud_types[0] == args.top_fraud_types[1]
    ):
        print("--top-fraud-types 의 두 사기유형은 서로 달라야 합니다.", file=sys.stderr)
        return 2
    if args.amount is not None and args.amount <= 0:
        print("--amount 는 0보다 큰 값이어야 합니다(출금 부호는 스크립트가 붙인다).",
              file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    now = datetime.now(UTC)
    seeded = build_seed(args, rng=rng, now=now)

    # 무거운 import 는 인자 검증 뒤로 미룬다(엔진 생성 포함).
    from pydantic import ValidationError
    from sqlmodel import Session

    import app.data.model  # noqa: F401  모든 테이블을 메타데이터에 등록한다
    from app.core.db import engine
    from app.dto.chatbot import CreateChatRequest
    from app.services.chatbot.session_creator import ChatSessionCreator
    from app.services.chatbot.session_url import build_chat_url

    # commit 뒤에도 방금 넣은 값을 그대로 출력해야 하므로 만료시키지 않는다.
    with Session(engine, expire_on_commit=False) as session:
        try:
            # 세 테이블 사이에 ORM 관계를 두지 않아 SQLAlchemy 가 INSERT 순서를
            # 정렬해주지 못한다. FK 방향대로 직접 끊어서 내보낸다.
            session.add(seeded.customer)
            session.flush()
            session.add(seeded.source_account)
            session.add(seeded.recipient_account)
            session.flush()
            session.add(seeded.transaction)
            session.flush()

            transaction_id = seeded.transaction.id
            request = CreateChatRequest(
                transaction_id=transaction_id,
                top_fraud_types=seeded.top_fraud_types,
            )
            result = ChatSessionCreator(session).create(
                transaction_id=request.transaction_id,
                top_fraud_types=request.top_fraud_types,
            )
            session.commit()
        except ValidationError as error:
            session.rollback()
            print(f"세션 생성 입력이 올바르지 않습니다:\n{error}", file=sys.stderr)
            return 2
        except Exception:
            # 고객·계좌·거래를 넣다 만 상태로 남기지 않는다.
            session.rollback()
            raise

        chat_session_id = result.chat_session.chat_session_id
        status = result.chat_session.status
        is_older = result.chat_session.is_older

    _print_summary(
        seeded=seeded,
        transaction_id=transaction_id,
        chat_session_id=chat_session_id,
        status=status,
        is_older=is_older,
        notified_email=result.notified_email,
        used_fallback_email=result.used_fallback_email,
        chat_url=build_chat_url(chat_session_id),
    )
    return 0


def _print_summary(
    *,
    seeded: SeedResult,
    transaction_id: int,
    chat_session_id: str,
    status: object,
    is_older: bool,
    notified_email: str | None,
    used_fallback_email: bool,
    chat_url: str,
) -> None:
    customer = seeded.customer
    transaction = seeded.transaction
    withdrawal = abs(transaction.transaction_amount)
    top_fraud_types = (
        ", ".join(seeded.top_fraud_types)
        if seeded.top_fraud_types
        else "(없음 — 일반 질문 폴백)"
    )

    print("")
    print("── 만든 테스트 데이터 ──")
    print(f"고객 id     : {customer.id}")
    print(f"이름        : {customer.name}")
    print(f"생년월일    : {customer.birth_date}")
    print(f"출금 계좌   : {seeded.source_account.account_number}")
    print(f"수취 계좌   : {seeded.recipient_account.account_number}")
    print(f"거래 id     : {transaction_id}")
    print(f"거래 일시   : {transaction.transaction_datetime.astimezone(KST)}")
    print(
        f"거래 위치   : {seeded.location_name} "
        f"({transaction.location_lat}, {transaction.location_lon})"
    )
    print("")
    print("── 챗봇 세션 ──")
    print(f"세션 id     : {chat_session_id}")
    print(f"상태        : {status}")
    print(f"상위 사기유형: {top_fraud_types}")
    print(
        f"수신 예정 주소: {notified_email}"
        f"{' (기본 주소 폴백)' if used_fallback_email else ''}"
    )
    print(f"접속 URL    : {chat_url}")
    print("")
    print("── 화면에서 확인할 값 ──")
    print(f"본인인증 4자리 : {customer.birth_date.year}")
    print(f"최초 알림 금액 : {withdrawal:,}원 출금")
    print(f"is_older       : {'true' if is_older else 'false'}")
    print("")
    print("빈 대화로 다시 시작하려면 그냥 한 번 더 실행한다(새 거래가 만들어진다).")
    print("쌓인 시드 데이터를 지우려면:")
    print("  uv run --env-file .env python -m scripts.seed_chat_session --cleanup")


if __name__ == "__main__":
    raise SystemExit(main())

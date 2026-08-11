"""ERD가 요구하는 PostgreSQL 전용 타입을 SQLite 테스트에서도 쓸 수 있게 맞춘다.

테스트는 `create_engine("sqlite://")` 위에 테이블을 직접 만들기 때문에 INET,
MACADDR 같은 방언 전용 타입을 그대로 쓰면 DDL 컴파일 단계에서 실패한다.
기존 `BigInteger().with_variant(Integer(), "sqlite")` 관례를 따라 방언별
대체 타입을 한곳에서 관리한다.
"""

from sqlalchemy import BigInteger, Integer, Interval, String
from sqlalchemy.dialects.postgresql import INET, JSONB, MACADDR
from sqlalchemy.types import JSON

# SQLite에는 64bit AUTOINCREMENT 개념이 없어 INTEGER로 낮춘다.
BIGINT_PRIMARY_KEY = BigInteger().with_variant(Integer(), "sqlite")

# PostgreSQL에서는 JSONB, 그 외에는 표준 JSON으로 저장한다.
JSON_COLUMN = JSON().with_variant(JSONB(), "postgresql")

# IPv6까지 담을 수 있는 45자, MAC 주소는 17자를 SQLite 대체 폭으로 사용한다.
INET_COLUMN = INET().with_variant(String(45), "sqlite")
MACADDR_COLUMN = MACADDR().with_variant(String(17), "sqlite")

# PostgreSQL은 native INTERVAL, 그 외 방언은 SQLAlchemy의 epoch 기준 에뮬레이션.
INTERVAL_COLUMN = Interval(native=True).with_variant(
    Interval(native=False),
    "sqlite",
)


__all__ = [
    "BIGINT_PRIMARY_KEY",
    "INET_COLUMN",
    "INTERVAL_COLUMN",
    "JSON_COLUMN",
    "MACADDR_COLUMN",
]

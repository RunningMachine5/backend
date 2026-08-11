"""ERD가 요구하는 PostgreSQL 전용 타입을 SQLite 테스트에서도 쓸 수 있게 맞춘다.

테스트는 `create_engine("sqlite://")` 위에 테이블을 직접 만들기 때문에 INET,
MACADDR 같은 방언 전용 타입을 그대로 쓰면 DDL 컴파일 단계에서 실패한다.
기존 `BigInteger().with_variant(Integer(), "sqlite")` 관례를 따라 방언별
대체 타입을 한곳에서 관리한다.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import BigInteger, Integer, Interval, String
from sqlalchemy.dialects.postgresql import INET, JSONB, MACADDR
from sqlalchemy.types import JSON, TypeDecorator


class SQLiteIntervalSeconds(TypeDecorator[timedelta]):
    """SQLite에서 timedelta를 정수 초로 보존한다.

    SQLAlchemy의 비 native Interval 에뮬레이션은 Python 3.13 sqlite3가 반환한
    문자열에 datetime 뺄셈을 시도한다. 테스트 방언에서는 프로젝트 입력의
    초 단위 정밀도에 맞춰 정수로 저장해 양수·음수 interval을 안정적으로
    왕복한다.
    """

    impl = Integer
    cache_ok = True

    def process_bind_param(
        self,
        value: timedelta | None,
        _dialect: Any,
    ) -> int | None:
        if value is None:
            return None
        return int(value.total_seconds())

    def process_result_value(
        self,
        value: int | None,
        _dialect: Any,
    ) -> timedelta | None:
        if value is None:
            return None
        return timedelta(seconds=value)


# SQLite에는 64bit AUTOINCREMENT 개념이 없어 INTEGER로 낮춘다.
BIGINT_PRIMARY_KEY = BigInteger().with_variant(Integer(), "sqlite")

# PostgreSQL에서는 JSONB, 그 외에는 표준 JSON으로 저장한다.
JSON_COLUMN = JSON().with_variant(JSONB(), "postgresql")

# IPv6까지 담을 수 있는 45자, MAC 주소는 17자를 SQLite 대체 폭으로 사용한다.
INET_COLUMN = INET().with_variant(String(45), "sqlite")
MACADDR_COLUMN = MACADDR().with_variant(String(17), "sqlite")

# PostgreSQL은 native INTERVAL, 그 외 방언은 SQLAlchemy의 epoch 기준 에뮬레이션.
INTERVAL_COLUMN = Interval(native=True).with_variant(
    SQLiteIntervalSeconds(),
    "sqlite",
)


__all__ = [
    "BIGINT_PRIMARY_KEY",
    "INET_COLUMN",
    "INTERVAL_COLUMN",
    "JSON_COLUMN",
    "MACADDR_COLUMN",
    "SQLiteIntervalSeconds",
]

from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy.engine import Connection
from sqlmodel import Session, create_engine

from app.core.config import DATABASE_URL

# DB 접속 정보 + 커넥션 풀 + SQL 실행/Dialect 를 관리하는 객체.
# 프로세스당 하나만 만들어서 앱 전체가 재사용
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,  # 풀에서 꺼낸 커넥션이 죽어 있으면 버리고 새로 만든다
    pool_size=5,         # 평상시 유지하는 커넥션 수
    max_overflow=10,     # 몰릴 때 추가로 더 열 수 있는 수 (최대 15개)
    pool_timeout=30,     # 풀이 다 찼을 때 기다리는 시간(초)
    pool_recycle=1800,   # 30분 넘게 산 커넥션은 폐기 (DB/방화벽이 조용히 끊는 것 방지)
)


def get_session() -> Generator[Session, None, None]:  # [YieldType, SendType, ReturnType]
    """FastAPI 요청 단위 SQLModel Session (의존성 주입용).

    커밋은 하지 않는다. 쓰기 작업은 호출하는 쪽에서 commit / rollback 을 관리한다.
    """
    with Session(engine) as session:
        yield session


# 라우터마다 다시 선언하지 않도록 여기서 한 번만 정의한다
SessionDep = Annotated[Session, Depends(get_session)]


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """원시 SQL 용 SQLAlchemy 커넥션. (ORM 을 쓸 수 있으면 SessionDep 을 쓴다)

    블록을 정상적으로 빠져나오면 commit, 예외가 나면 rollback 한다.
    블록이 끝나도 물리 커넥션은 닫히지 않고 engine 의 풀로 반환된다.

    SQL 문자열은 connection.exec_driver_sql(...) 로 실행한다.
    """
    with engine.begin() as connection:
        yield connection

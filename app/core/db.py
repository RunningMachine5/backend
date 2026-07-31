from collections.abc import Generator
from sqlmodel import Session, create_engine
from app.core.config import DATABASE_URL

# DataSource(HikariCP 커넥션 풀) + EntityManagerFactory 를 합친 것. 앱 전체에 1개만 두는 무거운 객체
engine = create_engine(DATABASE_URL, echo=True, pool_pre_ping=True)

def get_session() -> Generator[Session, None, None]: # [YieldType, SendType, ReturnType]
    # FastAPI 에서 db 접근하고 싶을때 의존성 주입용
    with Session(engine) as session:
        yield session
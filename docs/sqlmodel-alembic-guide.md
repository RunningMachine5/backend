# SQLModel + Alembic 연동 가이드 (Spring Boot JPA 개발자용)

이 프로젝트(`fdshield`)에 SQLModel 모델(= JPA 엔티티)을 정의하고 Alembic(= Flyway)으로
스키마를 관리하는 전체 흐름을 정리한 문서입니다.

**현재 프로젝트 상태**

- `pyproject.toml`: `sqlmodel`, `alembic`, `sqlalchemy`, `psycopg[binary]`, `pgvector` 설치 완료
- `docker-compose.yml`: paradedb(PostgreSQL 18 + pgvector + pg_search) 가 `localhost:5432` 에 기동
  - user `root` / password `1234` / db `fdshield-db`
- `docker/activate-vector-extension.sql`: 컨테이너 최초 기동 시 `vector`, `pg_search` 확장 생성
- `app/data/model/transaction.py`, `app/data/model/fss_document.py`: 아직 빈 파일

---

## 목차

1. [JPA 개념 매핑](#1-jpa-개념-매핑)
2. [최종 파일 배치](#2-최종-파일-배치)
3. [Step 1 — 접속 설정](#3-step-1--접속-설정)
4. [Step 2 — 테스트용 모델(엔티티)](#4-step-2--테스트용-모델엔티티)
5. [Step 3 — Alembic 초기화](#5-step-3--alembic-초기화)
6. [Step 4 — alembic.ini 수정](#6-step-4--alembicini-수정)
7. [Step 5 — migrations/env.py 교체 (핵심)](#7-step-5--migrationsenvpy-교체-핵심)
8. [Step 6 — script.py.mako 수정 (SQLModel 필수)](#8-step-6--scriptpymako-수정-sqlmodel-필수)
9. [Step 7 — 마이그레이션 생성 & 적용](#9-step-7--마이그레이션-생성--적용)
10. [Step 8 — FastAPI에서 CRUD 검증](#10-step-8--fastapi에서-crud-검증)
11. [Step 9 — 스키마 변경 재연습](#11-step-9--스키마-변경-재연습)
12. [자주 걸리는 함정](#12-자주-걸리는-함정)
13. [보너스 — pgvector 컬럼](#13-보너스--pgvector-컬럼)
14. [요약 체크리스트](#14-요약-체크리스트)

---

## 1. JPA 개념 매핑

| Spring Boot / JPA | Python / SQLModel |
| --- | --- |
| `@Entity` 클래스 | `class X(SQLModel, table=True)` |
| `@Id @GeneratedValue` | `id: int \| None = Field(default=None, primary_key=True)` |
| `@Column(nullable=false, length=64)` | `Field(nullable=False, max_length=64)` |
| `EntityManager` | `Session` |
| `JpaRepository<T, ID>` | `session.exec(select(T))` (Repository 인터페이스 없음, 직접 작성) |
| `DataSource` (application.yml) | `create_engine(DATABASE_URL)` |
| `spring.jpa.show-sql=true` | `create_engine(..., echo=True)` |
| `ddl-auto: update` | **없음. 쓰지 말 것** → Alembic |
| Flyway / Liquibase | **Alembic** |
| `V1__init.sql` | `migrations/versions/xxxx_init.py` |
| `flyway_schema_history` 테이블 | `alembic_version` 테이블 |
| `flyway baseline` | `alembic stamp head` |
| `@EntityScan` 패키지 스캔 | `env.py` 에서 **모델을 직접 import** ← 최대 함정 |
| Entity ↔ Request DTO 분리 | 동일하게 분리 권장 |

> **핵심 차이 하나만 기억할 것**
>
> Python 에는 클래스패스 스캐닝이 없습니다. 모델 파일을 `import` 하는 코드가 어딘가에서
> 실행되어야만 그 클래스가 `SQLModel.metadata` 에 등록됩니다.
> import 를 빼먹으면 Alembic 이 "테이블이 하나도 없네" 라고 판단해서
> **`DROP TABLE` 마이그레이션을 생성합니다.**

---

## 2. 최종 파일 배치

```text
backend/
├── alembic.ini                  # ← alembic init 이 생성
├── migrations/                  # ← alembic init 이 생성 (자바의 db/migration)
│   ├── env.py                   #    Alembic 부트스트랩. 여기를 손봐야 함
│   ├── script.py.mako           #    마이그레이션 파일 템플릿. 여기도 손봐야 함
│   └── versions/                #    실제 마이그레이션 스크립트들
├── app/
│   ├── core/
│   │   ├── config.py            # DATABASE_URL
│   │   └── db.py                # engine + get_session (DataSource 역할)
│   └── data/model/
│       ├── __init__.py          # 모델 일괄 export (@EntityScan 대용)
│       └── transaction.py       # 테스트용 엔티티
└── main.py
```

---

## 3. Step 1 — 접속 설정

### `app/core/config.py`

```python
import os

# docker-compose.yml 의 root / 1234 / fdshield-db 와 일치
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://root:1234@localhost:5432/fdshield-db",
)
```

`postgresql+psycopg://` 의 `psycopg` 는 psycopg**3** 드라이버입니다(설치되어 있는 것).
`psycopg2` 로 쓰면 안 됩니다. JDBC URL 의 `jdbc:postgresql://` 자리라고 보면 됩니다.

### `app/core/db.py` — `DataSource` + `EntityManagerFactory` 역할

```python
from collections.abc import Generator

from sqlmodel import Session, create_engine

from app.core.config import DATABASE_URL

# echo=True 는 spring.jpa.show-sql=true 와 동일
engine = create_engine(DATABASE_URL, echo=True, pool_pre_ping=True)


def get_session() -> Generator[Session, None, None]:
    """FastAPI 의존성 주입용. @PersistenceContext EntityManager 에 해당."""
    with Session(engine) as session:
        yield session
```

> ⚠️ 여기서 `SQLModel.metadata.create_all(engine)` 을 **호출하지 마세요.**
> `ddl-auto: update` 와 Flyway 를 동시에 켜는 것과 같아서 Alembic 이력이 꼬입니다.
> 스키마 생성 권한은 전부 Alembic 에 넘깁니다.

---

## 4. Step 2 — 테스트용 모델(엔티티)

기존 `app/dto/transaction.py` 의 `TransactionDTO` 를 영속 엔티티로 옮긴 버전입니다.

### `app/data/model/transaction.py`

```python
from datetime import datetime

from sqlmodel import Field, SQLModel


class Transaction(SQLModel, table=True):
    """거래 원장 테이블. JPA 의 @Entity @Table(name="transactions") 에 해당."""

    __tablename__ = "transactions"

    # @Id @GeneratedValue(strategy = IDENTITY)
    id: int | None = Field(default=None, primary_key=True)

    # @Column(nullable=false, length=64) + @Index
    user_id: str = Field(max_length=64, index=True)

    transaction_time: datetime = Field(index=True)

    amount: int = Field(nullable=False)

    user_amount_std_dev: float = Field(default=0.0)

    payment_method: str = Field(max_length=32)

    merchant_category: str = Field(max_length=64)

    is_fraud: bool = Field(default=False)

    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
```

**JPA 와 다른 점 3가지**

1. **`table=True` 가 없으면 그냥 Pydantic 모델**입니다(테이블이 안 생김).
   `@Entity` 어노테이션을 빠뜨린 것과 같은 효과.
2. `id: int | None` 으로 Optional 이어야 합니다. INSERT 전에는 값이 없고 DB 가 채워주기 때문.
   `Long id;` 가 null 인 상태와 같습니다.
3. `str` 에 `max_length` 를 안 주면 PostgreSQL 에서 `VARCHAR`(길이 무제한)로 생성됩니다.
   JPA `@Column` 의 기본 255 와 다릅니다.

### `app/data/model/__init__.py` — `@EntityScan` 대용

```python
"""모든 SQLModel 엔티티를 여기서 import 해야 SQLModel.metadata 에 등록된다."""

from app.data.model.transaction import Transaction  # noqa: F401

__all__ = ["Transaction"]
```

앞으로 엔티티를 추가할 때마다 **반드시 이 파일에 한 줄 추가**하세요.
이 규칙 하나만 지키면 Alembic 이 엔티티를 못 찾는 사고는 안 납니다.

---

## 5. Step 3 — Alembic 초기화

```bash
docker compose up -d          # DB 부터 띄우고
uv run alembic init migrations
```

`alembic.ini` 와 `migrations/` 디렉터리가 생성됩니다.
Flyway 가 `db/migration` 규약을 쓰는 것과 달리, Alembic 은 이 스캐폴딩을
**직접 수정해서 쓰는** 구조입니다.

---

## 6. Step 4 — `alembic.ini` 수정

```ini
[alembic]
script_location = migrations

# 프로젝트 루트를 sys.path 에 추가 → env.py 에서 app.* import 가능
# (기본값이 이미 '.' 인 경우가 많으니 주석 처리되어 있으면 풀어주세요)
prepend_sys_path = .

# 마이그레이션 파일명에 타임스탬프 → V1__, V2__ 처럼 순서가 눈에 보임
file_template = %%(year)d%%(month).2d%%(day).2d_%%(hour).2d%%(minute).2d_%%(rev)s_%%(slug)s

# sqlalchemy.url 은 비워둡니다. env.py 에서 주입할 것 (비밀번호 커밋 방지)
sqlalchemy.url =
```

`sqlalchemy.url` 을 ini 에 직접 쓰면 비밀번호가 git 에 올라가고,
`%` 문자가 들어있으면 `%%` 로 이스케이프해야 하는 문제도 있습니다.
`env.py` 에서 주입하는 쪽이 안전합니다.

---

## 7. Step 5 — `migrations/env.py` 교체 (핵심)

생성된 파일을 아래로 통째로 바꿉니다.

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from app.core.config import DATABASE_URL

# ★★★ 이 import 가 @EntityScan 역할. 지우면 Alembic 이 DROP TABLE 을 만든다 ★★★
import app.data.model  # noqa: F401

config = context.config

# alembic.ini 의 빈 sqlalchemy.url 을 런타임에 주입
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ★ Hibernate 의 "현재 엔티티 매핑 전체". autogenerate 의 비교 기준이 된다.
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """DB 접속 없이 SQL 스크립트만 출력 (alembic upgrade head --sql)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """실제 DB 에 접속해서 마이그레이션 적용."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,             # 컬럼 타입 변경도 감지
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`compare_type` / `compare_server_default` 는 기본값이 꺼져 있어서,
켜지 않으면 `VARCHAR(64) → VARCHAR(128)` 같은 변경을 autogenerate 가 조용히 무시합니다.

---

## 8. Step 6 — `script.py.mako` 수정 (SQLModel 필수)

SQLModel 은 `str` 필드를 `sqlmodel.sql.sqltypes.AutoString` 이라는 자체 타입으로 매핑합니다.
그래서 autogenerate 가 만든 마이그레이션 파일에 `sqlmodel.sql.sqltypes.AutoString()` 이 등장하는데,
템플릿에는 `import sqlmodel` 이 없어서 다음 에러로 터집니다:

```text
NameError: name 'sqlmodel' is not defined
```

`migrations/script.py.mako` 상단을 이렇게 고쳐두면 이후 생성되는 모든 파일에 자동 반영됩니다.

```mako
from alembic import op
import sqlalchemy as sa
import sqlmodel                    # ← 이 한 줄 추가
${imports if imports else ""}
```

SQLModel + Alembic 조합에서 가장 많이 겪는 에러이니 **초기화 직후 바로** 해두세요.

---

## 9. Step 7 — 마이그레이션 생성 & 적용

```bash
# Hibernate 가 ddl-auto=validate 로 비교하던 걸, 명시적으로 파일로 뽑는 단계
uv run alembic revision --autogenerate -m "create transactions table"
```

`migrations/versions/20260731_1630_a1b2c3d4e5f6_create_transactions_table.py` 가 생성됩니다.

```python
"""create transactions table

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-07-31 16:30:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = None          # ← Flyway 버전 순서에 해당하는 링크드리스트
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("transaction_time", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("user_amount_std_dev", sa.Float(), nullable=False),
        sa.Column("payment_method", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("merchant_category", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("is_fraud", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_transactions_user_id"), "transactions", ["user_id"])
    op.create_index(op.f("ix_transactions_transaction_time"), "transactions", ["transaction_time"])


def downgrade() -> None:
    op.drop_index(op.f("ix_transactions_transaction_time"), table_name="transactions")
    op.drop_index(op.f("ix_transactions_user_id"), table_name="transactions")
    op.drop_table("transactions")
```

> **생성된 파일은 항상 눈으로 검토하세요.** autogenerate 는 초안 생성기일 뿐입니다.
> ([자주 걸리는 함정](#12-자주-걸리는-함정) 참고)

검토가 끝나면 적용:

```bash
uv run alembic upgrade head        # flyway migrate
uv run alembic current             # flyway info (현재 리비전)
uv run alembic history --verbose   # 마이그레이션 이력
uv run alembic downgrade -1        # 한 단계 롤백 (Flyway 무료판엔 없는 기능)
```

DB 에서 직접 확인:

```bash
docker compose exec db psql -U root -d fdshield-db -c '\d transactions'
docker compose exec db psql -U root -d fdshield-db -c 'SELECT * FROM alembic_version;'
```

`alembic_version` 테이블에 리비전 해시 한 줄이 들어있으면 **연동 성공**입니다.
이게 `flyway_schema_history` 에 해당합니다.

---

## 10. Step 8 — FastAPI에서 CRUD 검증

### `main.py` 에 추가할 코드

```python
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from sqlmodel import Session, SQLModel, select

from app.core.db import get_session
from app.data.model.transaction import Transaction

app = FastAPI()

SessionDep = Annotated[Session, Depends(get_session)]


class TransactionCreate(SQLModel):
    """요청 전용 DTO. 엔티티를 그대로 노출하면 id 를 클라이언트가 주입할 수 있다."""

    user_id: str
    transaction_time: datetime
    amount: int
    user_amount_std_dev: float = 0.0
    payment_method: str
    merchant_category: str


@app.post("/transactions", response_model=Transaction)
def create_transaction(payload: TransactionCreate, session: SessionDep) -> Transaction:
    tx = Transaction.model_validate(payload)   # DTO → Entity 매핑 (ModelMapper 역할)
    session.add(tx)                            # em.persist()
    session.commit()                           # 트랜잭션 커밋
    session.refresh(tx)                        # DB 가 채운 id 를 다시 읽어옴
    return tx


@app.get("/transactions", response_model=list[Transaction])
def list_transactions(session: SessionDep) -> list[Transaction]:
    # SELECT * FROM transactions ORDER BY id DESC LIMIT 20
    stmt = select(Transaction).order_by(Transaction.id.desc()).limit(20)
    return list(session.exec(stmt).all())


@app.get("/transactions/{tx_id}", response_model=Transaction)
def get_transaction(tx_id: int, session: SessionDep) -> Transaction:
    tx = session.get(Transaction, tx_id)       # em.find(Transaction.class, id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    return tx
```

### 실행 & 호출

```bash
uv run uvicorn main:app --reload

curl -X POST localhost:8000/transactions \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"USR_100123","transaction_time":"2026-07-31T16:00:00",
       "amount":4500000,"user_amount_std_dev":3.2,
       "payment_method":"CARD","merchant_category":"LUXURY"}'

curl localhost:8000/transactions
```

`echo=True` 덕분에 콘솔에 실제 발행되는 SQL 이 찍힙니다.
`http://localhost:8000/docs` 에서 Swagger UI 로도 확인 가능합니다.

---

## 11. Step 9 — 스키마 변경 재연습

`ddl-auto: update` 를 대체하는 사이클을 한 번 더 돌려봅니다.

모델에 컬럼 추가:

```python
    risk_grade: str | None = Field(default=None, max_length=16, index=True)
```

```bash
uv run alembic revision --autogenerate -m "add risk_grade to transactions"
uv run alembic upgrade head
```

생성되는 내용:

```python
def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("risk_grade", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True),
    )
    op.create_index(op.f("ix_transactions_risk_grade"), "transactions", ["risk_grade"])
```

**모델 수정 → revision → 리뷰 → upgrade → 커밋** 이 사이클이 팀 협업 방식입니다.
마이그레이션 파일은 **반드시 모델 변경과 같은 커밋에** 올리세요.

---

## 12. 자주 걸리는 함정

1. **`env.py` 에서 모델 import 누락**
   → autogenerate 가 빈 마이그레이션 또는 `drop_table()` 을 생성합니다.
   `app/data/model/__init__.py` 에 전부 모아두는 이유가 이것입니다.

2. **`script.py.mako` 에 `import sqlmodel` 누락**
   → `NameError: name 'sqlmodel' is not defined`

3. **`create_all()` 과 Alembic 혼용**
   → 앱이 먼저 테이블을 만들면 `alembic upgrade` 가 `DuplicateTable` 로 실패합니다.
   `create_all` 은 테스트(SQLite in-memory)에서만 쓰세요.

4. **autogenerate 가 못 잡는 것들** — 반드시 손으로 수정
   - 컬럼 **rename** 을 `drop_column` + `add_column` 으로 인식 → **데이터가 날아갑니다.**
     `op.alter_column(..., new_column_name=...)` 로 직접 고쳐야 합니다.
   - `nullable=True → False` 변경 시 기존 행 백필(backfill) SQL 을 안 넣어줌
   - Python `Enum` 필드는 PG enum 타입을 만들지만 `downgrade()` 에 `DROP TYPE` 을 안 넣어줌
   - 테이블/컬럼명 대소문자, `CHECK` 제약, 뷰, 트리거

5. **`alembic_version` 은 리비전 한 줄만 저장**
   → 이미 운영 중인 DB 에 Alembic 을 도입할 땐 `alembic stamp head` 로
   "여기까진 적용된 걸로 친다" 고 표시합니다 (Flyway `baseline`).

6. **제약조건 이름이 DB 자동 생성이라 downgrade 가 깨질 수 있음**
   → 초기에 naming convention 을 박아두는 걸 권장합니다. 모델 정의 전에 한 번만:

   ```python
   SQLModel.metadata.naming_convention = {
       "ix": "ix_%(column_0_label)s",
       "uq": "uq_%(table_name)s_%(column_0_name)s",
       "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
       "pk": "pk_%(table_name)s",
   }
   ```

7. **N+1 과 지연 로딩**
   → SQLModel 의 `Relationship` 은 JPA 와 달리 기본이 lazy 이고,
   세션이 닫힌 뒤 접근하면 예외가 납니다.
   `selectinload()` 로 명시적 즉시 로딩을 걸어야 합니다 (`@EntityGraph` 에 해당).

---

## 13. 보너스 — pgvector 컬럼

paradedb 를 쓰고 있으니 벡터 컬럼도 같은 흐름으로 됩니다.
SQLModel 의 `Field` 로는 표현이 안 되므로 `sa_column` 으로 SQLAlchemy 컬럼을 직접 넘깁니다.

### `app/data/model/fss_document.py`

```python
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


class FssDocument(SQLModel, table=True):
    __tablename__ = "fss_documents"

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=200)
    content: str
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(1536)),   # OpenAI text-embedding-3-small 차원
    )
```

이때 생성되는 마이그레이션에는 `import pgvector` 가 자동으로 안 들어가므로
**파일 상단에 손으로 추가**해야 합니다.

```python
import pgvector.sqlalchemy   # ← 직접 추가


def upgrade() -> None:
    op.create_table(
        "fss_documents",
        ...
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1536), nullable=True),
    )
    # HNSW 인덱스는 autogenerate 가 못 만드니 직접 작성
    op.execute(
        "CREATE INDEX ix_fss_documents_embedding ON fss_documents "
        "USING hnsw (embedding vector_cosine_ops)"
    )
```

`CREATE EXTENSION vector` 는 이미 `docker/activate-vector-extension.sql` 에서 처리되지만,
컨테이너를 새로 만들지 않는 환경(운영 DB 등)도 있으니
첫 마이그레이션의 `upgrade()` 맨 앞에 아래를 넣어두면 더 안전합니다.

```python
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
```

---

## 14. 요약 체크리스트

```bash
docker compose up -d
uv run alembic init migrations
#   → alembic.ini      : prepend_sys_path=. / sqlalchemy.url 비우기 / file_template
#   → migrations/env.py: import app.data.model + target_metadata = SQLModel.metadata
#   → script.py.mako   : import sqlmodel 추가
uv run alembic revision --autogenerate -m "create transactions table"
#   → 생성된 파일 눈으로 검토 (필수)
uv run alembic upgrade head
docker compose exec db psql -U root -d fdshield-db -c '\d transactions'
```

| 단계 | 잊으면 생기는 일 |
| --- | --- |
| `prepend_sys_path = .` | `ModuleNotFoundError: No module named 'app'` |
| `import app.data.model` in env.py | 빈 마이그레이션 or `DROP TABLE` 생성 |
| `import sqlmodel` in script.py.mako | `NameError: name 'sqlmodel' is not defined` |
| 생성 파일 리뷰 | 컬럼 rename 이 drop+add 로 나가서 데이터 유실 |
| `create_all()` 제거 | `DuplicateTable` 로 upgrade 실패 |

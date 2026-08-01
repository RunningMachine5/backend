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
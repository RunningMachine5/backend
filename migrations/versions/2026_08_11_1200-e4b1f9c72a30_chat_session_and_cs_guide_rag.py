"""chat session and cs guide rag

챗봇 영역 스키마를 정리한다.

* fraud_type_score_after_chat: 유형별 점수 맵(additional_type_scores) 대신
  대표 유형의 우세 점수(primary_fraud_type_score) 하나만 남긴다.
* agent_chat_sessions: 마지막 메시지 캐시(last_message_id)와 노인 전용 UI
  여부(is_older) 추가
* cs_guide_documents / cs_guide_document_chunks: 고객 챗봇 RAG 고객대응가이드
  적재 전용 테이블 신규 생성 (documents와 달리 document_key/audience/metadata/
  updated_at 없음. 대신 출처 표기용 page 컬럼을 둔다)

Revision ID: e4b1f9c72a30
Revises: e8c4a1d7f290
Create Date: 2026-08-11 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "e4b1f9c72a30"
# 문서 복사 단계가 e8c4a1d7f290이 바꿔 놓은 documents 컬럼(audiences/
# fraud_types)을 읽으므로 그 뒤에 와야 한다. 분기로 두면 실행 순서가
# 보장되지 않아 새 DB에서 깨진다.
down_revision: Union[str, Sequence[str], None] = "e8c4a1d7f290"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# docs_embedding이 쓰는 OpenAI text-embedding-3-small 차원
EMBEDDING_DIM = 1536


def upgrade() -> None:
    _upgrade_chat_sessions()
    _upgrade_fraud_type_score_after_chat()
    _create_cs_guide_tables()
    _copy_documents_into_cs_guide()


def downgrade() -> None:
    _drop_cs_guide_tables()
    _downgrade_fraud_type_score_after_chat()
    _downgrade_chat_sessions()


# ------------------------------------------------------- agent_chat_sessions


def _upgrade_chat_sessions() -> None:
    op.add_column(
        "agent_chat_sessions",
        sa.Column("last_message_id", sa.BigInteger(), nullable=True),
    )
    # agent_chat_messages가 세션을 참조하는 반대 방향 FK도 있어 순환 참조다.
    # 메시지가 지워져도 세션은 남아야 하므로 SET NULL로 끊는다.
    op.create_foreign_key(
        "fk_agent_chat_sessions_last_message_id",
        "agent_chat_sessions",
        "agent_chat_messages",
        ["last_message_id"],
        ["message_id"],
        ondelete="SET NULL",
    )

    # 기존 행을 채워야 NOT NULL을 걸 수 있어 server_default로 넣고 바로 뗀다.
    # (모델이 기본값을 갖고 있어 스키마에 default를 남기면 autogenerate가 흔들린다)
    op.add_column(
        "agent_chat_sessions",
        sa.Column(
            "is_older",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("agent_chat_sessions", "is_older", server_default=None)


def _downgrade_chat_sessions() -> None:
    op.drop_column("agent_chat_sessions", "is_older")
    op.drop_constraint(
        "fk_agent_chat_sessions_last_message_id",
        "agent_chat_sessions",
        type_="foreignkey",
    )
    op.drop_column("agent_chat_sessions", "last_message_id")


# ----------------------------------------------- fraud_type_score_after_chat


def _upgrade_fraud_type_score_after_chat() -> None:
    op.add_column(
        "fraud_type_score_after_chat",
        sa.Column("primary_fraud_type_score", sa.Float(), nullable=True),
    )
    # 유형별 점수 맵은 더 이상 쓰지 않는다. 대표 유형 점수로는 복원 불가능.
    op.drop_column("fraud_type_score_after_chat", "additional_type_scores")


def _downgrade_fraud_type_score_after_chat() -> None:
    op.add_column(
        "fraud_type_score_after_chat",
        sa.Column(
            "additional_type_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.alter_column(
        "fraud_type_score_after_chat",
        "additional_type_scores",
        server_default=None,
    )
    op.drop_column("fraud_type_score_after_chat", "primary_fraud_type_score")


# --------------------------------------------------------------- cs guide RAG


def _create_cs_guide_tables() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "cs_guide_documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=True),
        sa.Column("fraud_type", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "cs_guide_document_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cs_guide_document_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        # 한 페이지가 여러 청크로 쪼개지면 page 값이 반복되므로 chunk_index와
        # 별개 컬럼으로 둔다.
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["cs_guide_document_id"],
            ["cs_guide_documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cs_guide_document_id",
            "chunk_index",
            name="uq_cs_guide_document_chunks_document_id_chunk_index",
        ),
    )
    op.create_index(
        op.f("ix_cs_guide_document_chunks_cs_guide_document_id"),
        "cs_guide_document_chunks",
        ["cs_guide_document_id"],
    )
    # hnsw 인덱스는 alembic이 만들지 못해 원본 DDL로 직접 만든다.
    op.execute(
        "CREATE INDEX ix_cs_guide_document_chunks_embedding_hnsw "
        "ON cs_guide_document_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def _copy_documents_into_cs_guide() -> None:
    """documents/document_chunks에 이미 적재된 고객대응가이드를 옮긴다.

    임베딩 벡터를 그대로 복사하므로 재임베딩(OpenAI 재호출)이 필요 없다.
    모니터링 전용 문서는 고객 챗봇 코퍼스에 섞이면 안 되므로 대상 채널에
    CUSTOMER/COMMON이 포함된 문서만 가져온다. id를 그대로 유지해 청크의 FK
    매핑을 맞추고, 이어지는 INSERT가 충돌하지 않도록 시퀀스를 다시 세팅한다.

    documents.audiences/fraud_types는 e8c4a1d7f290이 단일 문자열에서 JSONB
    배열로 바꿔 놓은 컬럼이다. cs_guide 쪽은 고객 채널 전용이라 채널 목록이
    없고 유형도 단일 값이므로 배열의 첫 원소만 옮긴다.
    """

    op.execute(
        """
        INSERT INTO cs_guide_documents
            (id, title, source, source_type, fraud_type, content, created_at)
        SELECT id, title, source, source_type,
               NULLIF(fraud_types ->> 0, ''),
               content, created_at
        FROM documents
        WHERE audiences @> '"CUSTOMER"'::jsonb
           OR audiences @> '"COMMON"'::jsonb
        """
    )
    op.execute(
        """
        INSERT INTO cs_guide_document_chunks
            (id, cs_guide_document_id, chunk_index, page, content, embedding,
             created_at)
        SELECT id, document_id, chunk_index,
               NULLIF(metadata->>'page', '')::int,
               content, embedding, created_at
        FROM document_chunks
        WHERE document_id IN (SELECT id FROM cs_guide_documents)
        """
    )

    # 명시적 id로 넣었으므로 시퀀스가 1에 멈춰 있다. is_called=false라
    # 다음 nextval이 곧 max(id) + 1이 되고, 원본이 비어 있으면 1이 된다.
    for table in ("cs_guide_documents", "cs_guide_document_chunks"):
        op.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{table}', 'id'),
                COALESCE((SELECT MAX(id) FROM {table}), 0) + 1,
                false
            )
            """
        )


def _drop_cs_guide_tables() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cs_guide_document_chunks_embedding_hnsw")
    op.drop_index(
        op.f("ix_cs_guide_document_chunks_cs_guide_document_id"),
        table_name="cs_guide_document_chunks",
    )
    op.drop_table("cs_guide_document_chunks")
    op.drop_table("cs_guide_documents")

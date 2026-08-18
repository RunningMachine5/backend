"""drop transactions location

`0ef235b` 이 ML 입력에서 지역 이름을 걷어내며 Transaction 모델의 ``location`` 을 지웠지만
마이그레이션이 빠져 있었다. 컬럼이 ``NOT NULL`` 이고 기본값이 없어서, 모델이 그 컬럼을
채우지 못하는 지금은 **모든 거래 INSERT 가 not-null 위반으로 실패한다**
(``POST /transactions`` 포함). 컬럼을 지워 모델과 맞춘다.

거래 위치는 ``location_lat`` / ``location_lon`` 만 남는다. ML 계약의 ``location`` 피처는
[dataset_builder.py](../../app/services/mlops/dataset_builder.py)가 두 좌표에서
``"<lat> <lon>"`` 로 조립하므로 이 컬럼에 의존하지 않는다.

Revision ID: 114319f0f9d6
Revises: b4167d7782e1
Create Date: 2026-08-17 22:46:27.471595

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import pgvector.sqlalchemy    # 임베딩 컬럼 렌더링에 필요
import app.data.model.types   # 프로젝트 커스텀 타입 렌더링에 필요


# revision identifiers, used by Alembic.
revision: str = '114319f0f9d6'
down_revision: Union[str, Sequence[str], None] = 'b4167d7782e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("transactions", "location")


def downgrade() -> None:
    """Downgrade schema."""
    # 지운 값은 되살릴 수 없다. 기존 행을 빈 문자열로 채워 NOT NULL 을 복구한 뒤
    # server_default 를 떼어 원래 정의(기본값 없는 NOT NULL)로 되돌린다.
    op.add_column(
        "transactions",
        sa.Column(
            "location",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.alter_column("transactions", "location", server_default=None)

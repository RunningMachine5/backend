from datetime import datetime

from sqlmodel import Field, SQLModel

"""거래 테이블"""
class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    # @Id @GeneratedValue(strategy = IDENTITY)
    id: int | None = Field(default=None, primary_key=True)

    payment_method: str = Field(max_length=32)

    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
import unittest
from datetime import timedelta

from sqlalchemy import Column
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine, select

from app.data.model.types import INTERVAL_COLUMN


class IntervalProbe(SQLModel, table=True):
    __tablename__ = "test_interval_probe"

    id: int = Field(primary_key=True)
    value: timedelta = Field(sa_column=Column(INTERVAL_COLUMN, nullable=False))


class SQLiteIntervalTypeTest(unittest.TestCase):
    def test_negative_zero_and_positive_values_round_trip(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        IntervalProbe.__table__.create(engine)
        expected = [
            timedelta(seconds=-1),
            timedelta(0),
            timedelta(days=2, hours=3, minutes=4, seconds=5),
        ]
        try:
            with Session(engine) as session:
                session.add_all(
                    [
                        IntervalProbe(id=index, value=value)
                        for index, value in enumerate(expected, start=1)
                    ]
                )
                session.commit()
                session.expire_all()
                actual = list(
                    session.exec(select(IntervalProbe).order_by(IntervalProbe.id)).all()
                )
        finally:
            engine.dispose()

        self.assertEqual([item.value for item in actual], expected)


if __name__ == "__main__":
    unittest.main()

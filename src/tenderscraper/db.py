from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from tenderscraper.config import settings
from tenderscraper.db_models import TenderRecord


connect_args = {"check_same_thread": False} if settings.normalized_database_url.startswith("sqlite") else {}
engine = create_engine(settings.normalized_database_url, echo=False, connect_args=connect_args)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    _ensure_tender_record_columns()


def _ensure_tender_record_columns() -> None:
    table_name = TenderRecord.__table__.name

    with engine.begin() as conn:
        inspector = inspect(conn)
        if table_name not in inspector.get_table_names():
            return

        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        alter_statements: list[str] = []

        if "date" not in existing_columns:
            alter_statements.append(f'ALTER TABLE {table_name} ADD COLUMN "date" DATE')
        if "price" not in existing_columns:
            alter_statements.append(f'ALTER TABLE {table_name} ADD COLUMN "price" TEXT')

        for statement in alter_statements:
            try:
                conn.execute(text(statement))
            except Exception as exc:
                message = str(exc).lower()
                if "already exists" not in message and "duplicate column" not in message:
                    raise


def reset_db() -> None:
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def ping_database() -> bool:
    with Session(engine) as session:
        session.exec(text("SELECT 1"))
    return True

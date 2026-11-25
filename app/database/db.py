import os
from collections.abc import Generator

from fastapi import Request
from sqlalchemy import Engine, event, make_url
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./deanflow_labs.db")


def create_database_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    is_sqlite = url.get_backend_name() == "sqlite"
    options = {}
    if is_sqlite:
        options["connect_args"] = {"check_same_thread": False}
        if url.database not in {None, "", ":memory:", "file::memory:"} and url.query.get("mode") != "memory":
            # Each file-backed SQLite request can use its own cheap connection.
            # A small pool can block the worker threads needed to finish active requests.
            options["poolclass"] = NullPool
    database_engine = create_engine(url, hide_parameters=True, **options)

    if is_sqlite:
        @event.listens_for(database_engine, "connect")
        def _enable_sqlite_foreign_keys(connection, _record) -> None:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.create_function("unicode_casefold", 1, str.casefold, deterministic=True)

    return database_engine


engine = create_database_engine(DATABASE_URL)


def init_db(database_engine: Engine = engine) -> None:
    SQLModel.metadata.create_all(database_engine)


def get_session(request: Request) -> Generator[Session, None, None]:
    with Session(request.app.state.database_engine) as session:
        yield session

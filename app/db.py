"""DB接続。SQLiteの外部キー有効・WAL・busy timeout（仕様第3章）。

SQLAlchemy の SQLite 方言は既定では DBAPI 任せの暗黙 BEGIN を使い、
BEGIN IMMEDIATE を発行できない。仕様第8章の claim（BEGIN IMMEDIATE＋条件付きUPDATE）を
A2 で実装できるようにするため、公式資料の推奨手順どおり DBAPI を autocommit にし、
BEGIN を SQLAlchemy 側で明示的に発行する。
https://docs.sqlalchemy.org/en/20/dialects/sqlite.html
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    # DBAPI 側の暗黙トランザクションを止める（BEGIN は SQLAlchemy が出す）
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def _emit_begin(conn: Connection) -> None:
    conn.exec_driver_sql("BEGIN")


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            future=True,
            connect_args={"check_same_thread": False},
        )
        event.listen(_engine, "connect", _configure_sqlite)
        event.listen(_engine, "begin", _emit_begin)
        _session_factory = sessionmaker(bind=_engine, future=True, expire_on_commit=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def reset_engine() -> None:
    """テストで設定を差し替えたときに使う。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def begin_immediate(session: Session) -> None:
    """書込トランザクションを即座に取得する（仕様第8章）。

    A2 の claim で使う。呼ぶ側は、このあとネットワーク通信をしないこと。
    """
    session.execute(text("COMMIT"))
    session.execute(text("BEGIN IMMEDIATE"))


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_session() -> Iterator[Session]:
    """FastAPI依存。"""
    with session_scope() as session:
        yield session

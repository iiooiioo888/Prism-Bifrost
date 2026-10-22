"""工作階段與僅追加審計觸發器。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from prism.db.models import Base

_SQLITE_AUDIT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;
"""


def make_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, future=True, connect_args=connect_args)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


_SQLITE_ADDED_COLUMNS = {
    "product_monitor": (("next_daily_at", "TEXT"), ("confirm_at", "TEXT")),
    "product_monitor_hit": (
        ("sku_code", "TEXT"),
        ("match_confidence", "TEXT"),
        ("match_band", "TEXT"),
        ("availability", "TEXT"),
        ("shipping_price", "TEXT"),
        ("seller", "TEXT"),
    ),
    "sku_offer": (
        ("availability", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("shipping_price", "NUMERIC(12,2)"),
        ("seller", "TEXT"),
    ),
}


_POSTGRES_ADDED_COLUMNS = (
    "ALTER TABLE sku_offer ADD COLUMN IF NOT EXISTS availability TEXT NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE sku_offer ADD COLUMN IF NOT EXISTS shipping_price NUMERIC(12,2)",
    "ALTER TABLE sku_offer ADD COLUMN IF NOT EXISTS seller TEXT",
    "ALTER TABLE product_monitor_hit ADD COLUMN IF NOT EXISTS availability TEXT",
    "ALTER TABLE product_monitor_hit ADD COLUMN IF NOT EXISTS shipping_price TEXT",
    "ALTER TABLE product_monitor_hit ADD COLUMN IF NOT EXISTS seller TEXT",
)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            connection.connection.executescript(_SQLITE_AUDIT_TRIGGER)
            for table, columns in _SQLITE_ADDED_COLUMNS.items():
                present = {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))}
                for column, column_type in columns:
                    if column not in present:
                        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))
    elif engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            for statement in _POSTGRES_ADDED_COLUMNS:
                connection.execute(text(statement))


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

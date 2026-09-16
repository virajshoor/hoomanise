import os
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        kwargs = dict(
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            open=True,
            timeout=10,
            max_lifetime=600,
            max_idle=60,
            reconnect_timeout=10,
        )
        try:
            _pool = ConnectionPool(
                settings.database_url,
                check=ConnectionPool.check_connection,
                **kwargs,
            )
        except TypeError:
            _pool = ConnectionPool(settings.database_url, **kwargs)
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


_TRANSIENT = (psycopg.OperationalError, psycopg.InterfaceError)


def query(sql: str, params: tuple | dict | None = None, one: bool = False):
    try:
        return _query(sql, params, one)
    except _TRANSIENT:
        _reset_pool()
        return _query(sql, params, one)


def execute(sql: str, params: tuple | dict | None = None) -> None:
    try:
        _execute(sql, params)
    except _TRANSIENT:
        _reset_pool()
        _execute(sql, params)


def _reset_pool():
    global _pool
    try:
        if _pool is not None:
            _pool.close()
    except Exception:
        pass
    _pool = None


def _query(sql: str, params: tuple | dict | None = None, one: bool = False):
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
    if one:
        return rows[0] if rows else None
    return rows


def _execute(sql: str, params: tuple | dict | None = None) -> None:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


def migrate() -> list[str]:
    conninfo = os.environ.get("MIGRATION_DATABASE_URL") or settings.database_url
    if not conninfo:
        raise RuntimeError("DATABASE_URL (or MIGRATION_DATABASE_URL) is not configured")
    applied: list[str] = []
    with psycopg.connect(conninfo, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur.execute("SELECT version FROM schema_migrations")
            existing = {r[0] for r in cur.fetchall()}
            conn.commit()
            files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))
            for path in files:
                version = path.stem
                if version in existing:
                    continue
                sql = path.read_text(encoding="utf-8")
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
                )
                conn.commit()
                applied.append(version)
    return applied


def new_id() -> uuid.UUID:
    return uuid.uuid4()

import os
import sqlite3
import threading

_local = threading.local()


def _get_database_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def _is_postgres() -> bool:
    return _get_database_url().startswith("postgres")


def get_db():
    """Get a thread-local database connection. Uses Postgres if DATABASE_URL is set, SQLite otherwise."""
    if hasattr(_local, "connection") and _local.connection is not None:
        return _local.connection

    if _is_postgres():
        import psycopg2
        import psycopg2.extras
        # Railway uses postgres:// but psycopg2 needs postgresql://
        url = _get_database_url().replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url)
        conn.autocommit = False
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        _local.connection = _PgConnectionWrapper(conn)
    else:
        db_path = os.environ.get(
            "DATABASE_PATH",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "capaco.db"),
        )
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.connection = conn

    return _local.connection


class _PgConnectionWrapper:
    """Wraps a psycopg2 connection to provide a sqlite3-compatible interface.
    Converts ? placeholders to %s and makes cursor results behave like sqlite3.Row."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, params or ())
        return _PgCursorWrapper(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


class _PgCursorWrapper:
    """Wraps a psycopg2 RealDictCursor to provide fetchone/fetchall with key access."""

    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def lastrowid(self):
        return self._cursor.fetchone()[0] if self._cursor.description else None

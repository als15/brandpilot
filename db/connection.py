import logging
import os
import sqlite3
import threading

_local = threading.local()
log = logging.getLogger("capaco")


def _get_database_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def _is_postgres() -> bool:
    return _get_database_url().startswith("postgres")


def _is_connection_alive(conn) -> bool:
    """Check if a cached connection is still usable."""
    try:
        if isinstance(conn, _PgConnectionWrapper):
            conn._conn.cursor().execute("SELECT 1")
        else:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _create_pg_connection():
    """Create a new Postgres connection with timeouts."""
    import psycopg2
    import psycopg2.extras
    url = _get_database_url().replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url, connect_timeout=10)
    conn.autocommit = False
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    # Prevent queries from hanging indefinitely (120s max)
    conn.cursor().execute("SET statement_timeout = '120000'")
    conn.commit()
    return _PgConnectionWrapper(conn)


def _create_sqlite_connection():
    """Create a new SQLite connection."""
    db_path = os.environ.get(
        "DATABASE_PATH",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "capaco.db"),
    )
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db():
    """Get a thread-local database connection. Uses Postgres if DATABASE_URL is set, SQLite otherwise.
    Automatically reconnects if the cached connection is dead."""
    if hasattr(_local, "connection") and _local.connection is not None:
        if _is_connection_alive(_local.connection):
            return _local.connection
        # Connection is dead — reconnect
        log.warning("Database connection lost, reconnecting...")
        try:
            _local.connection.close()
        except Exception:
            pass
        _local.connection = None

    if _is_postgres():
        _local.connection = _create_pg_connection()
    else:
        _local.connection = _create_sqlite_connection()

    return _local.connection


class _PgConnectionWrapper:
    """Wraps a psycopg2 connection to provide a sqlite3-compatible interface.
    Converts ? placeholders to %s and makes cursor results behave like sqlite3.Row."""

    def __init__(self, conn):
        self._conn = conn

    @staticmethod
    def _translate_sql(sql):
        """Convert SQLite date() calls to Postgres equivalents."""
        import re
        sql = sql.replace("?", "%s")
        # date('now') -> CURRENT_DATE
        sql = sql.replace("date('now')", "CURRENT_DATE")
        # date('now', '-7 days') -> CURRENT_DATE - INTERVAL '7 days'
        sql = re.sub(
            r"date\('now',\s*'(-?\d+)\s+days?'\)",
            r"CURRENT_DATE + INTERVAL '\1 days'",
            sql,
        )
        return sql

    def execute(self, sql, params=None):
        sql = self._translate_sql(sql)
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

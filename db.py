"""
One Postgres connection pool, shared by the two services that need storage.

WHY THIS FILE EXISTS
--------------------
`auth_service.py` and `learning_service.py` each opened their own SQLite file
and each owned its own connection handling. That was fine when a connection
was a file handle. Against a hosted Postgres it is not: connections are a
budgeted resource, the instance restarts, and two independent pools against
one database is two ways to exhaust the same budget. So connection handling
moves here and both services borrow from it.

Neither service learns anything about the other by doing so. They still own
their own schemas and their own queries; this file knows nothing except how to
hand out a connection.

LAZY, ON PURPOSE
----------------
The pool is opened on first use rather than at import. Importing `app.py`
without a database reachable has to keep working - the test suites do it, and
so does anything that only wants to read a constant. The failure then happens
on the first real query, with a message that says what is missing, instead of
at import with a stack trace pointing at the wrong thing.

CONFIGURATION
-------------
`DATABASE_URL` - the Neon connection string. Use the **pooled** endpoint (the
host containing `-pooler`), not the direct one: this backend runs on a Render
instance that restarts, and direct connections would be left to time out
against Neon's own limit rather than being reused.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import secrets
from typing import Optional

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Small on purpose. The backend is a single process on a free instance, and
# Neon's free plan shares a connection budget across the project - a large
# pool buys nothing here and costs headroom.
POOL_MAX = int(os.environ.get("DATABASE_POOL_MAX", "5"))

# Which Postgres schema everything reads and writes. Production leaves this
# alone and gets `public`.
#
# It exists for the test suites. They drive the real app through TestClient,
# and since guests write real rows now, a full run leaves a scatter of guest
# games behind - in the LIVE database, which is both noise in anyone's data
# and a slow leak. Pointing a run at its own schema makes the whole run
# disposable: create it, run, drop it. See CLAUDE.md section 6 for the
# incantation.
SCHEMA = os.environ.get("DATABASE_SCHEMA", "public")

_pool = None


class DatabaseUnavailable(RuntimeError):
    """Raised when there is no DATABASE_URL to connect with."""


def configured() -> bool:
    """Whether a database is configured at all. Callers that must degrade
    gracefully rather than fail - guest play, the learning panel - check this
    instead of catching an exception per query."""
    return bool(DATABASE_URL)


def get_pool():
    if _pool is None:
        _open_pool()
    return _pool


def _open_pool() -> None:
    global _pool
    if not DATABASE_URL:
        raise DatabaseUnavailable(
            "DATABASE_URL is not set - there is nowhere to read or write. "
            "Set it to the Neon *pooled* connection string."
        )
    from psycopg_pool import ConnectionPool

    def _configure(conn):
        # Pin the schema on every checkout, and never assume it is already
        # right. Neon's pooler multiplexes client connections onto shared
        # server ones, so a `SET search_path` issued by anything else - a test
        # using a temporary schema, a psql session, another service - can
        # still be in force on the connection we are handed. Setting it here
        # is idempotent, costs one round trip on checkout, and makes that
        # class of contamination self-healing rather than a mystery outage.
        conn.execute(f'SET search_path TO "{SCHEMA}"')

    _pool = ConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=POOL_MAX,
        open=True,
        configure=_configure,
        # Autocommit because every write here is a single statement or an
        # explicit `with conn.transaction()`. Without it psycopg opens a
        # transaction on the first statement and holds it until commit,
        # which on a pooled connection means holding a server-side
        # transaction open across the whole checkout.
        kwargs={"autocommit": True},
    )
    # The pool runs non-daemon worker threads, so an interpreter that exits
    # without closing it dies during finalization with
    # "cannot join thread at interpreter shutdown" - printed AFTER a test
    # script has already reported its results, which makes a clean run look
    # like a crash. This is the same class of trap as the Stockfish handle in
    # CLAUDE.md section 4, and gets the same treatment: closed automatically,
    # so no caller has to remember.
    atexit.register(close_pool)
    logger.info("🗄️ Postgres pool opened")


def connection():
    """A pooled connection, as a context manager."""
    return get_pool().connection()


def drop_schema() -> None:
    """Delete the schema this process was pointed at. Refuses `public`.

    The other half of `DATABASE_SCHEMA`: a test run calls this on the way out
    so nothing it wrote survives. The refusal is not paranoia for its own
    sake - the difference between a disposable run and deleting the live
    database is one unset environment variable.
    """
    if SCHEMA == "public":
        raise RuntimeError("Refusing to drop the public schema")
    with connection() as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    logger.info(f"🗄️ Dropped schema {SCHEMA}")


def close_pool() -> None:
    """Return connections to Neon on shutdown, rather than leaving the pool's
    worker threads to interpreter finalization. Idempotent."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
            logger.info("🗄️ Postgres pool closed")
        except Exception as e:
            logger.warning(f"⚠️ Failed to close the Postgres pool: {e}")
        finally:
            _pool = None


MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


def _migration_files() -> list:
    """Every migration, in the order their filenames sort.

    Sorted by name, so the numeric prefix IS the order. That is the whole
    versioning scheme, and it is deliberately not a framework: the app has one
    database, one deployment, and no need for down-migrations or branching
    version graphs. What it does need is that two machines applying the same
    directory end up with the same schema, which sorting a fixed set of files
    gives for free.
    """
    if not os.path.isdir(MIGRATIONS_DIR):
        return []
    return sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql"))


def applied_migrations(conn=None) -> set:
    """Versions already recorded as applied."""
    def _read(c):
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY, applied_at double precision NOT NULL)"
        )
        return {r[0] for r in c.execute("SELECT version FROM schema_migrations").fetchall()}

    if conn is not None:
        return _read(conn)
    with connection() as c:
        return _read(c)


def migrate() -> list:
    """
    Bring the database up to date. Returns the versions applied this call.

    Properties that matter, and how each is achieved:

    * **Deterministic** - the same directory produces the same schema, because
      the order is the filename order and every file is static SQL.
    * **Idempotent** - a version already in `schema_migrations` is skipped, and
      every migration is additionally written with IF NOT EXISTS so that even
      a lost ledger re-applies harmlessly rather than erroring.
    * **Fails safely** - each migration runs inside its own transaction
      together with the INSERT that records it, so a migration either applies
      completely and is recorded, or does neither. A failure stops the run
      rather than pressing on into a migration that assumed the failed one
      landed.
    * **Non-destructive** - migrations only ever add. There is no DROP or
      destructive ALTER in this directory, which is a rule to keep: dropping a
      column of real user history is not something to do from a deploy hook.

    Safe to call on every boot, which is how a fresh Neon branch gets its
    schema without a manual step.
    """
    import time

    applied_now = []
    with connection() as conn:
        # CREATE SCHEMA first when pointed somewhere other than public, so a
        # test run does not have to create its own before migrating into it.
        if SCHEMA != "public":
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
            conn.execute(f'SET search_path TO "{SCHEMA}"')
        done = applied_migrations(conn)
        for name in _migration_files():
            version = name[:-len(".sql")]
            if version in done:
                continue
            with open(os.path.join(MIGRATIONS_DIR, name), "r", encoding="utf-8") as f:
                sql = f.read()
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                    (version, time.time()),
                )
            applied_now.append(version)
            logger.info(f"🗄️ Applied migration {version}")
    if applied_now:
        logger.info(f"🗄️ Migrated {SCHEMA}: {len(applied_now)} new migration(s)")
    else:
        logger.info(f"🗄️ Schema up to date ({SCHEMA})")
    return applied_now


# The old name, kept because several call sites and tests read better with it
# and because "apply the schema" is what the caller actually wants done.
apply_schema = migrate


def direct_dsn() -> str:
    """
    DATABASE_URL against Neon's direct (unpooled) endpoint.

    Neon names the two endpoints identically apart from a `-pooler` suffix on
    the host, so the direct one is derivable rather than a second variable to
    configure and keep in step. Anything that needs a session setting to stay
    on its own connection has to use this - see `temporary_schema()`.
    """
    return DATABASE_URL.replace("-pooler.", ".", 1)


@contextlib.contextmanager
def temporary_schema(prefix: str = "zwtest"):
    """
    An isolated, disposable copy of the schema, for tests.

    This is what `AuthService(db_path=tmpfile)` used to be. A test that
    creates accounts must not create them in the real database, and with
    SQLite a temporary file was the whole answer. Postgres has no temporary
    file, so the equivalent is a temporary *schema*: same server, same
    connection string, its own set of tables, dropped on the way out.

    Yields a zero-argument callable that returns a connection already pointed
    at that schema - the shape `AuthService(connect=...)` expects.

    These connections go to Neon's DIRECT endpoint, not the pooled one, and
    that is load-bearing rather than a preference. `SET search_path` is a
    session setting, and Neon's pooler multiplexes many client connections
    onto few server connections - so the setting outlives the client that
    issued it and is handed to whoever borrows that server connection next.
    Observed exactly that: after one test run, pooled connections were still
    pointed at a schema the test had already dropped, and every query against
    them failed with "relation does not exist". The direct endpoint gives a
    real connection per client, where a session setting means what it says.
    """
    import psycopg

    name = f"{prefix}_{secrets.token_hex(6)}"
    direct_url = direct_dsn()

    def connect():
        conn = psycopg.connect(direct_url, autocommit=True)
        # SET rather than the `options` connection parameter, which Neon's
        # endpoints do not forward.
        conn.execute(f'SET search_path TO "{name}"')
        return conn

    with connection() as conn:
        conn.execute(f'CREATE SCHEMA "{name}"')
    try:
        import time as _time
        with connect() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY, applied_at double precision NOT NULL)"
            )
            for fname in _migration_files():
                with open(os.path.join(MIGRATIONS_DIR, fname), "r", encoding="utf-8") as f:
                    c.execute(f.read())
                c.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                    (fname[:-len(".sql")], _time.time()),
                )
        yield connect
    finally:
        with connection() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')

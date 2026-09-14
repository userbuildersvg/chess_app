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
`DATABASE_URL` - the Neon connection string. Either endpoint may be given;
this module talks to the **direct** one regardless, deriving it by dropping
the `-pooler` suffix (`direct_dsn()`).

WHY NOT THE POOLER - THIS IS NOT A PREFERENCE
---------------------------------------------
It used to use the pooled endpoint, on the reasoning that a restarting Render
instance would otherwise leave connections to time out. The reasoning was
fine and the conclusion was wrong: Neon's pooler multiplexes clients onto
shared server connections, and session state travels with them. A
`SET search_path` issued by anything else against the same project - a test
run, a psql session, a migration - stays on that server connection and is
handed to whoever borrows it next.

Not theoretical. It was found by an account that was created successfully,
answered a login, and then could not be found: it had been written into a
*test* schema by a process configured for `public`, because the connection it
was handed carried someone else's `search_path`. A health check says nothing
about this - `SELECT 1` works in any schema.

Measured on this project: six fresh connections through the pooler, six
carrying a leaked `search_path`; six through the direct endpoint, all clean,
and a deliberate `SET` on one direct connection bled into none of the next
six. Neon's pooler also refuses a startup `options=-c search_path=...`, so
pinning it at connect time is not available as a workaround.

The cost of going direct is real, small and bounded: one process holding at
most `DATABASE_POOL_MAX` (5) long-lived connections, which is the case a
pooler is *least* needed for - it exists for many short-lived serverless
clients. `close_pool()` runs on shutdown and via atexit, so a restart returns
them rather than orphaning them.

**Do not switch this back to the pooler** without solving `search_path`
first. Silent cross-schema writes are worse than connection churn.
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

# How long anything may wait on the database before it is an error rather
# than a hang. A Neon compute that suspends, or a WSL/Render network blip
# that drops the route, used to leave a request blocked on a socket that
# would never answer - the port stayed open, `curl` timed out, and every
# request behind it queued on the pool until the process was killed by PID
# (CLAUDE.md section 4, trap 15). Each of these bounds one way that happens:
#
#   connect     - opening a connection (includes a suspended compute waking)
#   acquire     - waiting for a free pool slot
#   statement   - one query, enforced server-side
#   tcp user    - unacknowledged data on an established socket, enforced by
#                 the kernel; this is the one that catches a vanished peer
#
# Seconds for the first three, milliseconds for the last, all overridable.
CONNECT_TIMEOUT = int(os.environ.get("DATABASE_CONNECT_TIMEOUT", "10"))
ACQUIRE_TIMEOUT = float(os.environ.get("DATABASE_ACQUIRE_TIMEOUT", "10"))
STATEMENT_TIMEOUT_MS = int(os.environ.get("DATABASE_STATEMENT_TIMEOUT_MS", "20000"))
TCP_USER_TIMEOUT_MS = int(os.environ.get("DATABASE_TCP_USER_TIMEOUT_MS", "15000"))
# Neon's direct endpoint drops connections idle for a few minutes; recycle
# ours first so the pool never hands out one the server has already closed.
MAX_IDLE = float(os.environ.get("DATABASE_MAX_IDLE", "240"))
MAX_LIFETIME = float(os.environ.get("DATABASE_MAX_LIFETIME", "1800"))

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


def is_transient(exc: BaseException) -> bool:
    """Whether `exc` is the database being unreachable, slow or missing -
    the cases a caller should answer with "try again in a moment" rather
    than a traceback. Import-free so the check costs nothing when no
    database is configured."""
    if isinstance(exc, DatabaseUnavailable):
        return True
    names = {type(exc).__name__}
    names.update(c.__name__ for c in type(exc).__mro__)
    return bool(names & {"OperationalError", "PoolTimeout", "QueryCanceled",
                         "AdminShutdown", "CannotConnectNow", "TooManyConnections"})


def transient_exception_classes() -> tuple:
    """The exception classes `is_transient` names, for registering handlers.
    psycopg is only imported when a database is configured."""
    classes = [DatabaseUnavailable]
    if DATABASE_URL:
        import psycopg
        import psycopg_pool
        classes += [psycopg.OperationalError, psycopg_pool.PoolTimeout, psycopg.errors.QueryCanceled]
    return tuple(classes)


TEMPORARILY_UNAVAILABLE = "Profile data is temporarily unavailable. Try again in a moment."


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
        # Pin the schema on every new connection. Against the direct endpoint
        # the connection is ours alone and this means what it says - which is
        # what makes DATABASE_SCHEMA work for test runs. It is NOT what makes
        # the isolation safe; the direct endpoint is. Keeping it means a
        # future change that reintroduces sharing loses the isolation loudly
        # rather than the pinning silently.
        conn.execute(f'SET search_path TO "{SCHEMA}"')
        # Server-side: a query that runs past this is cancelled and the
        # request answers 503 instead of holding its pool slot and its
        # worker thread indefinitely. Migrations get their own connection.
        conn.execute(f"SET statement_timeout TO {int(STATEMENT_TIMEOUT_MS)}")

    _pool = ConnectionPool(
        # Direct, never the pooler - see the module docstring. This is the
        # line that stops rows landing in somebody else's schema.
        pool_dsn(),
        min_size=1,
        max_size=POOL_MAX,
        open=True,
        configure=_configure,
        timeout=ACQUIRE_TIMEOUT,
        max_idle=MAX_IDLE,
        max_lifetime=MAX_LIFETIME,
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


class MigrationsMissing(RuntimeError):
    """The migration files are not on disk, so the schema cannot be applied.

    Its own type so a caller can tell "this deployment is built wrong" apart
    from "the database is unreachable". The first is never survivable by
    retrying; the second is.
    """


MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


def _migration_files() -> list:
    """Every migration, in the order their filenames sort.

    Sorted by name, so the numeric prefix IS the order. That is the whole
    versioning scheme, and it is deliberately not a framework: the app has one
    database, one deployment, and no need for down-migrations or branching
    version graphs. What it does need is that two machines applying the same
    directory end up with the same schema, which sorting a fixed set of files
    gives for free.

    Missing migrations are FATAL, not empty. This used to return `[]` when the
    directory was absent, which is exactly what a container image that forgot
    to COPY it looks like - and the failure was silent: `migrate()` created
    `schema_migrations`, recorded nothing, logged "Schema up to date", and the
    app booted onto a database with no tables in it. Every query then failed
    later, far from the cause. An empty directory is the same mistake wearing a
    different hat, so both raise.
    """
    if not os.path.isdir(MIGRATIONS_DIR):
        raise MigrationsMissing(
            f"No migrations directory at {MIGRATIONS_DIR}. The schema cannot be "
            "applied. In a container this means the image did not COPY "
            "migrations/ - see Dockerfile.backend."
        )
    names = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql"))
    if not names:
        raise MigrationsMissing(
            f"The migrations directory {MIGRATIONS_DIR} holds no .sql files. "
            "The schema cannot be applied."
        )
    return names


def assert_migrations_present() -> list:
    """Raise unless the migration files are on disk. Returns their names.

    A separate, public, database-free check so startup can refuse a build that
    shipped without `migrations/` even when there is no DATABASE_URL to talk
    to. A missing schema directory is a broken image, not a storage outage,
    and the two must not degrade the same way: the outage is survivable and
    this is not.
    """
    return _migration_files()


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
        # A migration may legitimately take longer than one request is
        # allowed to; the request bound (set on every pooled connection by
        # _configure) is lifted here and put back before the slot is returned.
        conn.execute("SET statement_timeout TO 0")
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
        conn.execute(f"SET statement_timeout TO {int(STATEMENT_TIMEOUT_MS)}")
    if applied_now:
        logger.info(f"🗄️ Migrated {SCHEMA}: {len(applied_now)} new migration(s)")
    else:
        logger.info(f"🗄️ Schema up to date ({SCHEMA})")
    return applied_now


# The old name, kept because several call sites and tests read better with it
# and because "apply the schema" is what the caller actually wants done.
apply_schema = migrate


def pool_dsn() -> str:
    """The direct DSN with the socket-level timeouts appended (libpq keyword
    parameters, so they ride along in the URL's query string). Values already
    present in DATABASE_URL win."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(direct_dsn())
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    defaults = {
        "connect_timeout": str(CONNECT_TIMEOUT),
        "keepalives": "1",
        "keepalives_idle": "30",
        "keepalives_interval": "5",
        "keepalives_count": "3",
        "tcp_user_timeout": str(TCP_USER_TIMEOUT_MS),
        # Neon suspends idle computes; the first connection after a suspend
        # can take a few seconds. Nothing here is a reason to add a retry
        # loop - connect_timeout above already covers the wake.
    }
    for k, v in defaults.items():
        query.setdefault(k, v)
    return urlunsplit(parts._replace(query=urlencode(query)))


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

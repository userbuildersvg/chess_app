"""
The database being away is a bounded wait and a clean 503, never a hang.

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_dbr_$$ \\
        /tmp/chessapp/bin/python test_db_resilience.py

Needs DATABASE_URL and a disposable schema (dropped on the way out).

What is proved: the pool DSN carries connect / keepalive / TCP-user timeouts
and respects values already in DATABASE_URL; every pooled connection has the
statement timeout and a query past it is cancelled rather than waited on;
migrations lift it and put it back; the transient-error classifier knows
the psycopg families; a database failure under /api/profile,
/api/profile/games, /api/admin/overview and /api/account answers 503 with
one sentence and no traceback; and the background writer never blocks the
caller, even while its thread is stuck on a write.
"""
import os
import threading
import time

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ["ACCOUNTS_ENABLED"] = "true"
os.environ.setdefault("SESSION_COOKIE_SECRET", "test-signing-key-not-a-real-secret")
os.environ["DATABASE_STATEMENT_TIMEOUT_MS"] = "400"
os.environ["ADMIN_EMAILS"] = "founder@example.com"
if os.environ.get("DATABASE_SCHEMA", "public") == "public":
    raise SystemExit("Refusing to run against the public schema - set DATABASE_SCHEMA to a disposable name.")

import psycopg
import psycopg_pool
from fastapi.testclient import TestClient

import admin_api
import app
import db
import db_writer
import profile_service
import rate_limit as rate_limit_module

PASSED = FAILED = 0


def check(label, cond, detail=None):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f" - {detail}" if detail else ""))


def section(name):
    print(f"\n--- {name} ---")


section("pool DSN")
saved = db.DATABASE_URL
db.DATABASE_URL = "postgresql://u:p@ep-x-pooler.neon.tech/db?sslmode=require"
dsn = db.pool_dsn()
check("direct endpoint, not the pooler", "-pooler." not in dsn and "ep-x.neon.tech" in dsn, dsn)
check("timeouts and keepalives appended", all(k in dsn for k in (
    "connect_timeout=10", "keepalives=1", "keepalives_idle=30", "tcp_user_timeout=15000", "sslmode=require")), dsn)
db.DATABASE_URL = "postgresql://u:p@ep-x.neon.tech/db?connect_timeout=3"
check("a value already in DATABASE_URL wins", "connect_timeout=3" in db.pool_dsn() and "connect_timeout=10" not in db.pool_dsn(), db.pool_dsn())
db.DATABASE_URL = saved

section("transient classifier")
check("OperationalError, PoolTimeout, QueryCanceled and no-database are transient",
      all(db.is_transient(e) for e in (psycopg.OperationalError("x"), psycopg_pool.PoolTimeout("x"),
                                       psycopg.errors.QueryCanceled("x"), db.DatabaseUnavailable("x"))))
check("a programming error or a value error is not", not db.is_transient(ValueError("x")) and not db.is_transient(psycopg.errors.UndefinedTable("x")))

section("statement timeout on every pooled connection")
db.migrate()
with db.connection() as conn:
    check("SHOW statement_timeout = 400ms", conn.execute("SHOW statement_timeout").fetchone()[0] == "400ms",
          conn.execute("SHOW statement_timeout").fetchone()[0])
    started = time.monotonic()
    try:
        conn.execute("SELECT pg_sleep(5)")
        cancelled = None
    except Exception as exc:
        cancelled = exc
    took = time.monotonic() - started
    check("a slow query is cancelled, not waited on", isinstance(cancelled, psycopg.errors.QueryCanceled) and took < 3, (type(cancelled).__name__, round(took, 2)))
    check("...and is classified transient", cancelled is not None and db.is_transient(cancelled))
db.migrate()  # runs the lift/restore path again
with db.connection() as conn:
    check("migrate() puts the bound back on the connection it used", conn.execute("SHOW statement_timeout").fetchone()[0] == "400ms")
check("pool acquisition is bounded", db.get_pool().timeout == db.ACQUIRE_TIMEOUT and db.ACQUIRE_TIMEOUT <= 30)
check("idle connections are recycled before Neon drops them", db.get_pool().max_idle <= 300)

section("admin overview stays bounded")
started = time.monotonic()
ov = admin_api.build_overview()
check("empty-schema overview finishes in bounded time (about thirty round trips)", ov["database"] and time.monotonic() - started < 10, round(time.monotonic() - started, 1))


def clear_limits():
    rate_limit_module.limit_login.limiter.reset()
    rate_limit_module.limit_signup.limiter.reset()


section("a database failure is a 503 with one sentence")
clear_limits()
with TestClient(app.app) as c:
    c.post("/api/auth/signup", json={"username": "founder", "password": "founder-password-1", "email": "founder@example.com"})
    original_build, original_list, original_get = profile_service.build, profile_service.list_games, profile_service.get_game
    original_overview = admin_api.build_overview

    def boom(*a, **k):
        raise psycopg.OperationalError("SSL SYSCALL error: No route to host")

    def slow(*a, **k):
        raise psycopg_pool.PoolTimeout("couldn't get a connection after 10.00 sec")

    def cancelled(*a, **k):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")

    try:
        profile_service.build = boom
        r = c.get("/api/profile")
        check("/api/profile -> 503", r.status_code == 503, r.status_code)
        body = r.json()
        check("...with the sentence, both shapes, and Retry-After",
              body == {"ok": False, "error": "database_unavailable", "message": db.TEMPORARILY_UNAVAILABLE, "detail": db.TEMPORARILY_UNAVAILABLE}
              and r.headers.get("retry-after") == "5", body)
        check("...and no Neon text or traceback", "SSL" not in r.text and "route to host" not in r.text and "Traceback" not in r.text)
        profile_service.list_games = slow
        r = c.get("/api/profile/games")
        check("/api/profile/games on pool timeout -> 503 same body", r.status_code == 503 and r.json()["error"] == "database_unavailable", r.text)
        profile_service.get_game = cancelled
        r = c.get("/api/profile/games/1")
        check("/api/profile/games/{id} on statement timeout -> 503", r.status_code == 503 and r.json()["message"] == db.TEMPORARILY_UNAVAILABLE, r.text)
        admin_api.build_overview = boom
        r = c.get("/api/admin/overview")
        check("/api/admin/overview -> 503, not a hang", r.status_code == 503 and "Traceback" not in r.text, r.text[:200])
    finally:
        profile_service.build, profile_service.list_games, profile_service.get_game = original_build, original_list, original_get
        admin_api.build_overview = original_overview
    r = c.get("/api/profile")
    check("once the database is back the route answers again", r.status_code == 200)
    check("a non-database error is not disguised as one", c.get("/api/profile/games/not-a-number").status_code == 422)

section("background writer never blocks the caller")
w = db_writer.BackgroundWriter(name="test-writer", max_queued=3)
gate = threading.Event()
w.submit(lambda: gate.wait(10), label="stuck")
time.sleep(0.05)
started = time.monotonic()
ok = [w.submit(lambda: None, label=f"q{i}") for i in range(5)]
took = time.monotonic() - started
check("submit returns at once while the writer thread is stuck", took < 0.2, round(took, 3))
check("the queue is bounded: extra writes are dropped, not queued forever", ok.count(True) == 3 and ok.count(False) == 2 and w.dropped == 2, ok)
gate.set()
check("the writer drains once the write completes", w.flush(5) and w.written >= 3, (w.written, w.pending()))

print(f"\n{PASSED} passed, {FAILED} failed")
db.drop_schema()
try:
    app.stockfish_service.close()
except Exception:
    pass
raise SystemExit(1 if FAILED else 0)

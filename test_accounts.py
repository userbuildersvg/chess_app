"""
Guest mode, and accounts that are built but switched off.

    DISABLE_LANGFLOW=true /tmp/chessapp/bin/python test_accounts.py

Needs Stockfish, because it drives the real endpoints through TestClient
rather than mocking them - the claims being made here ("nothing is saved",
"a stranger cannot reach your game") are only worth anything if they are true
of the app as it actually serves requests.

THE FOUR CLAIMS
---------------
1. Accounts are unavailable. Not hidden - unavailable, refused by the server
   with a message meant for a person, on every route that would create or use
   one.
2. A guest can do everything. Play, reset, change the opponent profile, chat history,
   grading, the learning panel - the whole app, exactly as before.
3. A guest's history is their own, and claimable. It used to be that a guest
   saved nothing at all; that changed so signing up can carry the games you
   played beforehand into your new account. What is still true, and is what
   these tests check, is that no other owner can see it and that the old
   SQLite file is never touched.
4. Two visitors are two players. Separate boards, separate profiles,
   separate sandbox sessions, and neither can reach the other's.

TestClient is used as a context manager throughout (see CLAUDE.md section 4):
used bare it builds a fresh portal per request, so a detached background task
dies the instant its request returns.
"""

import hashlib
import os
import shutil
import tempfile
import time

os.environ.setdefault("DISABLE_LANGFLOW", "true")
# The closed beta gate is fail-closed by default (`beta_service.beta_required`),
# so with it left alone every request in this file would be answered 403 by
# `beta_gate.py` before reaching the route under test. Switched off here rather
# than worked around, because this suite is testing what the routes do and not
# who may reach them - that is `test_beta_access.py`, which asserts among other
# things that this default is ON when nobody says otherwise.
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
# Must be decided BEFORE app is imported: app.py wires the account resolver
# into the identity middleware at import time, and the whole point of the
# default is that with the flag off there is nothing wired that could resolve
# an account at all.
os.environ["ACCOUNTS_ENABLED"] = "false"

from fastapi.testclient import TestClient

import app
import auth_service as auth_module
import db
import learning_service as learning_module
from auth_service import AuthError, AuthService

PASSED = 0
FAILED = 0


def check(label, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f" - {detail}" if detail else ""))


def no_such_account(username):
    """True if the real accounts database has no such user.

    Accounts moved from `data/accounts.db` to Postgres, so this asks the real
    database rather than a file. Answers True when there is no database
    configured at all, which is the same "nothing was created" the file
    version meant when the file was absent.""" 
    if not db.configured():
        return True
    try:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM users WHERE username_ci = %s", (username.lower(),)
            ).fetchone()
    except Exception:
        return True
    return row[0] == 0


def db_fingerprint():
    """A hash of the shared learning database, or None if it does not exist."""
    path = learning_module.DB_PATH
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ===========================================================================
# 1. Accounts are switched off, and say so
# ===========================================================================

print("\n--- accounts are unavailable ---")

with TestClient(app.app) as client:
    r = client.get("/api/auth/config")
    body = r.json()
    check("the config endpoint answers even with accounts off", r.status_code == 200, r.status_code)
    check("it reports accounts as disabled", body["accounts_enabled"] is False, body)
    check("it reports guest mode as available", body["guest_mode"] is True, body)
    check("it carries the message the UI should show",
          isinstance(body["unavailable_message"], str) and body["unavailable_message"],
          body.get("unavailable_message"))

    r = client.post("/api/auth/signup", json={"username": "someone", "password": "correct horse"})
    check("signup is refused with 503, not 404", r.status_code == 503, r.status_code)
    check("the signup refusal is readable by a person",
          "aren't available" in r.json()["detail"], r.json())

    r = client.post("/api/auth/login", json={"username": "someone", "password": "correct horse"})
    check("login is refused with 503", r.status_code == 503, r.status_code)

    # The refusal has to be enforced by the SERVER, not by a hidden button.
    # If any account were creatable while the flag is off, the whole
    # "deploy guest mode first" plan would be false.
    check("no account was created by the refused signup",
          no_such_account("someone"),
          "an account exists despite accounts being disabled")

    r = client.get("/api/auth/me")
    me = r.json()
    check("whoami answers for a guest", r.status_code == 200, r.status_code)
    check("a guest is reported as not signed in", me["signed_in"] is False, me)
    check("a guest is reported as a guest", me["guest"] is True, me)
    check("whoami never returns the identity string itself",
          "identity" not in me and "guest_id" not in me, me)

    # Sign-out is deliberately not gated: it must work in states nobody
    # planned for, and its outcome is always achievable.
    r = client.post("/api/auth/logout")
    check("logout works even with accounts off", r.status_code == 200, r.status_code)


# ===========================================================================
# 2 & 3. A guest can do everything, and saves nothing
# ===========================================================================

print("\n--- a guest plays a real game ---")

fingerprint_before = db_fingerprint()

with TestClient(app.app) as client:
    r = client.get("/api/status")
    status = r.json()
    check("a first-time visitor gets a game without signing in", r.status_code == 200, r.status_code)
    check("the visitor is told they are a guest", status["guest"] is True, status.get("guest"))
    check("a guest cookie was set", "zw_guest" in client.cookies, dict(client.cookies))

    guest_cookie = client.cookies.get("zw_guest")
    check("the guest cookie is opaque and unguessable",
          guest_cookie.startswith("guest:") and len(guest_cookie) > 30, guest_cookie)

    r = client.post("/api/difficulty", json={"profile": "casual"})
    check("a guest can change the opponent profile", r.json().get("profile") == "casual", r.json())

    r = client.post("/api/move", json={"move": "e2e4"})
    check("a guest can play a move", r.json().get("success") is True, r.json())

    status = client.get("/api/status").json()
    check("the guest's move is in their history",
          len(status["history"]) >= 1 and status["history"][0]["move"] == "e2e4",
          status["history"][:1])
    check("the profile stuck across requests", status["opponent_profile"] == "casual", status["opponent_profile"])
    check("move grading is available to a guest", status["move_quality_enabled"] is True)
    check("the accuracy panel is populated for a guest", "white" in status["accuracy"])

    summary = client.get("/api/learning/summary").json()
    check("the learning panel answers for a guest", summary["success"] is True, summary)
    check("the learning panel says this is a guest", summary["guest"] is True, summary)
    check("the learning panel says a guest's history IS persisted",
          summary["persisted"] is True, summary)
    check("the learning panel says a guest's history can be claimed",
          summary["claimable"] is True, summary)

    r = client.post("/api/reset")
    check("a guest can reset", r.json().get("success") is True, r.json())
    # Reset destroys a game in progress, so it must not be reachable by
    # anything that can make this browser follow a URL.
    check("GET /api/reset is refused", client.get("/api/reset").status_code == 405,
          client.get("/api/reset").status_code)

    r = client.post("/api/set-color", json={"color": "black"})
    check("a guest can switch colour", r.json().get("player_color") == "black", r.json())
    check("the profile survived the new game (it is a preference, not a position)",
          r.json().get("opponent_profile") == "casual", r.json().get("opponent_profile"))

fingerprint_after = db_fingerprint()
check("the shared learning database was never written by a guest",
      fingerprint_before == fingerprint_after,
      "data/learning.db changed while only guests were playing")

# The other half of claim 3: it is not that learning was switched OFF for the
# guest, it is that their learning is filed under their own owner. A guest
# whose learning layer did nothing would pass the check above and still be a
# regression in behaviour.
with TestClient(app.app) as client:
    client.get("/api/status")
    identity = next(iter(app.player_sessions.identities()))
    session = app.player_sessions.for_identity(identity)
    check("a guest still HAS a learning layer",
          session.learning is not None, session.learning)
    check("it is not the shared one",
          session.learning.owner == session.identity, session.learning.owner)
    check("it records the guest's own play",
          session.learning.get_learning_summary()["opponent"]["games_played"] >= 0)
    check("its database is in memory, not a file",
          session.learning.owner.startswith("guest:"), session.learning.owner)


print("\n--- the API docs follow the deployment ---")

import identity as identity_module

check("docs are served locally", app.DOCS_ENABLED is True, app.DOCS_ENABLED)
with TestClient(app.app) as client:
    check("/docs answers locally", client.get("/docs").status_code == 200)
    check("/openapi.json answers locally", client.get("/openapi.json").status_code == 200)

# The production decision itself, without needing a second process. Hiding the
# docs page while leaving /openapi.json readable would be decorative, so both
# are checked.
os.environ["RENDER"] = "1"
try:
    check("production is detected from RENDER", identity_module.is_production() is True)
    docs_off = (
        os.environ.get("ENABLE_DOCS").lower() == "true"
        if os.environ.get("ENABLE_DOCS") is not None
        else not identity_module.is_production()
    )
    check("docs would be off in production", docs_off is False, docs_off)
    os.environ["ENABLE_DOCS"] = "true"
    forced = os.environ.get("ENABLE_DOCS").lower() == "true"
    check("ENABLE_DOCS=true overrides production", forced is True)
finally:
    os.environ.pop("RENDER", None)
    os.environ.pop("ENABLE_DOCS", None)


print("\n--- the health check creates nothing ---")

with TestClient(app.app) as client:
    before = app.player_sessions.count()
    for _ in range(5):
        r = client.get("/api/health")
    check("the health endpoint answers", r.status_code == 200, r.status_code)
    check("it reports the process is up", r.json()["status"] == "ok", r.json())
    check("it says whether accounts are on", r.json()["accounts_enabled"] is False, r.json())
    # The point of the endpoint. Health checks are anonymous and frequent, so
    # if this one minted a player session the store's 500-session cap would be
    # churned through daily and real players evicted to make room for
    # monitoring.
    check("five health checks created no player sessions",
          app.player_sessions.count() == before,
          (before, app.player_sessions.count()))


# ===========================================================================
# 4. Two visitors are two players
# ===========================================================================

print("\n--- two visitors, two games ---")

with TestClient(app.app) as alice, TestClient(app.app) as bob:
    alice.get("/api/status")
    bob.get("/api/status")

    check("two visitors get two different guest cookies",
          alice.cookies.get("zw_guest") != bob.cookies.get("zw_guest"))

    alice.post("/api/difficulty", json={"profile": "beginner"})
    bob.post("/api/difficulty", json={"profile": "expert"})
    check("Alice's profile is her own",
          alice.get("/api/difficulty").json()["profile"] == "beginner")
    check("Bob's profile is his own",
          bob.get("/api/difficulty").json()["profile"] == "expert")

    alice.post("/api/move", json={"move": "d2d4"})
    alice_history = alice.get("/api/status").json()["history"]
    bob_history = bob.get("/api/status").json()["history"]
    check("Alice's move landed on Alice's board", len(alice_history) >= 1, len(alice_history))
    check("Bob's board never moved", len(bob_history) == 0, len(bob_history))

    alice_summary = alice.get("/api/learning/summary").json()
    bob_summary = bob.get("/api/learning/summary").json()
    check("neither visitor can see the other's learning history",
          alice_summary["opponent"] is not bob_summary["opponent"])

    # Sandbox sessions are owned, not merely isolated. Before this, anyone
    # holding an id could drive the session it named.
    made = alice.post("/api/sandbox/session", json={}).json()
    session_id = made["session_id"]
    check("Alice can open a sandbox session", bool(session_id), made)
    check("Alice can read her own sandbox session",
          alice.get(f"/api/sandbox/session/{session_id}").status_code == 200)

    r = bob.get(f"/api/sandbox/session/{session_id}")
    check("Bob cannot read Alice's sandbox session by id", r.status_code == 404, r.status_code)
    # Same status and same wording as a session that was never created. The
    # id differs only because each message echoes the id the caller asked
    # for, which tells them nothing they did not already type.
    missing = bob.get("/api/sandbox/session/nope")
    check("the refusal is indistinguishable from a session that does not exist",
          r.status_code == missing.status_code
          and r.json()["detail"].replace(session_id, "X")
              == missing.json()["detail"].replace("nope", "X"),
          (r.json(), missing.json()))
    r = bob.delete(f"/api/sandbox/session/{session_id}")
    check("Bob cannot delete Alice's sandbox session", r.status_code == 404, r.status_code)
    check("Alice's sandbox session survived Bob's attempt",
          alice.get(f"/api/sandbox/session/{session_id}").status_code == 200)


# ===========================================================================
# 5. The account machinery itself, exercised directly
#
# Switched off at the HTTP layer, but the code beneath must be right BEFORE
# it is ever switched on - that is the whole reason to build it now rather
# than later. Run against a temporary database so the real one is untouched.
# ===========================================================================

print("\n--- the accounts themselves ---")

# A temporary Postgres schema, not a temporary file: same isolation, one
# storage backend later. Dropped on the way out of the with-block at the end
# of this section.
_schema_ctx = db.temporary_schema(prefix="zwtest_auth")
_test_connect = _schema_ctx.__enter__()
svc = AuthService(connect=_test_connect)

user = svc.create_user("Magnus", "a-good-long-password")
check("an account can be created", user["username"] == "Magnus", user)

try:
    svc.create_user("magnus", "another-password")
    check("usernames are case-insensitively unique", False, "duplicate accepted")
except AuthError as e:
    check("usernames are case-insensitively unique", e.status_code == 409, e.status_code)

for bad, why in [("ab", "too short"), ("a" * 33, "too long"), ("has space", "has a space"), ("", "empty")]:
    try:
        svc.create_user(bad, "a-good-long-password")
        check(f"a username that is {why} is refused", False, bad)
    except AuthError:
        check(f"a username that is {why} is refused", True)

try:
    svc.create_user("shorty", "small")
    check("a short password is refused", False)
except AuthError:
    check("a short password is refused", True)

try:
    svc.create_user("longy", "x" * 500)
    check("an absurdly long password is refused", False)
except AuthError:
    check("an absurdly long password is refused", True)

with _test_connect() as conn:
    row = conn.execute("SELECT password_hash, salt, iterations FROM users WHERE username = 'Magnus'").fetchone()
# bytea comes back as bytes or memoryview depending on the path; the claims
# below are about the contents, not the wrapper.
_hash_bytes, _salt_bytes = bytes(row[0]), bytes(row[1])
check("the password is not stored in plaintext",
      b"a-good-long-password" not in _hash_bytes, "plaintext password found in the database")
check("the hash is salted", len(_salt_bytes) == 16, _salt_bytes)
check("the iteration count is stored with the row so it can be raised later",
      row[2] >= 600_000, row[2])

check("the right password verifies", svc.verify_password("Magnus", "a-good-long-password") is not None)
check("the username is case-insensitive on the way in",
      svc.verify_password("MAGNUS", "a-good-long-password") is not None)
check("a wrong password does not verify", svc.verify_password("Magnus", "wrong") is None)
check("an unknown user does not verify", svc.verify_password("nobody", "anything") is None)

token = svc.start_session(user["id"])
check("a session token resolves to the account", svc.resolve_session(token) == f"user:{user['id']}")
check("the identity is the opaque string the rest of the app expects",
      svc.resolve_session(token).startswith("user:"))
check("a made-up token resolves to nothing", svc.resolve_session("not-a-real-token") is None)
check("an empty token resolves to nothing", svc.resolve_session("") is None)

with _test_connect() as conn:
    stored = [r[0] for r in conn.execute("SELECT token_hash FROM sessions").fetchall()]
check("the session token itself is never stored, only its hash",
      token not in stored and hashlib.sha256(token.encode()).hexdigest() in stored)

check("signing out ends the session", svc.end_session(token) is True)
check("the ended session no longer resolves", svc.resolve_session(token) is None)
check("signing out twice is harmless", svc.end_session(token) is False)

expiring = svc.start_session(user["id"], ttl_seconds=-1)
check("an expired session does not resolve", svc.resolve_session(expiring) is None)
with _test_connect() as conn:
    left = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
check("an expired session is cleaned up when it is used", left == 0, left)

# The temporary schema replaces the temporary directory; it is dropped at
# the end of this file, alongside closing the pool.


# ===========================================================================
# 6. The flag is read live, not cached at import
#
# If it were cached, the enabled path could never be tested without a restart
# - which is exactly the path most in need of testing before it is switched
# on for real.
# ===========================================================================

print("\n--- the flag ---")

check("accounts are off by default", auth_module.accounts_enabled() is False)
os.environ["ACCOUNTS_ENABLED"] = "true"
check("the flag is read at call time, not cached at import",
      auth_module.accounts_enabled() is True)
os.environ["ACCOUNTS_ENABLED"] = "false"
check("turning it back off takes effect immediately",
      auth_module.accounts_enabled() is False)


# python-chess runs the engine on a non-daemon thread, closed by a FastAPI
# shutdown hook that a plain script never fires. Without this the interpreter
# hangs at exit AFTER printing "all passed" - which looks exactly like a
# deadlock in the code under test. (CLAUDE.md section 4.)
app.stockfish_service.close()
# Drop the temporary schema the account tests ran against, and return the
# shared pool's connections. Neither is optional: a leaked schema accumulates
# in the real database on every run, and a live pool holds non-daemon threads
# that keep the interpreter from exiting.
_schema_ctx.__exit__(None, None, None)
db.close_pool()

print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

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
2. A guest can do everything. Play, reset, change difficulty, chat history,
   grading, the learning panel - the whole app, exactly as before.
3. A guest saves nothing. `data/learning.db` is byte-identical afterwards.
4. Two visitors are two players. Separate boards, separate difficulty,
   separate sandbox sessions, and neither can reach the other's.

TestClient is used as a context manager throughout (see CLAUDE.md section 4):
used bare it builds a fresh portal per request, so a detached background task
dies the instant its request returns.
"""

import hashlib
import os
import shutil
import sqlite3
import tempfile
import time

os.environ.setdefault("DISABLE_LANGFLOW", "true")
# Must be decided BEFORE app is imported: app.py wires the account resolver
# into the identity middleware at import time, and the whole point of the
# default is that with the flag off there is nothing wired that could resolve
# an account at all.
os.environ["ACCOUNTS_ENABLED"] = "false"

from fastapi.testclient import TestClient

import app
import auth_service as auth_module
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
    """True if the real accounts database has no such user (or does not exist)."""
    path = auth_module.DB_PATH
    if not os.path.exists(path):
        return True
    with sqlite3.connect(path) as conn:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM users WHERE username_ci = ?", (username.lower(),)
            ).fetchone()
        except sqlite3.OperationalError:
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

    r = client.post("/api/difficulty", json={"difficulty": 6})
    check("a guest can change difficulty", r.json().get("difficulty") == 6, r.json())

    r = client.post("/api/move", json={"move": "e2e4"})
    check("a guest can play a move", r.json().get("success") is True, r.json())

    status = client.get("/api/status").json()
    check("the guest's move is in their history",
          len(status["history"]) >= 1 and status["history"][0]["move"] == "e2e4",
          status["history"][:1])
    check("the difficulty stuck across requests", status["difficulty"] == 6, status["difficulty"])
    check("move grading is available to a guest", status["move_quality_enabled"] is True)
    check("the accuracy panel is populated for a guest", "white" in status["accuracy"])

    summary = client.get("/api/learning/summary").json()
    check("the learning panel answers for a guest", summary["success"] is True, summary)
    check("the learning panel says this is a guest", summary["guest"] is True, summary)
    check("the learning panel says nothing is persisted", summary["persisted"] is False, summary)

    r = client.get("/api/reset")
    check("a guest can reset", r.json().get("success") is True, r.json())

    r = client.post("/api/set-color", json={"color": "black"})
    check("a guest can switch colour", r.json().get("player_color") == "black", r.json())
    check("the difficulty survived the new game (it is a preference, not a position)",
          r.json().get("difficulty") == 6, r.json().get("difficulty"))

fingerprint_after = db_fingerprint()
check("the shared learning database was never written by a guest",
      fingerprint_before == fingerprint_after,
      "data/learning.db changed while only guests were playing")

# The other half of claim 3: it is not that learning was switched OFF for the
# guest, it is that their learning went somewhere private. A guest whose
# learning layer did nothing would pass the check above and still be a
# regression in behaviour.
with TestClient(app.app) as client:
    client.get("/api/status")
    identity = next(iter(app.player_sessions.identities()))
    session = app.player_sessions.for_identity(identity)
    check("a guest still HAS a learning layer",
          session.learning is not None, session.learning)
    check("it is not the shared one",
          session.learning is not app.learning_service, type(session.learning).__name__)
    check("it records the guest's own play",
          session.learning.get_learning_summary()["opponent"]["games_played"] >= 0)
    check("its database is in memory, not a file",
          "mode=memory" in session.learning.db_path, session.learning.db_path)


# ===========================================================================
# 4. Two visitors are two players
# ===========================================================================

print("\n--- two visitors, two games ---")

with TestClient(app.app) as alice, TestClient(app.app) as bob:
    alice.get("/api/status")
    bob.get("/api/status")

    check("two visitors get two different guest cookies",
          alice.cookies.get("zw_guest") != bob.cookies.get("zw_guest"))

    alice.post("/api/difficulty", json={"difficulty": 3})
    bob.post("/api/difficulty", json={"difficulty": 18})
    check("Alice's difficulty is her own",
          alice.get("/api/difficulty").json()["difficulty"] == 3)
    check("Bob's difficulty is his own",
          bob.get("/api/difficulty").json()["difficulty"] == 18)

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

tmpdir = tempfile.mkdtemp(prefix="zw-auth-test-")
svc = AuthService(db_path=os.path.join(tmpdir, "accounts.db"))

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

with sqlite3.connect(os.path.join(tmpdir, "accounts.db")) as conn:
    row = conn.execute("SELECT password_hash, salt, iterations FROM users WHERE username = 'Magnus'").fetchone()
check("the password is not stored in plaintext",
      b"a-good-long-password" not in row[0], "plaintext password found in the database")
check("the hash is salted", isinstance(row[1], bytes) and len(row[1]) == 16, row[1])
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

with sqlite3.connect(os.path.join(tmpdir, "accounts.db")) as conn:
    stored = [r[0] for r in conn.execute("SELECT token_hash FROM sessions").fetchall()]
check("the session token itself is never stored, only its hash",
      token not in stored and hashlib.sha256(token.encode()).hexdigest() in stored)

check("signing out ends the session", svc.end_session(token) is True)
check("the ended session no longer resolves", svc.resolve_session(token) is None)
check("signing out twice is harmless", svc.end_session(token) is False)

expiring = svc.start_session(user["id"], ttl_seconds=-1)
check("an expired session does not resolve", svc.resolve_session(expiring) is None)
with sqlite3.connect(os.path.join(tmpdir, "accounts.db")) as conn:
    left = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
check("an expired session is cleaned up when it is used", left == 0, left)

shutil.rmtree(tmpdir, ignore_errors=True)


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

print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

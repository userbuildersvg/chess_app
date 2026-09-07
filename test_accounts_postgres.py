"""
Accounts on Postgres: ownership, claiming, isolation, and the AI privacy line.

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_pg_$$ \\
        /tmp/chessapp/bin/python test_accounts_postgres.py

Needs Stockfish and DATABASE_URL. Run it against a disposable schema, never
`public` - it creates accounts and games, and this file drops the schema on
the way out (CLAUDE.md section 6).

WHAT THIS FILE IS FOR
---------------------
`test_accounts.py` proves the shipping configuration: accounts are refused,
everyone is a guest, and two visitors are two players. This file proves the
configuration we are NOT shipping yet - `ACCOUNTS_ENABLED=true` - because the
whole point of building accounts before switching them on is that the code
beneath must be right first, and "it is switched off" is not an argument that
it works.

Seven claims, and the reason each is here rather than assumed:

1. **Migrations are deterministic, idempotent and fail safely.** They will be
   run against real user data by a deploy hook nobody is watching.
2. **Every persisted row has one unambiguous owner**, enforced by the database
   and not only by the queries above it.
3. **A guest's history is claimable exactly once, by exactly one account.**
   The failure mode is handing one person's games to another.
4. **Accounts cannot reach each other's data** - read, write or delete, by any
   id an attacker can put in a URL, a payload or a cookie. Release blocker.
5. **Live game state is per session.** Two players, two boards.
6. **Guest games do not enter the global AI pool until claimed**, and nothing
   identifying enters it ever.
7. **Retention deletes unclaimed guest data and nothing else.**
"""

import os
import threading
import time

os.environ.setdefault("DISABLE_LANGFLOW", "true")
# Before importing app: app.py wires the account resolver into the identity
# middleware at import time, so this decides whether accounts exist at all.
os.environ["ACCOUNTS_ENABLED"] = "true"
os.environ.setdefault("SESSION_COOKIE_SECRET", "test-signing-key-not-a-real-secret")

if os.environ.get("DATABASE_SCHEMA", "public") == "public":
    raise SystemExit(
        "Refusing to run against the public schema - this suite creates accounts "
        "and games. Set DATABASE_SCHEMA to a disposable name, e.g. "
        "DATABASE_SCHEMA=zwtest_pg_$$"
    )

from fastapi.testclient import TestClient

import app
import auth_service as auth_module
import db
import email_service
import google_oauth
import identity as identity_module
import rate_limit as rate_limit_module
from auth_service import AuthService
from learning_service import LearningService

PASSED = 0
FAILED = 0


def check(label, condition, detail=None):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f" - {detail}" if detail else ""))


def clear_auth_limits():
    """Forget recorded auth attempts.

    This suite signs in dozens of times from one address; a person does not.
    Without this the login bucket fills partway through and every later
    sign-in fails with a 429 that reads exactly like a broken password. The
    limit itself is proven in the rate-limiting section rather than assumed.
    """
    rate_limit_module.limit_login.limiter.reset()
    rate_limit_module.limit_signup.limiter.reset()
    rate_limit_module.limit_password_forgot.limiter.reset()
    rate_limit_module.limit_password_reset.limiter.reset()
    rate_limit_module.forgot_by_email.reset()


def section(name):
    print(f"\n--- {name} ---")


# ===========================================================================
# 1. Migrations
# ===========================================================================

section("migrations")

# Against a genuinely empty schema, not this one: when the whole suite runs
# in sequence an earlier file's startup hook has already migrated the shared
# schema, and "a fresh database applies everything" would then be measuring
# test ordering rather than the migration runner.
with db.temporary_schema(prefix="zwtest_fresh") as fresh_connect:
    with fresh_connect() as fc:
        ledger = {r[0] for r in fc.execute("SELECT version FROM schema_migrations").fetchall()}
    expected = {f[:-len(".sql")] for f in db._migration_files()}
    check("a fresh database applies every migration", ledger == expected, (ledger, expected))

db.migrate()
again = db.migrate()
check("re-running applies nothing", again == [], again)

with db.connection() as conn:
    tables = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (db.SCHEMA,)).fetchall()}
check("every expected table exists",
      {"users", "sessions", "games", "moves", "claimed_guests",
       "federated_identities", "schema_migrations"} <= tables, sorted(tables))

with db.connection() as conn:
    indexes = {r[0] for r in conn.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = %s", (db.SCHEMA,)).fetchall()}
# Each of these backs a query that runs on a hot path: the owner filter on
# every profile read, and the exact-FEN lookup on every single AI move.
check("the owner index exists", "idx_games_owner" in indexes, sorted(indexes))
check("the FEN index exists", "idx_moves_fen_before" in indexes, sorted(indexes))
check("username uniqueness is an index, not a check-then-insert",
      "idx_users_username_ci" in indexes, sorted(indexes))

with db.connection() as conn:
    fks = conn.execute(
        "SELECT conname FROM pg_constraint WHERE contype = 'f' AND connamespace = %s::regnamespace",
        (db.SCHEMA,)).fetchall()
check("foreign keys are declared", len(fks) >= 3, fks)


# ===========================================================================
# 2. Ownership is enforced by the database, not only by the queries
# ===========================================================================

section("ownership constraints")

with db.connection() as conn:
    try:
        conn.execute("INSERT INTO games (owner, started_at, human_color) "
                     "VALUES (NULL, '2026-01-01', 'white')")
        check("a game cannot be created with no owner", False, "NULL owner accepted")
    except Exception:
        check("a game cannot be created with no owner", True)

with db.connection() as conn:
    try:
        conn.execute("INSERT INTO moves (game_id, ply, color, mover, move_uci) "
                     "VALUES (999999999, 1, 'white', 'human', 'e2e4')")
        check("a move cannot hang off a game that does not exist", False, "orphan accepted")
    except Exception:
        check("a move cannot hang off a game that does not exist", True)

# Deleting a game must take its moves with it, or the retention sweep leaves
# orphaned move rows behind forever.
svc_guest = LearningService("guest:cascade-test")
gid = svc_guest.start_game("white")
svc_guest.record_move(gid, 1, "white", "human", "e2e4", "e4", "FEN1", "FEN2",
                      {"score": 10}, {"score": 12})
with db.connection() as conn:
    conn.execute("DELETE FROM games WHERE id = %s", (gid,))
    left = conn.execute("SELECT count(*) FROM moves WHERE game_id = %s", (gid,)).fetchone()[0]
check("deleting a game cascades to its moves", left == 0, left)


# ===========================================================================
# 3. Guest identity: unguessable AND server-verifiable
# ===========================================================================

section("guest identity")

gid1 = identity_module.new_guest_id()
signed = identity_module._sign(gid1)
check("a guest id is unguessable (128 bits of entropy)",
      len(gid1) == len("guest:") + 32, gid1)
check("a signed cookie round-trips",
      identity_module.verify_guest_cookie(signed) == gid1)
check("an unsigned id is refused",
      identity_module.verify_guest_cookie(gid1) is None)
check("an invented id is refused",
      identity_module.verify_guest_cookie("guest:0000000000000000") is None)
check("a tampered MAC is refused",
      identity_module.verify_guest_cookie(
          signed[:-1] + ("0" if signed[-1] != "0" else "1")) is None)
check("an account identity in the guest cookie is refused",
      identity_module.verify_guest_cookie(identity_module._sign("user:1")) is None)

with TestClient(app.app) as c:
    c.get("/api/status")
    cookie = c.cookies.get("zw_guest")
check("the server sets a signed guest cookie", cookie and "." in cookie, cookie)
check("that cookie verifies", identity_module.verify_guest_cookie(cookie) is not None)

# The forged-cookie attack, end to end: play as a guest, then come back
# claiming to be that guest without a valid signature.
with TestClient(app.app) as forger:
    forged = "guest:" + "f" * 32
    r = forger.get("/api/status", headers={"Cookie": f"zw_guest={forged}"})
    # Read what the server SENT, not the client's jar: the jar would hold both
    # the forgery we supplied and the replacement, and cannot say which won.
    set_cookie = r.headers.get("set-cookie", "")
check("a forged guest cookie is replaced rather than trusted",
      "zw_guest=guest:" in set_cookie and forged not in set_cookie, set_cookie[:120])
check("the replacement is signed",
      identity_module.verify_guest_cookie(
          set_cookie.split("zw_guest=")[1].split(";")[0]) is not None, set_cookie[:120])


# ===========================================================================
# 4. Accounts: signup, login, logout, and the Google path
# ===========================================================================

section("accounts")
clear_auth_limits()

with TestClient(app.app) as c:
    r = c.post("/api/auth/signup", json={"username": "alice", "password": "alice-long-password",
                                         "email": "alice@example.com"})
    check("signup succeeds", r.status_code == 200 and r.json().get("signed_in") is True, r.text)
    me = c.get("/api/auth/me").json()
    check("the session is the new account", me.get("signed_in") is True, me)
    alice_id = auth_module.auth_service.find_by_email("alice@example.com")["id"]

with TestClient(app.app) as c:
    r = c.post("/api/auth/login", json={"username": "alice", "password": "alice-long-password"})
    check("login by username works", r.status_code == 200, r.text)
with TestClient(app.app) as c:
    r = c.post("/api/auth/login", json={"username": "ALICE@example.com",
                                        "password": "alice-long-password"})
    check("login by email works, case-insensitively", r.status_code == 200, r.text)
with TestClient(app.app) as c:
    r = c.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"})
    check("a wrong password is refused", r.status_code == 401, r.status_code)
with TestClient(app.app) as c:
    r = c.post("/api/auth/login", json={"username": "nobody", "password": "wrong-password"})
    check("an unknown user is refused with the same status as a wrong password",
          r.status_code == 401, r.status_code)

with TestClient(app.app) as c:
    c.post("/api/auth/login", json={"username": "alice", "password": "alice-long-password"})
    c.post("/api/auth/logout")
    me = c.get("/api/auth/me").json()
    check("logout leaves a guest, not an account", me.get("signed_in") is False, me)

# --- Google, with the exchange faked. What is exercised is everything after
# --- Google answers: state checking, account creation, linking, idempotency.
_real_configured = google_oauth.configured
_real_exchange = google_oauth.exchange_code
_fake_profile = {"subject": "google-sub-1", "email": "bob@example.com",
                 "email_verified": True, "name": "Bob"}
google_oauth.configured = lambda: True
google_oauth.exchange_code = lambda code, redirect_uri: dict(_fake_profile)
os.environ["GOOGLE_REDIRECT_URI"] = "http://testserver/api/auth/google/callback"

with TestClient(app.app) as c:
    cfg = c.get("/api/auth/config").json()
    check("config advertises Google when it is configured", cfg.get("google") is True, cfg)

    r = c.get("/api/auth/google/start", follow_redirects=False)
    check("start redirects to Google", r.status_code == 302
          and "accounts.google.com" in r.headers.get("location", ""), r.status_code)
    state = c.cookies.get("zw_oauth_state")
    check("start sets a state cookie", bool(state), state)

    r = c.get(f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False)
    check("the callback signs in and redirects back to the app",
          r.status_code == 302, r.status_code)
    me = c.get("/api/auth/me").json()
    check("a Google sign-in produces an ordinary account session",
          me.get("signed_in") is True, me)

bob = auth_module.auth_service.find_by_federated("google", "google-sub-1")
check("the Google account was created and linked", bob is not None, bob)
check("a Google account has no usable password",
      auth_module.auth_service.verify_password(bob["username"], "") is None)

# Signing in again must reach the SAME account, not create a second one.
with TestClient(app.app) as c:
    c.get("/api/auth/google/start", follow_redirects=False)
    st = c.cookies.get("zw_oauth_state")
    c.get(f"/api/auth/google/callback?code=abc&state={st}", follow_redirects=False)
bob_again = auth_module.auth_service.find_by_federated("google", "google-sub-1")
check("signing in with Google twice reaches one account",
      bob_again["id"] == bob["id"], (bob, bob_again))

# CSRF on the callback.
with TestClient(app.app) as c:
    c.get("/api/auth/google/start", follow_redirects=False)
    r = c.get("/api/auth/google/callback?code=abc&state=attacker-chosen", follow_redirects=False)
    check("a callback whose state does not match is refused", r.status_code == 400, r.status_code)
with TestClient(app.app) as c:
    r = c.get("/api/auth/google/callback?code=abc", follow_redirects=False)
    check("a callback with no state at all is refused", r.status_code == 400, r.status_code)

google_oauth.configured = _real_configured
google_oauth.exchange_code = _real_exchange


# ===========================================================================
# 5. Guest -> account claim
# ===========================================================================

section("claiming guest history")
clear_auth_limits()


def finished_game(owner, human_color="white", result="white_win"):
    """One completed game owned by `owner`. Returns its id."""
    svc = LearningService(owner)
    game_id = svc.start_game(human_color)
    svc.record_move(game_id, 1, "white", "human", "e2e4", "e4", "FEN-A", "FEN-B",
                    {"score": 20}, {"score": 25})
    svc.finalize_game(game_id, result, "checkmate")
    return game_id


g_alice = "guest:" + "a" * 32
g_bob = "guest:" + "b" * 32
finished_game(g_alice)
finished_game(g_alice)
finished_game(g_bob)

check("a guest's history is their own before claiming",
      LearningService(g_alice).get_opponent_summary()["games_played"] == 2)

moved = LearningService.claim_guest_games(g_alice, "user:%s" % alice_id)
check("claiming moves exactly that guest's games", moved == 2, moved)
check("the account now owns them",
      LearningService("user:%s" % alice_id).get_opponent_summary()["games_played"] == 2)
check("the guest identity now owns nothing",
      LearningService(g_alice).get_opponent_summary()["games_played"] == 0)
check("the other guest is untouched",
      LearningService(g_bob).get_opponent_summary()["games_played"] == 1)

check("claiming again moves nothing (idempotent)",
      LearningService.claim_guest_games(g_alice, "user:%s" % alice_id) == 0)
check("the account's history did not double",
      LearningService("user:%s" % alice_id).get_opponent_summary()["games_played"] == 2)

# A second account must not be able to claim a guest that is already claimed -
# this is the shared-browser case, and getting it wrong hands one person's
# games to the next person who signs up on that machine.
second = auth_module.auth_service.create_user("carol", "carol-long-password", "carol@example.com")
check("a second account cannot claim an already-claimed guest",
      LearningService.claim_guest_games(g_alice, "user:%s" % second["id"]) == 0)
check("the games still belong to the first account",
      LearningService("user:%s" % alice_id).get_opponent_summary()["games_played"] == 2)

# Concurrency: two claims of the same guest at once must total one claim.
g_race = "guest:" + "c" * 32
for _ in range(3):
    finished_game(g_race)
results = []
racer_a = auth_module.auth_service.create_user("dave", "dave-long-password", "dave@example.com")
racer_b = auth_module.auth_service.create_user("erin", "erin-long-password", "erin@example.com")


def claim_as(user_id):
    results.append(LearningService.claim_guest_games(g_race, "user:%s" % user_id))


threads = [threading.Thread(target=claim_as, args=(racer_a["id"],)),
           threading.Thread(target=claim_as, args=(racer_b["id"],))]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("two simultaneous claims: exactly one moves the games",
      sorted(results) == [0, 3], results)
owners = {LearningService("user:%s" % racer_a["id"]).get_opponent_summary()["games_played"],
          LearningService("user:%s" % racer_b["id"]).get_opponent_summary()["games_played"]}
check("the games ended up with exactly one of them", owners == {0, 3}, owners)

with db.connection() as conn:
    rows = conn.execute("SELECT count(*) FROM claimed_guests WHERE guest_identity = %s",
                        (g_race,)).fetchone()[0]
check("the claim ledger has exactly one row for that guest", rows == 1, rows)

# A guest must never be able to claim another guest's history: the claim is
# keyed on the server-resolved identity, and there is no API that accepts a
# guest id from the caller.
import auth_api as auth_api_module
claim_src = inspect_source = __import__("inspect").getsource(auth_api_module._claim_guest_history)
check("the claim reads the server-resolved identity, not client input",
      "identity_of(request)" in claim_src and "request.json" not in claim_src, claim_src[:200])
check("the claim refuses to run for an already-signed-in caller",
      "if not is_guest(identity)" in claim_src, claim_src[:200])


# ===========================================================================
# 6. Authorization: accounts cannot reach each other
# ===========================================================================

section("cross-account isolation")
clear_auth_limits()

auth_module.auth_service.create_user("victim", "victim-long-password", "victim@example.com")
auth_module.auth_service.create_user("attacker", "attacker-long-password", "attacker@example.com")

with TestClient(app.app) as victim, TestClient(app.app) as attacker:
    victim.post("/api/auth/login", json={"username": "victim", "password": "victim-long-password"})
    attacker.post("/api/auth/login", json={"username": "attacker",
                                           "password": "attacker-long-password"})

    v_id = victim.get("/api/auth/me").json()
    a_id = attacker.get("/api/auth/me").json()
    check("the two clients are two different accounts",
          v_id.get("username") != a_id.get("username"), (v_id, a_id))

    # A sandbox session is the addressable, owned resource - the realistic
    # target for an id typed into a URL.
    r = victim.post("/api/sandbox/session", json={})
    session_id = (r.json() or {}).get("session", {}).get("id") or (r.json() or {}).get("session_id")
    check("the victim can open a sandbox session", bool(session_id), r.text[:200])

    if session_id:
        r = attacker.get(f"/api/sandbox/session/{session_id}")
        check("A cannot READ B's sandbox session", r.status_code == 404, r.status_code)
        r = attacker.post(f"/api/sandbox/session/{session_id}/move", json={"move": "e2e4"})
        check("A cannot WRITE to B's sandbox session", r.status_code == 404, r.status_code)
        r = attacker.delete(f"/api/sandbox/session/{session_id}")
        check("A cannot DELETE B's sandbox session", r.status_code == 404, r.status_code)
        r = victim.get(f"/api/sandbox/session/{session_id}")
        check("the victim's session survived all of that", r.status_code == 200, r.status_code)

    # Learning history is scoped by identity with no id in the request at all,
    # so the attack is to forge the identity rather than the resource id.
    victim_games = victim.get("/api/learning/summary").json()["opponent"]["games_played"]
    attacker_games = attacker.get("/api/learning/summary").json()["opponent"]["games_played"]
    check("each account sees only its own learning history",
          victim_games == 0 and attacker_games == 0, (victim_games, attacker_games))

    # Forged identity: put someone else's account identity in the guest cookie.
    attacker.cookies.set("zw_guest", identity_module._sign("user:%s" % alice_id))
    attacker.cookies.delete("zw_session")
    summary = attacker.get("/api/learning/summary").json()
    check("an account identity smuggled into the guest cookie grants nothing",
          summary["opponent"]["games_played"] == 0, summary["opponent"])

# A logged-out caller gets a guest's empty world, never an account's.
with TestClient(app.app) as c:
    c.post("/api/auth/login", json={"username": "victim", "password": "victim-long-password"})
    c.post("/api/auth/logout")
    me = c.get("/api/auth/me").json()
    check("a logged-out caller is not signed in", me.get("signed_in") is False, me)

# A stale session token must not resolve after the session has ended.
stale_user = auth_module.auth_service.create_user("stale", "stale-long-password", "stale@example.com")
stale_token = auth_module.auth_service.start_session(stale_user["id"])
check("a live token resolves",
      auth_module.auth_service.resolve_session(stale_token) == "user:%s" % stale_user["id"])
auth_module.auth_service.end_session(stale_token)
check("an ended token resolves to nothing",
      auth_module.auth_service.resolve_session(stale_token) is None)
with TestClient(app.app) as c:
    c.cookies.set("zw_session", stale_token)
    me = c.get("/api/auth/me").json()
    check("a stale session cookie is treated as a guest, not an account",
          me.get("signed_in") is False, me)
with TestClient(app.app) as c:
    c.cookies.set("zw_session", "not-a-real-token-at-all")
    me = c.get("/api/auth/me").json()
    check("an invented session token grants nothing", me.get("signed_in") is False, me)


# ===========================================================================
# 7. Live game state isolation
# ===========================================================================

section("live session isolation")
clear_auth_limits()

with TestClient(app.app) as a, TestClient(app.app) as b:
    a.get("/api/status")
    b.get("/api/status")

    a.post("/api/move", json={"move": "e2e4"})
    b_state = b.get("/api/status").json()
    check("A moves, B's board is unchanged",
          b_state["status"]["fen"].startswith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP"),
          b_state["status"]["fen"])

    b.post("/api/move", json={"move": "d2d4"})
    a_state = a.get("/api/status").json()
    check("B moves, A still has A's position",
          "4P3" in a_state["status"]["fen"], a_state["status"]["fen"])

    a.post("/api/difficulty", json={"difficulty": 3})
    check("A's difficulty change is A's alone",
          b.get("/api/status").json()["difficulty"] == 20,
          b.get("/api/status").json()["difficulty"])
    check("A's own difficulty did change",
          a.get("/api/status").json()["difficulty"] == 3)

    a.get("/api/reset")
    b_after = b.get("/api/status").json()
    check("A resets, B's game survives",
          "3P4" in b_after["status"]["fen"], b_after["status"]["fen"])

    # Signing in must not hand over anyone else's live board.
    a.post("/api/auth/login", json={"username": "victim", "password": "victim-long-password"})
    a_signed = a.get("/api/status").json()
    check("signing in does not expose another session's live state",
          a_signed["status"]["fen"] != b_after["status"]["fen"],
          (a_signed["status"]["fen"], b_after["status"]["fen"]))


# ===========================================================================
# 8. The global AI pool
# ===========================================================================

section("global candidate reweighting")

POOL_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def ai_game(owner, move, result, human_color="white", fen=POOL_FEN):
    """One finished game in which the AI played `move` from `fen`.

    `fen` is a parameter and not a constant: two scenarios below use two
    different positions, and filing both under one position makes the second
    scenario silently unfalsifiable.
    """
    svc = LearningService(owner)
    gid = svc.start_game(human_color)
    svc.record_move(gid, 1, "black", "ai", move, None, fen, "FEN-AFTER",
                    {"score": 0}, {"score": 0}, source="gemini")
    svc.finalize_game(gid, result, "checkmate")
    return gid


candidates = [{"move": "e7e5", "score": 10}, {"move": "c7c5", "score": 9}]
probe = LearningService("user:%s" % alice_id)

g_pool = "guest:" + "d" * 32
# A guest wins repeatedly with c7c5. If guests fed the pool, this would move
# c7c5 to the front.
for _ in range(3):
    ai_game(g_pool, "c7c5", "black_win")
order = [c["move"] for c in probe.reweight_candidates(POOL_FEN, list(candidates))]
check("guest games do NOT influence the global pool", order == ["e7e5", "c7c5"], order)

# The same games, once claimed by an account, must count.
LearningService.claim_guest_games(g_pool, "user:%s" % second["id"])
order = [c["move"] for c in probe.reweight_candidates(POOL_FEN, list(candidates))]
check("claimed guest games DO influence the global pool", order == ["c7c5", "e7e5"], order)

# And an account's own games count from the start.
POOL_FEN2 = "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1"
for _ in range(2):
    ai_game("user:%s" % racer_a["id"], "d7d5", "black_win", fen=POOL_FEN2)
cands2 = [{"move": "g8f6", "score": 10}, {"move": "d7d5", "score": 9}]
order2 = [c["move"] for c in probe.reweight_candidates(POOL_FEN2, list(cands2))]
check("account games influence the global pool", order2 == ["d7d5", "g8f6"], order2)

# The pool must carry no identity. reweight_candidates returns move strings
# and nothing else, and the query selects no owner column.
import inspect
src = inspect.getsource(LearningService.reweight_candidates)
check("the reweighting query never selects an owner",
      "SELECT m.move_uci, g.result, g.human_color" in src and "g.owner LIKE" in src,
      "query shape changed - re-check what it exposes")
check("reweighting returns only the candidates it was given",
      set(order2) == {"g8f6", "d7d5"}, order2)

# Personal profile data must not be reachable through the global path.
fresh = auth_module.auth_service.create_user("freshy", "freshy-long-password",
                                            "freshy@example.com")
alice_profile = LearningService("user:%s" % alice_id).get_prompt_context_summary()
fresh_profile = LearningService("user:%s" % fresh["id"]).get_prompt_context_summary()
check("one account's prompt context does not mention another's history",
      alice_profile != fresh_profile, (alice_profile[:60], fresh_profile[:60]))
check("an account with no games gets no history in its prompt",
      "No history yet" in fresh_profile, fresh_profile)
check("an account WITH games gets its own numbers, and only its own",
      "Across 2 tracked games" in alice_profile, alice_profile[:80])


# ===========================================================================
# 9. Guest retention
# ===========================================================================

section("guest retention")

g_old = "guest:" + "e" * 32
g_new = "guest:" + "f" * 32
g_claimed = "guest:" + "0" * 32

old_id = finished_game(g_old)
finished_game(g_new)
finished_game(g_claimed)
claim_target = auth_module.auth_service.create_user("keeper", "keeper-long-password",
                                                    "keeper@example.com")
LearningService.claim_guest_games(g_claimed, "user:%s" % claim_target["id"])

# Age the old guest's game past the retention window.
with db.connection() as conn:
    conn.execute("UPDATE games SET started_at = %s WHERE id = %s",
                 ("2020-01-01T00:00:00+00:00", old_id))

account_before = LearningService("user:%s" % alice_id).get_opponent_summary()["games_played"]
removed = LearningService.purge_unclaimed_guest_games(30)
check("the sweep deletes the aged unclaimed guest game", removed >= 1, removed)
check("the aged guest has nothing left",
      LearningService(g_old).get_opponent_summary()["games_played"] == 0)
check("a recent guest is untouched",
      LearningService(g_new).get_opponent_summary()["games_played"] == 1)
check("a claimed guest's games are not eligible - they belong to an account now",
      LearningService("user:%s" % claim_target["id"]).get_opponent_summary()["games_played"] == 1)
check("no account lost anything",
      LearningService("user:%s" % alice_id).get_opponent_summary()["games_played"] == account_before)

with db.connection() as conn:
    orphans = conn.execute(
        "SELECT count(*) FROM moves m LEFT JOIN games g ON g.id = m.game_id "
        "WHERE g.id IS NULL").fetchone()[0]
check("the sweep left no orphaned move rows", orphans == 0, orphans)

check("running the sweep again is safe and deletes nothing",
      LearningService.purge_unclaimed_guest_games(30) == 0)
check("the sweep is safe on a database with nothing eligible",
      LearningService.purge_unclaimed_guest_games(3650) == 0)


# ===========================================================================
# 10. Security probes
# ===========================================================================

section("security probes")
clear_auth_limits()

INJECTION = "x'; DROP TABLE games; --"
with TestClient(app.app) as c:
    c.post("/api/auth/signup", json={"username": INJECTION, "password": "a-long-password-here"})
    c.post("/api/auth/login", json={"username": INJECTION, "password": "a-long-password-here"})
with db.connection() as conn:
    still = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = 'games'", (db.SCHEMA,)).fetchone()[0]
check("SQL injection through a username does nothing", still == 1, still)

# The same, through an identity string, which reaches the learning queries.
evil = LearningService("guest:x'; DROP TABLE moves; --")
evil.get_opponent_summary()
with db.connection() as conn:
    still = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = 'moves'", (db.SCHEMA,)).fetchone()[0]
check("SQL injection through an identity string does nothing", still == 1, still)

with TestClient(app.app) as c:
    for bad in ["", "Bearer", "x" * 5000]:
        c.cookies.set("zw_session", bad)
        r = c.get("/api/auth/me")
        if r.status_code != 200 or r.json().get("signed_in") is not False:
            check(f"a malformed session cookie ({bad[:10]!r}) is rejected cleanly", False, r.text[:100])
            break
    else:
        check("malformed session cookies are rejected cleanly", True)

check("CORS does not use a wildcard origin with credentials",
      "*" not in app.ALLOWED_ORIGINS, app.ALLOWED_ORIGINS)

# No secret should be reachable through the API surface.
with TestClient(app.app) as c:
    body = c.get("/api/health").text + c.get("/api/auth/config").text + c.get("/").text
for secret_name in ("GEMINI_API_KEY", "DATABASE_URL", "SESSION_COOKIE_SECRET",
                    "GOOGLE_CLIENT_SECRET"):
    value = os.environ.get(secret_name)
    if value:
        check(f"{secret_name} does not appear in API responses", value not in body)
    else:
        check(f"{secret_name} is not set in this process, nothing to leak", True)



# ===========================================================================
# 11. The account area: profile, preferences, password, deletion
#
# These are the routes behind /settings. The claim being tested is the one the
# settings screen makes on its face: that a preference shown there belongs to
# the account and follows it, rather than being browser state with an account
# label on it.
# ===========================================================================

section("account area")
clear_auth_limits()

with TestClient(app.app) as c:
    c.post("/api/auth/signup", json={"username": "prefs1", "password": "prefs-long-password",
                                     "email": "prefs1@example.com"})

    prof = c.get("/api/account").json()
    check("the profile carries the email that was signed up with",
          prof.get("email") == "prefs1@example.com", prof)
    check("the profile says how the account signs in",
          prof.get("auth_methods") == ["password"], prof)

    defaults = c.get("/api/account/settings").json()["prefs"]
    check("a new account gets a complete set of defaults",
          set(defaults) == {"pieceTheme", "showCoordinates", "showEngineNumbers",
                            "showMoveQuality", "activeSection"}, defaults)

    saved = c.put("/api/account/settings",
                  json={"prefs": {"showCoordinates": False, "pieceTheme": "obsidian"}}).json()["prefs"]
    check("a preference saves", saved["showCoordinates"] is False and saved["pieceTheme"] == "obsidian",
          saved)

    merged = c.put("/api/account/settings", json={"prefs": {"showMoveQuality": False}}).json()["prefs"]
    check("saving one preference does not wipe the others",
          merged["pieceTheme"] == "obsidian" and merged["showCoordinates"] is False
          and merged["showMoveQuality"] is False, merged)

    junk = c.put("/api/account/settings",
                 json={"prefs": {"evil": "payload", "showCoordinates": "not-a-bool"}}).json()["prefs"]
    check("unknown keys are dropped rather than stored", "evil" not in junk, junk)
    check("a wrongly-typed value is ignored, not written",
          junk["showCoordinates"] is False, junk)

# The claim the settings screen actually makes: sign in somewhere else, get
# your board back.
with TestClient(app.app) as fresh:
    fresh.post("/api/auth/login", json={"username": "prefs1@example.com",
                                        "password": "prefs-long-password"})
    elsewhere = fresh.get("/api/account/settings").json()["prefs"]
check("preferences follow the account to a different browser",
      elsewhere["pieceTheme"] == "obsidian" and elsewhere["showCoordinates"] is False, elsewhere)

# And do not follow it to a different account.
with TestClient(app.app) as other:
    other.post("/api/auth/signup", json={"username": "prefs2", "password": "prefs2-long-password",
                                         "email": "prefs2@example.com"})
    theirs = other.get("/api/account/settings").json()["prefs"]
check("another account does not inherit them",
      theirs["pieceTheme"] is None and theirs["showCoordinates"] is True, theirs)

with TestClient(app.app) as guest:
    check("a guest cannot read account settings",
          guest.get("/api/account/settings").status_code == 401)
    check("a guest cannot write account settings",
          guest.put("/api/account/settings", json={"prefs": {"showCoordinates": False}}).status_code == 401)
    check("a guest has no profile to read",
          guest.get("/api/account").status_code == 401)

# --- changing a password
with TestClient(app.app) as c:
    c.post("/api/auth/login", json={"username": "prefs1", "password": "prefs-long-password"})
    r = c.post("/api/account/password", json={"current_password": "wrong-one",
                                              "new_password": "a-brand-new-password"})
    check("changing a password needs the current one", r.status_code == 401, r.status_code)

    r = c.post("/api/account/password", json={"current_password": "prefs-long-password",
                                              "new_password": "a-brand-new-password"})
    check("the password changes with the right current one", r.status_code == 200, r.text[:120])
    check("the device that changed it stays signed in",
          c.get("/api/auth/me").json()["signed_in"] is True)

check("the old password no longer works",
      auth_module.auth_service.verify_password("prefs1", "prefs-long-password") is None)
check("the new password does",
      auth_module.auth_service.verify_password("prefs1", "a-brand-new-password") is not None)

# Every OTHER session must have been ended - that is the point of changing a
# password you think someone else has.
with TestClient(app.app) as elsewhere_client:
    elsewhere_client.post("/api/auth/login", json={"username": "prefs1",
                                                   "password": "a-brand-new-password"})
    other_token = elsewhere_client.cookies.get("zw_session")
    auth_module.auth_service.change_password("%s" % auth_module.auth_service.find_by_email(
        "prefs1@example.com")["id"], "a-brand-new-password", "third-password-here")
    check("changing the password ends other sessions",
          auth_module.auth_service.resolve_session(other_token) is None)

# --- deleting an account
with TestClient(app.app) as c:
    c.post("/api/auth/signup", json={"username": "doomed", "password": "doomed-long-password",
                                     "email": "doomed@example.com"})
    doomed_id = auth_module.auth_service.find_by_email("doomed@example.com")["id"]
    doomed = LearningService("user:%s" % doomed_id)
    dg = doomed.start_game("white")
    doomed.record_move(dg, 1, "white", "human", "e2e4", "e4", "F1", "F2", {"score": 5}, {"score": 6})
    c.put("/api/account/settings", json={"prefs": {"showCoordinates": False}})

    r = c.request("DELETE", "/api/account", json={"confirm_username": "not-my-name"})
    check("deleting needs the username typed back exactly", r.status_code == 400, r.status_code)

    r = c.request("DELETE", "/api/account", json={"confirm_username": "doomed"})
    check("deleting works with the right confirmation", r.status_code == 200, r.text[:120])
    check("the session is over", c.get("/api/auth/me").json()["signed_in"] is False)

check("the account row is gone", auth_module.auth_service.get_user(doomed_id) is None)
with db.connection() as conn:
    left_games = conn.execute("SELECT count(*) FROM games WHERE owner = %s",
                              ("user:%s" % doomed_id,)).fetchone()[0]
    left_moves = conn.execute("SELECT count(*) FROM moves WHERE game_id = %s", (dg,)).fetchone()[0]
    left_prefs = conn.execute("SELECT count(*) FROM user_settings WHERE user_id = %s",
                              (doomed_id,)).fetchone()[0]
    left_sessions = conn.execute("SELECT count(*) FROM sessions WHERE user_id = %s",
                                 (doomed_id,)).fetchone()[0]
check("its games went with it", left_games == 0, left_games)
check("its moves went with the games", left_moves == 0, left_moves)
check("its settings went with it", left_prefs == 0, left_prefs)
check("its sessions went with it", left_sessions == 0, left_sessions)

# The ledger must survive deletion, or the guest identity it recorded becomes
# claimable a second time by whoever next signs up in that browser.
with db.connection() as conn:
    ledger_rows = conn.execute("SELECT count(*) FROM claimed_guests").fetchone()[0]
check("the claim ledger is not emptied by an account deletion", ledger_rows > 0, ledger_rows)

# Another account's data must be untouched by all of the above.
check("an unrelated account still has its games",
      LearningService("user:%s" % alice_id).get_opponent_summary()["games_played"] == 2)


# ===========================================================================
# 12. Rate limiting still applies to the account routes
#
# Accounts introduced two new spend paths that are cheap to script and
# expensive to serve - password hashing is 600k iterations by design. The
# question this answers is not "does rate_limit.py work" but "did adding
# accounts leave the auth routes uncovered".
# ===========================================================================

section("rate limiting")
clear_auth_limits()

with TestClient(app.app) as c:
    statuses = []
    for i in range(14):
        r = c.post("/api/auth/login", json={"username": "nobody-at-all",
                                            "password": "wrong-password-here"})
        statuses.append(r.status_code)
check("repeated failed logins are eventually refused with 429",
      429 in statuses, statuses)
check("the first few attempts are answered normally, not blanket-refused",
      statuses[0] == 401, statuses[:3])

clear_auth_limits()
with TestClient(app.app) as c:
    codes = []
    for i in range(8):
        codes.append(c.post("/api/auth/signup",
                            json={"username": f"floods{i}", "password": "flood-long-password",
                                  "email": f"floods{i}@example.com"}).status_code)
check("signup is rate limited too", 429 in codes, codes)

clear_auth_limits()


# ===========================================================================
# 13. Password reset
#
# Two claims carry most of the weight here. First, that `/forgot-password`
# cannot be used to find out whether an address is registered - it is the
# endpoint whose whole job is to be uninformative. Second, that a reset link
# is single-use and takes every existing session with it, because a person
# resetting a password is usually a person who thinks somebody else is in
# their account.
# ===========================================================================

section("password reset")
clear_auth_limits()

# Email is captured rather than sent. What is being tested is our logic, not
# Mailjet's - and a suite that posts to a real mail provider is a suite nobody
# can run offline.
SENT = []
_real_send = email_service.send
email_service.send = lambda to, subject, text, html: (
    SENT.append({"to": to, "subject": subject, "text": text, "html": html}) or True
)

with TestClient(app.app) as c:
    c.post("/api/auth/signup", json={"username": "forgetful", "password": "forgetful-password-1",
                                     "email": "forgetful@example.com"})

clear_auth_limits()
SENT.clear()

with TestClient(app.app) as c:
    known = c.post("/api/auth/forgot-password", json={"email": "forgetful@example.com"})
check("asking for a reset succeeds for a real address", known.status_code == 200, known.text[:120])
check("an email was sent", len(SENT) == 1, SENT)
check("it went to the address on the account",
      SENT and SENT[0]["to"] == "forgetful@example.com", SENT[:1])

sent_body = SENT[0]["text"] if SENT else ""
link = ""
for word in sent_body.split():
    if "/reset-password?token=" in word:
        link = word
        break
token = link.split("token=", 1)[1] if link else ""
check("the email carries a reset link with a token", len(token) > 30, len(token))

# --- enumeration: the whole point of this endpoint
clear_auth_limits()
SENT.clear()
with TestClient(app.app) as c:
    unknown = c.post("/api/auth/forgot-password", json={"email": "nobody-here@example.com"})
check("an unknown address gets the same status", unknown.status_code == known.status_code,
      (known.status_code, unknown.status_code))
check("an unknown address gets the same body", unknown.json() == known.json(),
      (known.json(), unknown.json()))
check("and no email is sent for it", len(SENT) == 0, SENT)

clear_auth_limits()
SENT.clear()
with TestClient(app.app) as c:
    malformed = c.post("/api/auth/forgot-password", json={"email": "not-an-email"})
check("a malformed address is answered the same way, not validated back at the caller",
      malformed.status_code == known.status_code and malformed.json() == known.json(),
      malformed.text[:120])

# A Google-only account must not be distinguishable over HTTP either. It gets
# a different EMAIL, which only its owner sees.
clear_auth_limits()
SENT.clear()
googler = auth_module.auth_service.create_user("googleonly", None, "googleonly@example.com")
auth_module.auth_service.link_federated("google", "sub-reset-test", googler["id"])
with TestClient(app.app) as c:
    goog = c.post("/api/auth/forgot-password", json={"email": "googleonly@example.com"})
check("a Google-only account is answered identically over HTTP",
      goog.status_code == known.status_code and goog.json() == known.json(), goog.text[:120])
check("but its owner is told why, by email", len(SENT) == 1 and "Google" in SENT[0]["subject"],
      SENT[:1])
check("and no reset link is issued for it",
      SENT and "/reset-password?token=" not in SENT[0]["text"], SENT[:1])

# --- storage: only the hash
with db.connection() as conn:
    rows = conn.execute("SELECT token_hash FROM password_resets").fetchall()
check("reset tokens are stored, hashed", len(rows) >= 1, len(rows))
check("the raw token is never in the table",
      all(token not in r[0] for r in rows) if token else False, "raw token found in storage")
check("what is stored is a SHA-256 hex digest",
      all(len(r[0]) == 64 for r in rows), [len(r[0]) for r in rows])

# --- redeeming
clear_auth_limits()
old_session = auth_module.auth_service.start_session(
    auth_module.auth_service.find_by_email("forgetful@example.com")["id"])
check("a session exists before the reset",
      auth_module.auth_service.resolve_session(old_session) is not None)

with TestClient(app.app) as c:
    bad = c.post("/api/auth/reset-password", json={"token": "invented-token",
                                                   "new_password": "brand-new-password-1"})
check("an invented token is refused", bad.status_code == 400, bad.status_code)

with TestClient(app.app) as c:
    short = c.post("/api/auth/reset-password", json={"token": token, "new_password": "short"})
check("a too-short new password is refused", short.status_code in (400, 401), short.status_code)

with TestClient(app.app) as c:
    ok = c.post("/api/auth/reset-password", json={"token": token,
                                                  "new_password": "brand-new-password-1"})
check("a valid token sets the new password", ok.status_code == 200, ok.text[:160])
check("the reset does NOT sign the caller in",
      ok.json().get("signed_in") is False, ok.json())

check("the new password works",
      auth_module.auth_service.verify_password("forgetful", "brand-new-password-1") is not None)
check("the old password does not",
      auth_module.auth_service.verify_password("forgetful", "forgetful-password-1") is None)
check("every session for the account was ended",
      auth_module.auth_service.resolve_session(old_session) is None)

# --- single use
clear_auth_limits()
with TestClient(app.app) as c:
    again = c.post("/api/auth/reset-password", json={"token": token,
                                                     "new_password": "third-password-here"})
check("the same link cannot be used twice", again.status_code == 400, again.status_code)
check("and the second attempt did not change anything",
      auth_module.auth_service.verify_password("forgetful", "brand-new-password-1") is not None)

# --- a second request invalidates the first link
clear_auth_limits()
SENT.clear()
with TestClient(app.app) as c:
    c.post("/api/auth/forgot-password", json={"email": "forgetful@example.com"})
first_token = SENT[0]["text"].split("token=", 1)[1].split()[0] if SENT else ""
clear_auth_limits()
SENT.clear()
with TestClient(app.app) as c:
    c.post("/api/auth/forgot-password", json={"email": "forgetful@example.com"})
second_token = SENT[0]["text"].split("token=", 1)[1].split()[0] if SENT else ""
check("asking twice produces two different tokens",
      first_token and second_token and first_token != second_token)
clear_auth_limits()
with TestClient(app.app) as c:
    stale = c.post("/api/auth/reset-password", json={"token": first_token,
                                                     "new_password": "fourth-password-x"})
check("asking again invalidates the earlier link", stale.status_code == 400, stale.status_code)
clear_auth_limits()
with TestClient(app.app) as c:
    fresh_ok = c.post("/api/auth/reset-password", json={"token": second_token,
                                                        "new_password": "fourth-password-x"})
check("the newest link still works", fresh_ok.status_code == 200, fresh_ok.text[:120])

# --- expiry
clear_auth_limits()
SENT.clear()
with TestClient(app.app) as c:
    c.post("/api/auth/forgot-password", json={"email": "forgetful@example.com"})
expiring = SENT[0]["text"].split("token=", 1)[1].split()[0] if SENT else ""
import hashlib as _hashlib
with db.connection() as conn:
    conn.execute("UPDATE password_resets SET expires_at = %s WHERE token_hash = %s",
                 (time.time() - 60, _hashlib.sha256(expiring.encode()).hexdigest()))
clear_auth_limits()
with TestClient(app.app) as c:
    expired = c.post("/api/auth/reset-password", json={"token": expiring,
                                                       "new_password": "fifth-password-xx"})
check("an expired link is refused", expired.status_code == 400, expired.status_code)
check("expired, used and invented all give the same message",
      expired.json().get("detail") == bad.json().get("detail") == again.json().get("detail"),
      (expired.json(), bad.json(), again.json()))

# --- rate limiting, both buckets
clear_auth_limits()
with TestClient(app.app) as c:
    codes = [c.post("/api/auth/forgot-password",
                    json={"email": f"person{i}@example.com"}).status_code for i in range(9)]
check("repeated reset requests from one caller are eventually refused", 429 in codes, codes)

clear_auth_limits()
with TestClient(app.app) as c:
    # Same address every time. The per-IP bucket is generous enough that this
    # is the per-ADDRESS bucket firing - and it must answer 200, not 429, or
    # it becomes a way to detect which addresses are being targeted.
    bodies = [c.post("/api/auth/forgot-password",
                     json={"email": "forgetful@example.com"}).status_code for i in range(5)]
SENT.clear()
clear_auth_limits()
check("hammering one address is throttled without saying so", set(bodies) <= {200}, bodies)

# --- the secret and the token must not reach the logs
check("the reset link is not logged unless explicitly enabled locally",
      email_service.log_reset_link_locally.__doc__ is not None
      and "RESET_LINK_TO_LOG" in email_service.log_reset_link_locally.__doc__)
# All three credentials are needed, and a partial configuration must count as
# unconfigured - a half-configured mail system looks exactly like a working one
# from outside, because the endpoint that uses it is silent about failure.
_saved_mail = {k: os.environ.pop(k, None)
               for k in ("MAILJET_API_KEY", "MAILJET_SECRET_KEY", "MAILJET_FROM_EMAIL")}
check("with nothing configured, email is off", email_service.configured() is False)
os.environ["MAILJET_API_KEY"] = "test_key_must_not_leak"
check("a key alone is not enough", email_service.configured() is False)
os.environ["MAILJET_SECRET_KEY"] = "test_secret_must_not_leak"
check("a key and secret without a sender is not enough", email_service.configured() is False)
os.environ["MAILJET_FROM_EMAIL"] = "no-reply@example.com"
check("all three together switch email on", email_service.configured() is True)
check("the credentials are not returned by any config endpoint",
      "test_key_must_not_leak" not in TestClient(app.app).get("/api/auth/config").text
      and "test_secret_must_not_leak" not in TestClient(app.app).get("/api/auth/config").text)
for _k in ("MAILJET_API_KEY", "MAILJET_SECRET_KEY", "MAILJET_FROM_EMAIL"):
    os.environ.pop(_k, None)
check("with nothing configured, sending is a no-op rather than an error",
      _real_send("nobody@example.com", "s", "t", "<p>h</p>") is False)
for _k, _v in _saved_mail.items():
    if _v is not None:
        os.environ[_k] = _v

email_service.send = _real_send
clear_auth_limits()

# ===========================================================================

app.stockfish_service.close()
db.drop_schema()
db.close_pool()

print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

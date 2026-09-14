"""
Practice from a profile theme: POST /api/profile/mistakes/{theme}/practice
and POST /api/profile/practice/attempt.

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_pp_$$ \\
        /tmp/chessapp/bin/python test_profile_practice.py

Needs DATABASE_URL and a disposable schema (dropped on the way out).

What is proved: practice is account-gated and owner-scoped; the position is
one of the theme's own stored `fen_before` rows, with the player to move and
the engine's move withheld; the first move is graded against that engine
move and then revealed; a second grading is refused; a theme with no stored
position gets the honest unavailable answer, never an invented one; both
events are recorded with theme and finding.
"""
import os

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ["ACCOUNTS_ENABLED"] = "true"
os.environ.setdefault("SESSION_COOKIE_SECRET", "test-signing-key-not-a-real-secret")
if os.environ.get("DATABASE_SCHEMA", "public") == "public":
    raise SystemExit("Refusing to run against the public schema - set DATABASE_SCHEMA to a disposable name.")

import json

from fastapi.testclient import TestClient

import app
import db
import learning_events
import profile_api
import profile_service as ps
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


def clear_limits():
    rate_limit_module.limit_login.limiter.reset()
    rate_limit_module.limit_signup.limiter.reset()
    for dep in (profile_api.limit_import, profile_api.limit_read):
        dep.limiter.reset()


db.migrate()

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
PGN = '[Event "x"]\n[White "alice"]\n[Black "bob"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0\n'
THEME = "TACTICAL_OVERLOOK"


def seed(owner, n, fen=FEN, theme=THEME):
    for i in range(n):
        gid = ps.add_game(owner, PGN.replace("a6", f"a6 {{{theme}{i}}}"), {"white": "alice", "black": "bob", "result": "1-0", "date": "2026.09.10"},
                          "white", 6, source="chesscom", external_id=f"{theme}-{i}", time_control="300")
        ps.record_findings(gid, owner, [{
            "ply": 5, "theme": theme, "severity": "major", "cpl": 100 + i, "fen_before": fen,
            "move_san": "Bb5", "best_san": "d4", "phase": "opening",
        }])


section("guest")
with TestClient(app.app) as guest:
    check("guest practice -> 401", guest.post(f"/api/profile/mistakes/{THEME}/practice").status_code == 401)
    check("guest attempt -> 401", guest.post("/api/profile/practice/attempt", json={"session_id": "x", "uci": "d2d4"}).status_code == 401)

section("alice practises her own mistake")
clear_limits()
with TestClient(app.app) as alice:
    alice.post("/api/auth/signup", json={"username": "alice", "password": "alice-password-1", "email": "alice@example.com"})
    with db.connection() as conn:
        owner = "user:%d" % conn.execute("SELECT id FROM users WHERE username = 'alice'").fetchone()[0]
    seed(owner, 10)
    # One theme with findings but no snapshot: practice must be refused honestly.
    with db.connection() as conn:
        for i in range(3):
            gid = ps.add_game(owner, PGN.replace("a6", f"a6 {{ks{i}}}"), {"white": "alice", "black": "bob", "result": "1-0"}, "white", 6,
                              source="lichess", external_id=f"ks-{i}")
            ps.record_findings(gid, owner, [{"ply": 5, "theme": "KING_SAFETY", "severity": "major", "cpl": 50, "fen_before": FEN,
                                             "move_san": "Bb5", "best_san": "d4", "phase": "opening"}])
        # The column is NOT NULL, so "missing" is an empty snapshot.
        conn.execute("UPDATE game_findings SET fen_before = '' WHERE theme = 'KING_SAFETY'")

    learning_events.events.clear()
    check("unknown theme -> 404", alice.post("/api/profile/mistakes/NOT_A_THEME/practice").status_code == 404)

    r = alice.post(f"/api/profile/mistakes/{THEME}/practice")
    check("practice -> 200 available", r.status_code == 200 and r.json()["available"] is True, r.text[:300])
    p = r.json()
    check("the position is the stored fen_before, white to move", p["fen"] == FEN and p["side_to_move"] == "white")
    check("mode learn, theme named, instructions given", p["mode"] == "learn" and p["theme_id"] == THEME and p["theme"] and p["instructions"])
    check("source evidence names the game and the move", p["source_evidence"]["game_label"].startswith("Chess.com · you as White vs bob · Blitz 5+0")
          and p["source_evidence"]["move_label"] == "3. Bb5" and p["source_evidence"]["played_san"] == "Bb5", p["source_evidence"])
    check("the engine's move is NOT in the response", "d4" not in json.dumps(p["source_evidence"]) and "best_san" not in r.text
          and "best" not in json.dumps(p["session"]["practice"]))
    sid = p["practice_session_id"]
    s = alice.get(f"/api/sandbox/session/{sid}").json()
    check("the sandbox session exists, at that FEN, with the practice brief", s["fen"] == FEN and s["practice"]["theme"] == THEME
          and s["practice"]["attempted"] is False and "best" not in json.dumps(s["practice"]), s.get("practice"))
    check("its title says what it is", s["title"].startswith("Practice:"), s["title"])

    section("grading the first move")
    r = alice.post("/api/profile/practice/attempt", json={"session_id": sid, "uci": "z9z9"})
    check("nonsense -> 400", r.status_code == 400)
    r = alice.post("/api/profile/practice/attempt", json={"session_id": sid, "uci": "e1e3"})
    check("illegal -> 400", r.status_code == 400)
    r = alice.post("/api/profile/practice/attempt", json={"session_id": sid, "uci": "f1b5"})
    check("repeating the original mistake: not passed, flagged, answer revealed",
          r.status_code == 200 and r.json()["passed"] is False and r.json()["repeated_mistake"] is True and r.json()["best_san"] == "d4", r.text)
    check("a second grading is refused", alice.post("/api/profile/practice/attempt", json={"session_id": sid, "uci": "d2d4"}).status_code == 409)

    r = alice.post(f"/api/profile/mistakes/{THEME}/practice")
    sid2 = r.json()["practice_session_id"]
    r = alice.post("/api/profile/practice/attempt", json={"session_id": sid2, "uci": "d2d4"})
    check("finding the engine move passes", r.status_code == 200 and r.json()["passed"] is True and r.json()["played_san"] == "d4", r.text)
    s2 = alice.get(f"/api/sandbox/session/{sid2}").json()
    check("the result is on the session for a reload", s2["practice"]["attempted"] is True and s2["practice"]["result"]["passed"] is True)

    section("no snapshot")
    r = alice.post("/api/profile/mistakes/KING_SAFETY/practice")
    check("theme without a stored position -> available: false with the honest sentence",
          r.status_code == 200 and r.json()["available"] is False and "missing the position snapshot" in r.json()["reason"], r.text)
    check("...and the profile says so up front", next(f for f in alice.get("/api/profile").json()["findings"] if f["theme"] == "KING_SAFETY")["practice_available"] is False)

    section("events")
    names = [e["event"] for e in learning_events.events.recent(50)]
    started = [e for e in learning_events.events.recent(50) if e["event"] == "profile_practice_started"]
    attempted = [e for e in learning_events.events.recent(50) if e["event"] == "profile_practice_attempted"]
    check("three starts (one unavailable) and two attempts recorded", len(started) == 3 and len(attempted) == 2, names)
    check("start carries theme, game and finding, never the position",
          all(e.get("theme") and "fen" not in json.dumps(e) for e in started) and any(e.get("finding_id") for e in started), started[:1])
    check("attempts carry outcomes", sorted(e["outcome"] for e in attempted) == ["passed", "repeated"], attempted)

section("mallory")
clear_limits()
with TestClient(app.app) as mallory:
    mallory.post("/api/auth/signup", json={"username": "mallory", "password": "mallory-password-1", "email": "m@example.com"})
    check("mallory has nothing to practise", mallory.post(f"/api/profile/mistakes/{THEME}/practice").json()["available"] is False)
    check("mallory cannot grade alice's session", mallory.post("/api/profile/practice/attempt", json={"session_id": sid2, "uci": "d2d4"}).status_code == 404)
    check("mallory cannot read alice's session", mallory.get(f"/api/sandbox/session/{sid2}").status_code in (403, 404))

print(f"\n{PASSED} passed, {FAILED} failed")
db.drop_schema()
try:
    app.stockfish_service.close()
except Exception:
    pass
raise SystemExit(1 if FAILED else 0)

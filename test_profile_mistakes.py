"""
Evidence labels, safe deletion and GET /api/profile/mistakes.

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_pm_$$ \\
        /tmp/chessapp/bin/python test_profile_mistakes.py

Needs DATABASE_URL and a disposable schema (dropped on the way out). No
engine: findings are written directly, as the worker would.

What is proved: every evidence row and first/most-recent reference carries a
label naming source, players, speed, result, date and library id; no FEN or
PGN is in the profile or mistakes payloads; deletion is account-gated and
owner-scoped, removes exactly one game with its findings, and the profile
and mistakes stop counting it; sources stay apart; a re-import of a deleted
game is a fresh row; the mistakes list invents nothing.
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


def seed(owner, n, source, theme="TACTICAL_OVERLOOK", tc="300", played_on="2026.09.10", colour="white"):
    """n analysed games from `source`, each with one finding of `theme`."""
    ids = []
    for i in range(n):
        gid = ps.add_game(owner, PGN.replace("a6", f"a6 {{{source}{i}}}"), {"white": "alice" if colour == "white" else "magnus",
                          "black": "magnus" if colour == "white" else "alice", "result": "1-0", "date": played_on},
                          colour, 6, source=source, external_id=f"{source}-{i}", time_control=tc)
        ps.record_findings(gid, owner, [{
            "ply": 5, "theme": theme, "severity": "major", "cpl": 100 + i, "fen_before": FEN,
            "move_san": "Bb5", "best_san": "d4", "phase": "opening",
        }])
        ids.append(gid)
    return ids


section("labels")
check("chess.com label", ps.game_label({"id": 69, "source": "chesscom", "white": "alice", "black": "magnus", "player_color": "white",
                                        "time_control": "300", "result": "1-0", "played_on": "2026.09.10", "created_at": 0})
      == "Chess.com · you as White vs magnus · Blitz 5+0 · 1-0 · Sep 10, 2026 · game #69",
      ps.game_label({"id": 69, "source": "chesscom", "white": "alice", "black": "magnus", "player_color": "white", "time_control": "300", "result": "1-0", "played_on": "2026.09.10"}))
check("lichess label, black, increment", ps.game_label({"id": 12, "source": "lichess", "white": "x", "black": "me", "player_color": "black",
                                                        "time_control": "600+5", "result": "0-1", "played_on": "2026-09-13"})
      == "Lichess · x vs you as Black · Rapid 10+5 · 0-1 · Sep 13, 2026 · game #12")
check("manual label, placeholders dropped, import date used", ps.game_label({"id": 3, "source": "manual", "white": "W", "black": "B",
                                                                             "player_color": "white", "result": "*", "played_on": "????.??.??", "created_at": 1_789_000_000})
      .startswith("Manual PGN · you as White vs B · "))
check("time control classes", [ps._time_control_label(t) for t in ("60", "180", "600", "1800+30", "junk")]
      == ["Bullet 1+0", "Blitz 3+0", "Rapid 10+0", "Classical 30+30", "junk"])
check("no game -> no label", ps.game_label(None) is None and ps.game_ref(None) is None)

section("guest")
with TestClient(app.app) as guest:
    check("guest mistakes -> 401", guest.get("/api/profile/mistakes").status_code == 401)
    check("guest delete -> 401", guest.delete("/api/profile/games/1").status_code == 401)

section("alice: profile with labelled evidence")
clear_limits()
with TestClient(app.app) as alice:
    alice.post("/api/auth/signup", json={"username": "alice", "password": "alice-password-1", "email": "alice@example.com"})
    with db.connection() as conn:
        alice_id = conn.execute("SELECT id FROM users WHERE username = 'alice'").fetchone()[0]
    owner = f"user:{alice_id}"
    cc = seed(owner, 6, "chesscom")
    li = seed(owner, 3, "lichess", tc="600+5", played_on="2026.09.13", colour="black")
    man = seed(owner, 2, "manual", theme="KING_SAFETY")
    all_ids = cc + li + man

    prof = alice.get("/api/profile").json()
    check("profile ready from 11 analysed games", prof["ready"] and prof["analysed_games"] == 11, prof.get("analysed_games"))
    text = json.dumps(prof)
    check("profile carries no FEN or PGN", FEN not in text and "fen_before" not in text and "[Event" not in text)
    tactics = next(f for f in prof["findings"] if f["theme"] == "TACTICAL_OVERLOOK")
    check("first_seen / last_seen name exact games",
          tactics["first_seen"]["game_id"] == cc[0] and tactics["first_seen"]["game_label"].startswith("Chess.com · you as White vs magnus · Blitz 5+0")
          and tactics["last_seen"]["game_id"] == li[-1] and tactics["last_seen"]["game_label"].startswith("Lichess · magnus vs you as Black · Rapid 10+5"), tactics["first_seen"])
    check("every representative row has a label, a finding id and a review flag",
          all(r["game_label"] and r["finding_id"] and r["can_review_game"] for r in tactics["representative"]), tactics["representative"])
    check("practice available when FEN stored", tactics["practice_available"] is True)
    check("KING_SAFETY withheld: only 2 games", all(f["theme"] != "KING_SAFETY" for f in prof["findings"]))

    section("mistakes endpoint")
    r = alice.get("/api/profile/mistakes")
    check("200 ok", r.status_code == 200 and r.json()["ok"] is True, r.text[:200])
    m = r.json()
    check("analysed_games and one real theme", m["analysed_games"] == 11 and [t["id"] for t in m["themes"]] == ["TACTICAL_OVERLOOK"], m["themes"])
    t = m["themes"][0]
    check("theme shape", all(k in t for k in ("title", "short_label", "confidence", "stability", "description", "evidence_count",
                                              "game_count", "first_seen", "most_recent", "examples", "practice_available", "practice_endpoint")), sorted(t))
    check("counts", t["evidence_count"] == 9 and t["game_count"] == 9 and t["confidence"] == "high")
    check("examples carry move label, engine move, phase, severity, game label",
          t["examples"][0]["move_label"] == "3. Bb5" and t["examples"][0]["engine_san"] == "d4" and t["examples"][0]["phase"] == "opening"
          and t["examples"][0]["game_label"] and t["examples"][0]["can_review_game"], t["examples"][0])
    check("practice endpoint named", t["practice_endpoint"] == "/api/profile/mistakes/TACTICAL_OVERLOOK/practice")
    check("mistakes carry no FEN or PGN", FEN not in r.text and "fen_before" not in r.text and "[Event" not in r.text)
    check("no invented theme from the 2-game KING_SAFETY evidence", all(x["id"] != "KING_SAFETY" for x in m["themes"]))

    section("deletion")
    victim = cc[0]
    r = alice.delete(f"/api/profile/games/{victim}")
    check("owner deletes own game -> 200", r.status_code == 200, r.text)
    check("gone from the library", all(g["id"] != victim for g in alice.get("/api/profile/games").json()["games"]))
    with db.connection() as conn:
        left = conn.execute("SELECT count(*) FROM game_findings WHERE game_id = %s", (victim,)).fetchone()[0]
        n_games = conn.execute("SELECT count(*) FROM imported_games WHERE owner = %s", (owner,)).fetchone()[0]
    check("its findings went with it, nothing else did", left == 0 and n_games == 10)
    m2 = alice.get("/api/profile/mistakes").json()
    t2 = m2["themes"][0]
    check("aggregation updated: 10 analysed, 8 evidence, 8 games, first_seen moved on",
          m2["analysed_games"] == 10 and t2["evidence_count"] == 8 and t2["game_count"] == 8 and t2["first_seen"]["game_id"] == cc[1], t2["first_seen"])
    ev = alice.get("/api/profile/evidence").json()
    by_src = {s["source"]: s for s in ev["sources"]}
    check("source sections stay correct after deleting a chess.com game",
          by_src["chesscom"]["games"] == 5 and by_src["lichess"]["games"] == 3 and by_src["manual"]["games"] == 2, by_src)
    check("deleting again -> 404", alice.delete(f"/api/profile/games/{victim}").status_code == 404)
    # The same game, imported again, is a new row: deletion was not a hide.
    again = ps.add_game(owner, PGN.replace("a6", "a6 {chesscom0}"), {"white": "alice", "black": "magnus", "result": "1-0"}, "white", 6,
                        source="chesscom", external_id="chesscom-0", time_control="300")
    check("a deleted game can be imported again as a fresh row", again is not None and again != victim)

section("mallory cannot touch alice's games")
clear_limits()
with TestClient(app.app) as mallory:
    mallory.post("/api/auth/signup", json={"username": "mallory", "password": "mallory-password-1", "email": "m@example.com"})
    check("delete another user's game -> 404", mallory.delete(f"/api/profile/games/{cc[1]}").status_code == 404)
    with db.connection() as conn:
        still = conn.execute("SELECT count(*) FROM imported_games WHERE id = %s", (cc[1],)).fetchone()[0]
    check("...and it is still there", still == 1)
    check("mallory's mistakes are empty, not alice's", mallory.get("/api/profile/mistakes").json()["themes"] == [])

print(f"\n{PASSED} passed, {FAILED} failed")
db.drop_schema()
try:
    app.stockfish_service.close()
except Exception:
    pass
raise SystemExit(1 if FAILED else 0)

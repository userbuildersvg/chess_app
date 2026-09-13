"""
An imported game opens in the existing Review, and what it teaches lands on
the improvement profile, tagged with where the game came from.

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_rev_$$ \\
        /tmp/chessapp/bin/python test_review_import.py

Needs Stockfish, DATABASE_URL and a disposable schema. The diagnosis model
is faked at the service boundary (as test_learning_loop_api.py does), the
providers by an httpx MockTransport.

What is proved: "Review this game" on an imported game is the ONE Post-Mortem
pipeline (same store, same routes, same scan); the review keeps the source,
the seats, the result, date, time control and opening; the library row
records that a review happened; a correction card made in that review files
source-tagged evidence against the account; the profile aggregation counts
only what exists and claims no pattern from one game.
"""

import os

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ["ACCOUNTS_ENABLED"] = "true"
os.environ.setdefault("SESSION_COOKIE_SECRET", "test-signing-key-not-a-real-secret")

if os.environ.get("DATABASE_SCHEMA", "public") == "public":
    raise SystemExit("Refusing to run against the public schema - set DATABASE_SCHEMA to a disposable name.")

import time

import chess
import httpx
from fastapi.testclient import TestClient

import app
import db
import diagnosis_service
import external_games as eg
import learning_events
import learning_loop
import learning_loop_api
import postmortem_api
import profile_api
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


def clear_limits():
    rate_limit_module.limit_login.limiter.reset()
    rate_limit_module.limit_signup.limiter.reset()
    for dep in (profile_api.limit_import, profile_api.limit_read, profile_api.limit_external):
        dep.limiter.reset()


db.migrate()


async def fake_decide(fen, color, *, profile=None, last_move=None, use_learning=True, learning=None):
    board = chess.Board(fen)
    return sorted(m.uci() for m in board.legal_moves)[0], "Fake reason.", "gemini", {}


postmortem_api.configure(fake_decide)


class FakeDiagnosis:
    def __init__(self):
        self.script = []
        self.calls = []

    def available(self):
        return True

    async def diagnose(self, evidence, intent, prior=None):
        self.calls.append({"evidence": evidence, "intent": intent, "prior": prior})
        if not self.script:
            return False, "no script left"
        nxt = self.script.pop(0)
        try:
            return True, diagnosis_service.validate(nxt, evidence)
        except diagnosis_service.DiagnosisError as exc:
            return False, f"rejected: {exc}"


fake = FakeDiagnosis()
learning_loop_api.diagnosis_service = fake


def a_diagnosis(theme="TACTICAL_OVERLOOK"):
    return {"theme": theme, "diagnosis": "You went for the plan and missed what was on the board.",
            "missed_factor": "The square was not covered.", "confidence": 0.66, "uncertainty": ""}


# A game Alice (Black) loses to a scholar's mate on chess.com, and one she
# plays as White on lichess. Both short, both real blunders.
SCHOLAR = "1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0"
LICHESS_GAME = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nd4 4. Nxe5 Qg5 5. Nxf7 Qxg2 6. Rf1 Qxe4+ 7. Be2 Nf3# 0-1"


def handler(request):
    url = str(request.url)
    if "api.chess.com" in url:
        if url.endswith("/archives"):
            return httpx.Response(200, json={"archives": ["https://api.chess.com/pub/player/alice/games/2026/09"]})
        pgn = ('[Event "Live Chess"]\n[Site "Chess.com"]\n[Date "2026.09.03"]\n[White "bob"]\n[Black "alice"]\n'
               '[Result "1-0"]\n[WhiteElo "1210"]\n[BlackElo "1190"]\n[TimeControl "180"]\n[UTCDate "2026.09.03"]\n[UTCTime "12:00:00"]\n'
               '[ECOUrl "https://www.chess.com/openings/Bishops-Opening-2...Nc6"]\n'
               '[Link "https://www.chess.com/game/live/777"]\n\n' + SCHOLAR + '\n')
        return httpx.Response(200, json={"games": [{"url": "https://www.chess.com/game/live/777", "pgn": pgn,
                                                    "time_control": "180", "rated": True, "rules": "chess",
                                                    "end_time": 1_757_000_000}]})
    if "lichess.org" in url:
        pgn = ('[Event "Rated Blitz game"]\n[Site "https://lichess.org/li000777"]\n[Date "2026.09.05"]\n'
               '[White "alice"]\n[Black "carol"]\n[Result "0-1"]\n[UTCDate "2026.09.05"]\n[UTCTime "09:00:00"]\n'
               '[Variant "Standard"]\n[TimeControl "300+0"]\n[ECO "C50"]\n[Opening "Italian Game"]\n\n' + LICHESS_GAME + '\n')
        return httpx.Response(200, text=pgn, headers={"content-type": "application/x-chess-pgn"})
    return httpx.Response(500, text="unrouted")


eg.client_factory = lambda: eg.make_client(transport=httpx.MockTransport(handler))
eg.clear_cache()


def wait_scan(client, game_id, seconds=90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        a = client.get(f"/api/postmortem/game/{game_id}/analysis").json()
        if a.get("scan", {}).get("status") in ("done", "failed", "complete", "completed"):
            return a
        time.sleep(0.5)
    return client.get(f"/api/postmortem/game/{game_id}/analysis").json()


clear_limits()
with TestClient(app.app) as c:
    learning_loop.corrections.clear()
    learning_events.events.clear()
    r = c.post("/api/auth/signup", json={"username": "alice", "password": "alice-password-1"})
    check("account exists", r.status_code == 200, r.text)
    r = c.post("/api/profile/external/import", json={"source": "chesscom", "username": "alice"})
    check("chess.com game imported", r.status_code == 200 and r.json()["imported_count"] == 1, r.text)
    cc_id = r.json()["imported"][0]["id"]
    r = c.post("/api/profile/external/import", json={"source": "lichess", "username": "alice"})
    check("lichess game imported", r.status_code == 200 and r.json()["imported_count"] == 1, r.text)
    li_id = r.json()["imported"][0]["id"]

    section("Review this game - the existing Post-Mortem pipeline")
    r = c.post(f"/api/profile/games/{cc_id}/review")
    check("review opens with 200", r.status_code == 200, r.text[:300])
    state = r.json()
    review_id = state["game_id"]
    check("the answer is the Post-Mortem review state", "headers" in state and "total_plies" in state and "scan" in state, list(state)[:12])
    check("origin says imported, with its source", state["origin"] == "imported" and state["import_source"] == "chesscom", state.get("origin"))
    check("the source username travels with it", state["source_username"] == "alice")
    check("the library row id travels with it", state["imported_game_id"] == cc_id)
    check("player colour is alice's seat (black)", state["player_color"] == "black", state.get("player_color"))
    h = state["headers"]
    check("white/black names preserved", h.get("White") == "bob" and h.get("Black") == "alice", h)
    check("result preserved", state["result"] == "1-0" and h.get("Result") == "1-0", state.get("result"))
    check("date preserved", h.get("Date") == "2026.09.03", h)
    check("time control preserved", h.get("TimeControl") == "180", h)
    check("ratings preserved", h.get("WhiteElo") == "1210" and h.get("BlackElo") == "1190", h)
    check("the scan is already running or done", state.get("scan", {}).get("status") in ("running", "done", "complete", "completed"), state.get("scan"))
    r = c.get(f"/api/postmortem/game/{review_id}")
    check("the same /api/postmortem route serves it - one review system", r.status_code == 200 and r.json()["game_id"] == review_id)
    r = c.post(f"/api/postmortem/game/{review_id}/forward")
    check("...and navigates like any review", r.status_code == 200, r.text[:200])
    row = c.get(f"/api/profile/games/{cc_id}").json()["game"]
    check("the library row records the review", row["reviewed_at"] is not None, row)
    check("external_import_game_selected_for_review was emitted",
          learning_events.events.counts().get("external_import_game_selected_for_review", 0) >= 1,
          learning_events.events.counts())

    r = c.post(f"/api/profile/games/{li_id}/review")
    check("the lichess game opens too, tagged lichess", r.status_code == 200 and r.json()["import_source"] == "lichess" and r.json()["player_color"] == "white", r.text[:200])
    li_review = r.json()["game_id"]
    check("lichess opening header preserved", r.json()["headers"].get("Opening") == "Italian Game", r.json()["headers"])

    section("a correction made in the review is evidence on the profile")
    analysis = wait_scan(c, review_id)
    # Black's last move (3...Nf6, ply 6) allowed mate; diagnose it. The
    # review was stepped forward once above, so five more steps land there.
    for _ in range(5):
        st = c.post(f"/api/postmortem/game/{review_id}/forward").json()
    node_id = st["current_id"]
    check("standing on ply 6", st["ply"] == 6, st.get("ply"))
    fake.script = [a_diagnosis("KING_SAFETY")]
    r = c.post("/api/learning-loop/diagnose", json={"game_id": review_id, "node_id": node_id, "intent": "I wanted to develop the knight."})
    check("diagnosis answers 200", r.status_code == 200, r.text[:300])
    card = r.json()["correction"]
    ev = (card.get("evidence") or [{}])[-1]
    check("the card's evidence names the source platform and game", ev.get("import_source") == "chesscom" and ev.get("imported_game_id") == cc_id, ev)
    check("...and the move number and theme", ev.get("ply") == 6 and card["theme"] == "KING_SAFETY", (ev.get("ply"), card["theme"]))
    with db.connection() as conn:
        rows = conn.execute("SELECT origin, theme, ply, confidence FROM game_findings WHERE game_id = %s AND origin = 'correction'", (cc_id,)).fetchall()
    check("one correction row filed against the imported game", len(rows) == 1 and rows[0][1] == "KING_SAFETY" and rows[0][2] == 6, rows)
    check("...carrying the diagnosis confidence", rows[0][3] is not None and abs(rows[0][3] - 0.66) < 1e-6, rows)
    fake.script = [a_diagnosis("KING_SAFETY")]
    c.post("/api/learning-loop/diagnose", json={"game_id": review_id, "node_id": node_id, "intent": "again"})
    with db.connection() as conn:
        n = conn.execute("SELECT count(*) FROM game_findings WHERE game_id = %s AND origin = 'correction'", (cc_id,)).fetchone()[0]
    check("diagnosing the same decision twice is one piece of evidence", n == 1, n)

    section("the source-tagged evidence summary")
    ev = c.get("/api/profile/evidence").json()
    srcs = {s["source"]: s for s in ev["sources"]}
    check("chess.com: 1 game, reviewed", srcs["chesscom"]["games"] == 1 and srcs["chesscom"]["reviewed"] == 1, srcs)
    check("lichess: 1 game, reviewed, no evidence yet", srcs["lichess"]["games"] == 1 and srcs["lichess"]["reviewed"] == 1, srcs)
    check("chess.com correction evidence counted", srcs["chesscom"]["correction_evidence"] == 1, srcs)
    themes = {t["theme"]: t for t in ev["themes"]}
    check("KING_SAFETY appears with a count and a label", "KING_SAFETY" in themes and themes["KING_SAFETY"]["count"] >= 1 and themes["KING_SAFETY"]["label"], themes)
    check("...tagged by source", themes["KING_SAFETY"]["by_source"].get("chesscom", 0) >= 1, themes)
    check("...and is NOT called recurring from one game", themes["KING_SAFETY"]["recurring"] is False and themes["KING_SAFETY"]["games_count"] == 1, themes)
    check("no theme is invented", all(t["count"] > 0 for t in ev["themes"]))

    section("the recurrence claims stay deterministic")
    profile = c.get("/api/profile").json()
    check("the profile claims nothing below the threshold", profile["ready"] is False and profile["findings"] == [], profile)
    with db.connection() as conn:
        detector_rows = conn.execute("SELECT count(*) FROM game_findings WHERE owner LIKE 'user:%%' AND origin = 'detector'").fetchone()[0]
    with db.connection() as conn:
        conn.execute("UPDATE imported_games SET state = 'done'")
    # a re-scan by the worker must not erase correction evidence
    profile_service.record_findings(cc_id, "user:1", [])
    with db.connection() as conn:
        n = conn.execute("SELECT count(*) FROM game_findings WHERE game_id = %s AND origin = 'correction'", (cc_id,)).fetchone()[0]
    check("a worker re-scan keeps correction evidence", n == 1, n)

    section("a dropped file files nothing")
    r = c.post("/api/postmortem/import", json={"pgn": '[White "x"]\n[Black "y"]\n\n' + SCHOLAR, "source_name": "drop.pgn"})
    dropped = r.json()["game_id"]
    check("a dropped-file review has no import source", r.json()["import_source"] is None and r.json()["imported_game_id"] is None)
    for _ in range(6):
        st = c.post(f"/api/postmortem/game/{dropped}/forward").json()
    nid = st["current_id"]
    fake.script = [a_diagnosis("KING_SAFETY")]
    before = None
    with db.connection() as conn:
        before = conn.execute("SELECT count(*) FROM game_findings WHERE origin = 'correction'").fetchone()[0]
    c.post("/api/learning-loop/diagnose", json={"game_id": dropped, "node_id": nid, "intent": "x"})
    with db.connection() as conn:
        after = conn.execute("SELECT count(*) FROM game_findings WHERE origin = 'correction'").fetchone()[0]
    check("no evidence row for a game that is not in the library", before == after, (before, after))

print(f"\n{PASSED} passed, {FAILED} failed")
app.stockfish_service.close()
raise SystemExit(1 if FAILED else 0)

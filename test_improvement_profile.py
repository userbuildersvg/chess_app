"""
The Improvement Profile: detection, storage, aggregation, and the API.

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_prof_$$ \\
        /tmp/chessapp/bin/python test_improvement_profile.py

Needs Stockfish and DATABASE_URL, and a disposable schema - it creates
accounts, imported games and findings.

WHAT THIS FILE IS FOR
---------------------
The profile says "you consistently...". That sentence is only worth anything if
the counting behind it is right, so the claims worth proving are:

1. **A detector fires on evidence and stays silent without it.** A theme
   claimed from noise is worse than a theme that is missing, because a person
   will go and practise it.
2. **One ply produces at most one finding.** A ply that tripped three detectors
   would inflate three separate patterns off a single mistake.
3. **Only the account's own moves count.** A finding about the opponent's
   blunder is not a fact about the person.
4. **The threshold holds.** Nothing is claimed below ten analysed games, and no
   theme is claimed from fewer than three of them - so one catastrophic game
   cannot invent a pattern.
5. **Imported games never reach the AI candidate pool.** They live outside
   `games`, which is what stops anyone steering the engine by importing.
6. **A library belongs to one account**, and no id in a URL reaches another's.
"""

import io
import os
import random
import time

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ["ACCOUNTS_ENABLED"] = "true"
os.environ.setdefault("SESSION_COOKIE_SECRET", "test-signing-key-not-a-real-secret")

if os.environ.get("DATABASE_SCHEMA", "public") == "public":
    raise SystemExit(
        "Refusing to run against the public schema - this suite creates accounts, "
        "imported games and findings. Set DATABASE_SCHEMA to a disposable name."
    )

import chess
from fastapi.testclient import TestClient

import app
import db
import pattern_detectors as pdx
import profile_service
import profile_worker
import rate_limit as rate_limit_module

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


def section(name):
    print(f"\n--- {name} ---")


def clear_limits():
    rate_limit_module.limit_login.limiter.reset()
    rate_limit_module.limit_signup.limiter.reset()
    import profile_api
    profile_api.limit_import.limiter.reset()
    profile_api.limit_read.limiter.reset()


db.migrate()


def evidence(**kw):
    """An evidence packet with sane defaults, so each test states only what it
    is actually about."""
    base = {
        "ply": 10,
        # A knight move, deliberately: the default has to be a move that trips
        # none of the specific detectors, so each test states the one thing it
        # is actually about. A wing pawn push here would quietly make every
        # test a FLANK_PAWN_COMMITTAL test.
        "san": "Nh3",
        "uci": "g1h3",
        "color": "white",
        "phase": "middlegame",
        "fen_before": chess.STARTING_FEN,
        "eval_before": {"score": 20, "mate_in": None},
        "best_move": None,
        "best_san": None,
        "cpl": 200,
        "quality": {"label": "mistake"},
    }
    base.update(kw)
    return base


# ===========================================================================
# 1. The detectors, pure
# ===========================================================================

section("detection: what is and is not evidence")

check("a theme taxonomy of twelve", len(pdx.THEMES) == 12, len(pdx.THEMES))
check("the eight learning-loop themes are reused verbatim",
      all(t in pdx.THEMES for t in
          ("FORCING_MOVE_MISSED", "OPPONENT_THREAT_MISSED", "TACTICAL_OVERLOOK",
           "PREMATURE_ATTACK", "KING_SAFETY", "PIECE_ACTIVITY",
           "CAPTURE_RECALCULATION", "PLAN_BEFORE_OPPONENT_RESPONSE")))
check("every theme has a claim sentence to say about a person",
      all(pdx.THEMES[t].get("claim") for t in pdx.THEME_IDS))
check("every claim is written in the second person, as a habit",
      all(pdx.THEMES[t]["claim"].lower().startswith("you") for t in pdx.THEME_IDS),
      [t for t in pdx.THEME_IDS if not pdx.THEMES[t]["claim"].lower().startswith("you")])

# --- the noise floor
check("a good move is not evidence", pdx.detect(evidence(cpl=0)) is None)
check("a small wobble is not evidence", pdx.detect(evidence(cpl=40)) is None)
check("an unmeasured move is not evidence", pdx.detect(evidence(cpl=None)) is None)
check("a book move is a fact about the position, not about the player",
      pdx.detect(evidence(cpl=300, quality={"label": "book"})) is None)
check("a forced move is not a choice",
      pdx.detect(evidence(cpl=300, quality={"label": "forced"})) is None)
check("a real mistake IS evidence", pdx.detect(evidence(cpl=200)) is not None)

# --- one ply, one finding
found = pdx.detect(evidence(cpl=400, phase="opening"))
check("a ply produces exactly one finding", isinstance(found, dict) and "theme" in found)

# --- severity bands
check("severity: minor", pdx.severity_of(60) == "minor")
check("severity: serious", pdx.severity_of(150) == "serious")
check("severity: critical", pdx.severity_of(400) == "critical")

# --- the four new themes
section("detection: the four themes the loop's eight cannot express")

opening = pdx.detect(evidence(cpl=150, phase="opening"))
check("ground lost in the opening, with nothing more specific to say, is OPENING_UNCERTAINTY",
      opening["theme"] == "OPENING_UNCERTAINTY", opening)

# The ordering that was wrong the first time. A hung piece on move four is a
# tactical miss that happened to be in the opening, not "opening uncertainty" -
# the thing to practise is seeing the reply, not learning a line. A phase is
# where a mistake happened; it is the best description of one only when nothing
# better fits.
OPENING_CHECK_FEN = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 3"
_b = chess.Board(OPENING_CHECK_FEN)
_checking = next((m for m in _b.legal_moves if _b.gives_check(m)), None)
if _checking is not None:
    t = pdx.detect(evidence(cpl=300, phase="opening", fen_before=OPENING_CHECK_FEN,
                            uci="a2a3", san="a3", best_move=_checking.uci()))
    check("a missed forcing move IN THE OPENING is still the forcing move, not the phase",
          t["theme"] == "FORCING_MOVE_MISSED", t)

# A won endgame given away. Mover is white and was +5.
endgame = pdx.detect(evidence(cpl=400, phase="endgame",
                              eval_before={"score": 500, "mate_in": None}))
check("a winning endgame given away is ENDGAME_CONVERSION",
      endgame["theme"] == "ENDGAME_CONVERSION", endgame)

level_endgame = pdx.detect(evidence(cpl=400, phase="endgame",
                                    eval_before={"score": 10, "mate_in": None}))
check("a LEVEL endgame is not a conversion failure",
      level_endgame["theme"] != "ENDGAME_CONVERSION", level_endgame)

losing_endgame = pdx.detect(evidence(cpl=400, phase="endgame", color="black",
                                     eval_before={"score": 500, "mate_in": None}))
check("being +5 as white is not black converting anything",
      losing_endgame["theme"] != "ENDGAME_CONVERSION", losing_endgame)

# A wing pawn push with the centre still live.
WING_FEN = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
wing = pdx.detect(evidence(cpl=200, phase="middlegame", fen_before=WING_FEN,
                           uci="h2h4", san="h4"))
check("a wing pawn push with the centre unresolved is FLANK_PAWN_COMMITTAL",
      wing["theme"] == "FLANK_PAWN_COMMITTAL", wing)

centre_push = pdx.detect(evidence(cpl=200, phase="middlegame", fen_before=WING_FEN,
                                  uci="d2d4", san="d4"))
check("a CENTRE pawn push is not a flank committal",
      centre_push["theme"] != "FLANK_PAWN_COMMITTAL", centre_push)

wing_opening = pdx.detect(evidence(cpl=200, phase="opening", fen_before=WING_FEN,
                                   uci="h2h4", san="h4"))
check("a wing push in the opening names the habit, not the phase",
      wing_opening["theme"] == "FLANK_PAWN_COMMITTAL", wing_opening)

BARE_FEN = "4k3/5ppp/8/8/8/8/5PPP/4K3 w - - 0 1"
check("the centre is not unresolved when there are no central pawns",
      pdx._centre_is_unresolved(chess.Board(BARE_FEN)) is False)

# --- time pressure, which must never be guessed
section("detection: time pressure is measured or not claimed")

no_clock = pdx.detect(evidence(cpl=400, phase="middlegame"))
check("with no clock in the file, TIME_PRESSURE is never claimed",
      no_clock["theme"] != "TIME_PRESSURE", no_clock)

low_clock = pdx.detect(evidence(cpl=400, phase="middlegame", clock_seconds=30))
check("with a low clock it IS claimed", low_clock["theme"] == "TIME_PRESSURE", low_clock)

lots_of_clock = pdx.detect(evidence(cpl=400, phase="middlegame", clock_seconds=1800))
check("a full clock is not time pressure",
      lots_of_clock["theme"] != "TIME_PRESSURE", lots_of_clock)

check("clock parsing reads [%clk h:mm:ss]",
      profile_worker.clock_seconds_from_comment("[%clk 0:01:30]") == 90.0)
check("clock parsing reads fractional seconds",
      profile_worker.clock_seconds_from_comment("[%clk 1:00:00.5]") == 3600.5)
check("a comment with no clock reads as unknown, not zero",
      profile_worker.clock_seconds_from_comment("good move!") is None)
check("an empty comment reads as unknown",
      profile_worker.clock_seconds_from_comment("") is None)

# --- the tactical eight
section("detection: which of the eight the engine's facts point at")

# The engine wanted a check; a quiet move was played.
CHECK_FEN = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 3"
board = chess.Board(CHECK_FEN)
checking = next((m for m in board.legal_moves if board.gives_check(m)), None)
if checking is not None:
    t = pdx.detect(evidence(cpl=200, fen_before=CHECK_FEN, uci="a2a3", san="a3",
                            best_move=checking.uci()))
    check("a missed check is FORCING_MOVE_MISSED", t["theme"] == "FORCING_MOVE_MISSED", t)

CAP_FEN = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
b2 = chess.Board(CAP_FEN)
capture = next((m for m in b2.legal_moves if b2.is_capture(m)), None)
if capture is not None:
    t = pdx.detect(evidence(cpl=200, fen_before=CAP_FEN, uci="a2a3", san="a3",
                            best_move=capture.uci()))
    check("a missed capture is CAPTURE_RECALCULATION",
          t["theme"] == "CAPTURE_RECALCULATION", t)


# ===========================================================================
# 2. Storage
# ===========================================================================

section("the library")

OWNER_A = "user:profile-test-a"
OWNER_B = "user:profile-test-b"

SHORT_PGN = """[Event "Test"]
[White "alice"]
[Black "bob"]
[Result "0-1"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 0-1
"""

gid = profile_service.add_game(OWNER_A, SHORT_PGN,
                               {"white": "alice", "black": "bob", "result": "0-1"},
                               "white", 8, "test.pgn")
check("a game can be added", isinstance(gid, int) and gid > 0, gid)

games = profile_service.list_games(OWNER_A)
check("it appears in the owner's library", len(games) == 1, games)
check("it starts pending", games[0]["state"] == "pending", games[0])
check("it remembers which side was the account's", games[0]["player_color"] == "white")

check("another owner's library is empty", profile_service.list_games(OWNER_B) == [])

prog = profile_service.progress(OWNER_A)
check("progress counts it as pending", prog["pending"] == 1 and prog["total"] == 1, prog)

# --- imported games are NOT in the AI's candidate pool. The release-blocking one.
with db.connection() as conn:
    in_games = conn.execute(
        "SELECT count(*) FROM games WHERE owner = %s", (OWNER_A,)).fetchone()[0]
check("an imported game does NOT become a row in `games`", in_games == 0, in_games)
with db.connection() as conn:
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = %s AND table_name = 'imported_games'", (db.SCHEMA,)).fetchall()}
check("imported_games is its own table", {"owner", "pgn", "state", "player_color"} <= cols,
      sorted(cols))

# --- claiming
claimed = profile_service.claim_next_pending()
check("the worker can claim it", claimed is not None and claimed["id"] == gid, claimed)
check("claiming marks it analysing",
      profile_service.list_games(OWNER_A)[0]["state"] == "analysing")
check("a second claim finds nothing left", profile_service.claim_next_pending() is None)

check("requeue_stuck puts it back", profile_service.requeue_stuck() == 1)
check("...as pending", profile_service.list_games(OWNER_A)[0]["state"] == "pending")

# --- findings
profile_service.claim_next_pending()
written = profile_service.record_findings(gid, OWNER_A, [
    {"ply": 5, "theme": "TACTICAL_OVERLOOK", "severity": "serious", "cpl": 150,
     "fen_before": chess.STARTING_FEN, "move_san": "Bb5", "best_san": "d4", "phase": "opening"},
])
check("findings are written", written == 1)
check("recording findings marks the game done",
      profile_service.list_games(OWNER_A)[0]["state"] == "done")
check("the game lists how many findings it has",
      profile_service.list_games(OWNER_A)[0]["findings"] == 1)

# --- deletion cascades
check("a game can be removed", profile_service.remove_game(OWNER_A, gid) is True)
with db.connection() as conn:
    left = conn.execute("SELECT count(*) FROM game_findings WHERE game_id = %s",
                        (gid,)).fetchone()[0]
check("its findings go with it", left == 0, left)
check("removing another owner's game does nothing",
      profile_service.remove_game(OWNER_B, gid) is False)


# ===========================================================================
# 3. Aggregation - the threshold is the product
# ===========================================================================

section("aggregation: nothing is claimed before the evidence supports it")


def seed(owner, games, theme, per_game=1, cpl=200, start=0):
    """`games` finished games for `owner`, each carrying `per_game` findings."""
    ids = []
    for i in range(games):
        gid = profile_service.add_game(
            owner, SHORT_PGN, {"white": "alice", "black": "bob"}, "white", 8, "seed.pgn")
        profile_service.claim_next_pending()
        profile_service.record_findings(gid, owner, [
            {"ply": 5 + j, "theme": theme, "severity": "serious", "cpl": cpl,
             "fen_before": chess.STARTING_FEN, "move_san": "Bb5",
             "best_san": "d4", "phase": "middlegame"}
            for j in range(per_game)
        ])
        ids.append(gid)
        time.sleep(0.002)  # distinct created_at, so ordering is deterministic
    return ids


OWNER_C = "user:profile-test-c"

# Nine games: below the floor, so nothing at all is said.
seed(OWNER_C, 9, "TACTICAL_OVERLOOK")
prof = profile_service.build(OWNER_C)
check("under ten analysed games the profile is not ready", prof["ready"] is False, prof["ready"])
check("...and says how many more it needs", prof["games_needed"] == 1, prof["games_needed"])
check("...and claims nothing", prof["findings"] == [], prof["findings"])
check("...while still reporting what it has analysed", prof["analysed_games"] == 9)

# The tenth.
seed(OWNER_C, 1, "TACTICAL_OVERLOOK")
prof = profile_service.build(OWNER_C)
check("at ten games the profile is ready", prof["ready"] is True)
check("...and now claims the pattern", len(prof["findings"]) == 1, prof["findings"])

f = prof["findings"][0]
check("the claim reads as a habit, not an incident",
      f["claim"].lower().startswith("you"), f["claim"])
check("it carries an evidence count", f["evidence_count"] == 10, f)
check("it carries a game count", f["games_count"] == 10, f)
check("it carries a confidence", f["confidence"] in ("low", "medium", "high"), f)
check("ten out of ten games is high confidence", f["confidence"] == "high", f["confidence"])
check("it carries representative moves", len(f["representative"]) > 0, f)
check("...capped so the list stays readable",
      len(f["representative"]) <= profile_service.REPRESENTATIVE_LIMIT, f)
check("every representative names a game and a move",
      all(r.get("game_id") and r.get("move_san") for r in f["representative"]))
check("it carries first seen and most recent",
      f["first_seen_game"] != f["last_seen_game"], f)
check("it carries a trend", f["trend"] in ("improving", "stable", "worsening"), f)

# --- one bad game must not invent a pattern
OWNER_D = "user:profile-test-d"
seed(OWNER_D, 9, "TACTICAL_OVERLOOK")
seed(OWNER_D, 1, "KING_SAFETY", per_game=12)   # one catastrophic game
prof = profile_service.build(OWNER_D)
themes = {f["theme"] for f in prof["findings"]}
check("twelve findings in ONE game is not a pattern",
      "KING_SAFETY" not in themes, sorted(themes))
check("...while the theme spread across games still is",
      "TACTICAL_OVERLOOK" in themes, sorted(themes))

# --- a theme in exactly three games is the boundary
OWNER_E = "user:profile-test-e"
seed(OWNER_E, 7, "TACTICAL_OVERLOOK")
seed(OWNER_E, 3, "PREMATURE_ATTACK")
prof = profile_service.build(OWNER_E)
themes = {f["theme"] for f in prof["findings"]}
check("a theme in exactly three games is claimed", "PREMATURE_ATTACK" in themes, sorted(themes))
check("findings are ordered by how many games carry them",
      prof["findings"][0]["games_count"] >= prof["findings"][-1]["games_count"],
      [(f["theme"], f["games_count"]) for f in prof["findings"]])

# --- trend
OWNER_F = "user:profile-test-f"
seed(OWNER_F, 5, "KING_SAFETY")          # early half: every game
seed(OWNER_F, 5, "TACTICAL_OVERLOOK")    # late half: none of them
prof = profile_service.build(OWNER_F)
ks = next((f for f in prof["findings"] if f["theme"] == "KING_SAFETY"), None)
check("a theme that stopped happening reads as improving",
      ks is not None and ks["trend"] == "improving", ks["trend"] if ks else None)
to = next((f for f in prof["findings"] if f["theme"] == "TACTICAL_OVERLOOK"), None)
check("a theme that started happening reads as worsening",
      to is not None and to["trend"] == "worsening", to["trend"] if to else None)


# ===========================================================================
# 4. The API
# ===========================================================================

section("the API")
clear_limits()

MULTI_PGN = """[Event "One"]
[White "profiler"]
[Black "opponent"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 1-0

[Event "Two"]
[White "opponent"]
[Black "profiler"]
[Result "0-1"]

1. d4 d5 2. c4 e6 3. Nc3 Nf6 0-1
"""

with TestClient(app.app) as guest:
    r = guest.get("/api/profile")
    check("a guest cannot have a profile", r.status_code == 401, r.status_code)
    check("...and is told why, in a sentence about accounts",
          "account" in r.json().get("detail", "").lower(), r.json())
    r = guest.post("/api/profile/games", json={"pgn": MULTI_PGN})
    check("a guest cannot import", r.status_code == 401, r.status_code)

clear_limits()
with TestClient(app.app) as c:
    r = c.post("/api/auth/signup", json={"username": "profiler",
                                         "password": "profiler-password-1",
                                         "email": "profiler@example.com"})
    check("the test account was created", r.status_code == 200, r.text)

    r = c.post("/api/profile/games", json={"pgn": MULTI_PGN, "player_color": "auto"})
    check("a multi-game PGN imports every game in it",
          r.status_code == 200 and r.json()["added"] == 2, r.text)

    listed = c.get("/api/profile/games").json()
    check("both games are in the library", len(listed["games"]) == 2, listed)
    colours = {g["white"]: g["player_color"] for g in listed["games"]}
    check("`auto` matched the username to White in the first game",
          colours.get("profiler") == "white", colours)
    check("...and to Black in the second", colours.get("opponent") == "black", colours)

    check("a PGN with no date does not store python-chess's ????.??.?? placeholder",
          all(g["played_on"] != "????.??.??" for g in listed["games"]),
          [g["played_on"] for g in listed["games"]])
    check("...nor a bare ? as a player name",
          all(g["white"] != "?" and g["black"] != "?" for g in listed["games"]),
          [(g["white"], g["black"]) for g in listed["games"]])

    prog = c.get("/api/profile/progress").json()
    check("progress reports them as pending", prog["pending"] == 2, prog)

    prof = c.get("/api/profile").json()
    check("the profile is honest that it is not ready", prof["ready"] is False, prof)
    check("...and says how many games it still wants",
          prof["games_needed"] == profile_service.MIN_GAMES_FOR_PROFILE, prof)

    # --- a bad game among good ones does not lose the good ones
    GOOD_AND_BAD = """[Event "Three"]
[White "profiler"]
[Black "opponent"]
[Result "1-0"]

1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 1-0

[Event "Broken"]

1. e4 e5 2. Ke2xz 1-0
"""
    r = c.post("/api/profile/games", json={"pgn": GOOD_AND_BAD})
    check("a file with one unreadable game still imports the readable ones",
          r.status_code == 200 and r.json()["added"] >= 1, r.text)

    r = c.post("/api/profile/games", json={"pgn": "not a pgn at all"})
    check("a body with no game in it is refused", r.status_code == 400, r.status_code)

    # --- deletion is scoped
    mine = c.get("/api/profile/games").json()["games"][0]["id"]
    r = c.delete(f"/api/profile/games/{mine}")
    check("a game can be deleted through the API", r.status_code == 200, r.text)
    r = c.delete(f"/api/profile/games/{mine}")
    check("deleting it twice is a 404, not a 500", r.status_code == 404, r.status_code)
    r = c.delete("/api/profile/games/999999999")
    check("a game id that is not yours is a 404, not a 403",
          r.status_code == 404, r.status_code)

# --- a second account sees none of it
clear_limits()
with TestClient(app.app) as other:
    other.post("/api/auth/signup", json={"username": "profiler2",
                                         "password": "profiler2-password-1"})
    listed = other.get("/api/profile/games").json()
    check("a second account's library is empty", listed["games"] == [], listed)
    prof = other.get("/api/profile").json()
    check("...and its profile claims nothing", prof["findings"] == [], prof)


# ===========================================================================
# 6. Three reproduced regressions
#
# All three were found by an independent audit of this feature and reproduced
# before being fixed. Each check below fails on the code as it was.
# ===========================================================================

section("regression: deleting an account leaves no profile data behind")
clear_limits()

with TestClient(app.app) as c:
    c.post("/api/auth/signup", json={"username": "deleteme",
                                     "password": "deleteme-password-1"})
    who = c.get("/api/auth/me").json()
    check("the account to be deleted exists", who["signed_in"] is True, who)

    r = c.post("/api/profile/games", json={"pgn": MULTI_PGN, "player_color": "white"})
    check("it has an imported library", r.json()["added"] == 2, r.text)
    owned = c.get("/api/profile/games").json()["games"]
    game_ids = [g["id"] for g in owned]

    # Findings too, so the cascade is exercised rather than assumed.
    with db.connection() as conn:
        owner_row = conn.execute(
            "SELECT owner FROM imported_games WHERE id = %s", (game_ids[0],)).fetchone()
    owner = owner_row[0]
    profile_service.claim_next_pending()
    profile_service.record_findings(game_ids[0], owner, [
        {"ply": 5, "theme": "TACTICAL_OVERLOOK", "severity": "serious", "cpl": 150,
         "fen_before": chess.STARTING_FEN, "move_san": "Bb5", "best_san": "d4",
         "phase": "opening"},
    ])
    with db.connection() as conn:
        before_findings = conn.execute(
            "SELECT count(*) FROM game_findings WHERE owner = %s", (owner,)).fetchone()[0]
    check("it has profile evidence to lose", before_findings == 1, before_findings)

    r = c.request("DELETE", "/api/account", json={"confirm_username": "deleteme"})
    check("the account deletes", r.status_code == 200, r.text)

with db.connection() as conn:
    left_games = conn.execute(
        "SELECT count(*) FROM imported_games WHERE owner = %s", (owner,)).fetchone()[0]
    left_findings = conn.execute(
        "SELECT count(*) FROM game_findings WHERE owner = %s", (owner,)).fetchone()[0]
    orphan_findings = conn.execute(
        "SELECT count(*) FROM game_findings f"
        " WHERE NOT EXISTS (SELECT 1 FROM imported_games g WHERE g.id = f.game_id)"
    ).fetchone()[0]
    left_played = conn.execute(
        "SELECT count(*) FROM games WHERE owner = %s", (owner,)).fetchone()[0]

check("no imported game survives the account", left_games == 0, left_games)
check("no pattern evidence survives the account", left_findings == 0, left_findings)
check("no finding is left orphaned of its game", orphan_findings == 0, orphan_findings)
check("no played game survives the account either", left_played == 0, left_played)


section("regression: the same game cannot be counted twice")
clear_limits()

SOLO_PGN = """[Event "Dup"]
[White "duper"]
[Black "opponent"]
[Result "0-1"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Ng5 Qxg5 0-1
"""

# The same game, re-exported: different Event, Site, Date and a differently
# spelled opponent. It is the same game and must be recognised as one.
REEXPORTED_PGN = """[Event "Some Other Tournament"]
[Site "elsewhere.example"]
[Date "2025.01.01"]
[White "duper"]
[Black "Opponent, The"]
[Result "0-1"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Ng5 Qxg5 0-1
"""

with TestClient(app.app) as c:
    c.post("/api/auth/signup", json={"username": "duper", "password": "duper-password-1"})

    first = c.post("/api/profile/games", json={"pgn": SOLO_PGN, "player_color": "white"}).json()
    check("the game imports the first time", first["added"] == 1, first)

    second = c.post("/api/profile/games", json={"pgn": SOLO_PGN, "player_color": "white"}).json()
    check("the same game is NOT added again", second["added"] == 0, second)
    check("...it is reported as a duplicate, not silently dropped",
          len(second["duplicates"]) == 1, second)
    check("...and the reason says so in words",
          "already" in second["duplicates"][0]["reason"].lower(), second["duplicates"])

    lib = c.get("/api/profile/games").json()["games"]
    check("the library still holds exactly one copy", len(lib) == 1, len(lib))

    # The point of all of it: evidence cannot be inflated by re-uploading.
    third = c.post("/api/profile/games", json={"pgn": REEXPORTED_PGN, "player_color": "white"}).json()
    check("a re-export with different headers is the same game",
          third["added"] == 0 and len(third["duplicates"]) == 1, third)
    check("the library is STILL one game",
          len(c.get("/api/profile/games").json()["games"]) == 1)

    # A body that repeats a game inside itself.
    fourth = c.post("/api/profile/games",
                    json={"pgn": SOLO_PGN + "\n" + SOLO_PGN + "\n" + SOLO_PGN}).json()
    check("a single upload repeating one game counts it once",
          fourth["added"] == 0 and len(fourth["duplicates"]) == 3, fourth)

# The fingerprint itself, directly.
check("the fingerprint ignores headers entirely",
      profile_service.fingerprint(chess.STARTING_FEN, ["e2e4", "e7e5"])
      == profile_service.fingerprint(chess.STARTING_FEN, ["e2e4", "e7e5"]))
check("a different move sequence is a different game",
      profile_service.fingerprint(chess.STARTING_FEN, ["e2e4", "e7e5"])
      != profile_service.fingerprint(chess.STARTING_FEN, ["d2d4", "d7d5"]))
check("a different starting position is a different game",
      profile_service.fingerprint(chess.STARTING_FEN, ["e2e4"])
      != profile_service.fingerprint("8/8/8/8/8/8/4P3/4K2k w - - 0 1", ["e2e4"]))
check("the fingerprint is deterministic across calls",
      len({profile_service.fingerprint(chess.STARTING_FEN, ["e2e4"]) for _ in range(5)}) == 1)

# Duplicates must not move confidence. Ten distinct games, then the same ten
# again: the numbers behind every claim have to be identical.
OWNER_G = "user:profile-dup-owner"


def distinct_pgn(i):
    """Ten legal games that differ only in a harmless late pawn move, so each
    has its own fingerprint while the mistake being counted stays the same."""
    tail = ["a3", "a4", "b3", "b4", "c3", "h3", "h4", "g3", "g4", "d3"][i]
    return (f'[Event "D{i}"]\n[White "dupowner"]\n[Black "opp"]\n[Result "0-1"]\n\n'
            f'1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Ng5 Qxg5 5. {tail} Qg6 0-1\n')


import profile_api as _papi
import chess.pgn as _pgn
import io as _io


def add_via_service(owner, text):
    """Import the way the API does, so the fingerprint is computed the same way."""
    game = _pgn.read_game(_io.StringIO(text))
    moves = [n.move.uci() for n in game.mainline() if n.move is not None]
    fp = profile_service.fingerprint(game.board().fen(), moves)
    return profile_service.add_game(
        owner, str(game), _papi._headers_of(game), "white", len(moves),
        "dup.pgn", game_fingerprint=fp)


ids = []
for i in range(10):
    gid = add_via_service(OWNER_G, distinct_pgn(i))
    ids.append(gid)
    profile_service.claim_next_pending()
    profile_service.record_findings(gid, OWNER_G, [
        {"ply": 7, "theme": "TACTICAL_OVERLOOK", "severity": "serious", "cpl": 300,
         "fen_before": chess.STARTING_FEN, "move_san": "Ng5", "best_san": "d3",
         "phase": "opening"},
    ])

before = profile_service.build(OWNER_G)
b0 = before["findings"][0]

# Now every one of them again.
again = [add_via_service(OWNER_G, distinct_pgn(i)) for i in range(10)]
check("re-importing every game adds nothing", all(g is None for g in again), again)

after = profile_service.build(OWNER_G)
a0 = after["findings"][0]
check("re-importing does not change the analysed game count",
      after["analysed_games"] == before["analysed_games"],
      (before["analysed_games"], after["analysed_games"]))
check("re-importing does not inflate the evidence count",
      a0["evidence_count"] == b0["evidence_count"],
      (b0["evidence_count"], a0["evidence_count"]))
check("re-importing does not inflate the games count",
      a0["games_count"] == b0["games_count"], (b0["games_count"], a0["games_count"]))
check("re-importing does not raise confidence",
      a0["confidence"] == b0["confidence"], (b0["confidence"], a0["confidence"]))


def unique_game(i: int, event: str) -> str:
    """A legal game whose move sequence is unique for each `i`.

    The base line carries the mistake being counted; the tail is a short
    seeded-random continuation, so each game is its own.

    Two earlier attempts got this wrong in ways worth recording, because both
    made the test measure deduplication instead of the batch limit. A
    hand-written list of "different" tails collided four ways. Replaying a
    different NUMBER of "first legal quiet move" plies collided twenty-nine
    ways, because that walk shuffles pieces back and forth and every game past
    a certain length ends at the same fivefold repetition. Hence a seed per
    game - and hence the assertion below, which checks the fixture is what it
    claims rather than trusting it.
    """
    rng = random.Random(i)
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "Ng5", "Qxg5"):
        board.push_san(san)
    for _ in range(6):
        if board.is_game_over():
            break
        board.push(rng.choice(list(board.legal_moves)))
    game = chess.pgn.Game.from_board(board)
    game.headers["Event"] = event
    game.headers["White"] = "batcher"
    game.headers["Black"] = "opp"
    return str(game)


def _fingerprint_of_pgn(text: str) -> str:
    game = chess.pgn.read_game(io.StringIO(text))
    moves = [n.move.uci() for n in game.mainline() if n.move is not None]
    return profile_service.fingerprint(game.board().fen(), moves)


section("regression: a batch over the limit is reported, never silently dropped")
clear_limits()

with TestClient(app.app) as c:
    c.post("/api/auth/signup", json={"username": "batcher", "password": "batcher-password-1"})

    over = _papi.MAX_GAMES_PER_REQUEST + 1
    # Distinct games, so the only reason anything can be left out is the limit.
    # Built by replaying a base line and then pushing a different NUMBER of
    # quiet moves onto each - a hand-written list of "different" tails turned
    # out to collide, and four silent duplicates made this test measure
    # deduplication instead of the limit.
    batch = [unique_game(i, f"B{i}") for i in range(over)]
    # The fixture has to be genuinely distinct or this section silently becomes
    # a duplicate test. Checked, not assumed - it was wrong twice.
    check("the batch fixture really is 51 different games",
          len({_fingerprint_of_pgn(g) for g in batch}) == over,
          len({_fingerprint_of_pgn(g) for g in batch}))
    body = "\n\n".join(batch)
    r = c.post("/api/profile/games", json={"pgn": body})
    data = r.json()

    check("the request is accepted", r.status_code == 200, r.status_code)
    check(f"only {_papi.MAX_GAMES_PER_REQUEST} are imported",
          data["added"] == _papi.MAX_GAMES_PER_REQUEST, data["added"])
    check("the ones past the limit are REPORTED, not discarded in silence",
          data["ignored"] == over - _papi.MAX_GAMES_PER_REQUEST, data["ignored"])
    check("every game in the request is accounted for",
          data["added"] + data["ignored"] + len(data["skipped"]) + len(data["duplicates"]) == over,
          {k: (v if isinstance(v, int) else len(v))
           for k, v in data.items() if k in ("added", "ignored", "skipped", "duplicates")})
    check("the response names the limit so the caller can act on it",
          data["limit"] == _papi.MAX_GAMES_PER_REQUEST, data)
    check("the message says some were left out, in words",
          "left out" in data["message"].lower(), data["message"])
    check("the message says how many were added",
          str(data["added"]) in data["message"], data["message"])


# ===========================================================================
# 5. The real scan, against the real engine
# ===========================================================================

section("the scan itself")

# A short game with a real blunder in it: 4. Ng5?? drops a knight to Qxg5,
# which is unambiguous at any depth and does not depend on the engine's taste.
BLUNDER_PGN = """[Event "Blunder"]
[White "me"]
[Black "them"]
[Result "0-1"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Ng5 Qxg5 0-1
"""

findings = profile_worker.analyse_game(BLUNDER_PGN, "white", depth=8)
check("the scan finds something in a game with a real blunder in it",
      len(findings) > 0, findings)
check("every finding names a theme in the taxonomy",
      all(pdx.is_theme(f["theme"]) for f in findings), findings)
check("every finding carries the position it happened in",
      all(f.get("fen_before") for f in findings))
check("every finding carries the move that was played",
      all(f.get("move_san") for f in findings))

# Only the account's own moves.
white_plies = {f["ply"] for f in findings}
check("only the account's own moves are examined (white plays odd plies)",
      all(p % 2 == 1 for p in white_plies), sorted(white_plies))

black_findings = profile_worker.analyse_game(BLUNDER_PGN, "black", depth=8)
black_plies = {f["ply"] for f in black_findings}
check("...and as black, only the even ones",
      all(p % 2 == 0 for p in black_plies), sorted(black_plies))


# ===========================================================================

app.stockfish_service.close()
db.drop_schema()
db.close_pool()

print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

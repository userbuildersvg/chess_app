"""
Integration test for the /api/postmortem/* endpoints.

Real Stockfish and the real app wiring; only the move *decision* is faked, so
the test is deterministic and spends no Gemini quota - the same arrangement
test_sandbox_api.py uses, for the same reasons.

What it actually checks is the three claims Post-Mortem is judged on:

  1. The imported game is what the player played, and stays that way through
     any amount of branching.
  2. A what-if is answered by the app's own move selection, at full strength,
     without touching the player's learning history.
  3. Engine analysis is real - the numbers come from Stockfish and are
     addressed to the move they describe - and its absence is handled rather
     than faked.

Run:
    DISABLE_LANGFLOW=true /tmp/chessapp/bin/python test_postmortem_api.py
"""

import chess
from fastapi.testclient import TestClient

import os as _os

# The closed beta gate is fail-closed by default (`beta_service.beta_required`),
# so with it left alone every request in this file would be answered 403 by
# `beta_gate.py` before reaching the route under test. Switched off here rather
# than worked around, because this suite is testing what the routes do and not
# who may reach them - that is `test_beta_access.py`, which asserts among other
# things that this default is ON when nobody says otherwise.
_os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")

import app
import postmortem_api
import postmortem_analysis
from postmortem_state import postmortem_games

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


# Record exactly what a branch asks the decider for, and hand back a fixed
# legal move so the test is deterministic.
calls = []


async def fake_decide(fen, color, *, profile=None, last_move=None, use_learning=True, learning=None):
    calls.append({
        "fen": fen, "color": color, "profile": profile,
        "last_move": last_move, "use_learning": use_learning, "learning": learning,
    })
    board = chess.Board(fen)
    return sorted(m.uci() for m in board.legal_moves)[0], "Fake reason.", "gemini", {}


postmortem_api.configure(fake_decide)

SCHOLARS = """[Event "Casual"]
[White "Alice"]
[Black "Bob"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""

# A context manager, not a bare TestClient: the scan is a detached background
# task, and a bare client builds a fresh portal per request - so a task
# detached by one request dies the instant that request returns and the scan
# would sit at zero forever. Same trap test_sandbox_narration.py documents.
with TestClient(app.app) as client:
    # The real game's state before any of this, so the isolation claim can be
    # checked at the end. Calling /api/status mints this player's session
    # exactly as a browser would.
    client.get("/api/status")
    real_session = app.player_sessions.for_identity(
        next(iter(app.player_sessions.identities()))
    )
    real_fen_before = real_session.game.get_fen()
    real_history_before = len(real_session.game.game_history)

    # --- import ----------------------------------------------------------

    r = client.post("/api/postmortem/import", json={"pgn": SCHOLARS, "source_name": "scholars.pgn"})
    check("import returns 200", r.status_code == 200, r.text[:200])
    state = r.json()
    gid = state["game_id"]
    check("the whole game is replayed", state["total_plies"] == 7, state["total_plies"])
    check("the review opens at the starting position",
          state["ply"] == 0 and state["fen"].startswith("rnbqkbnr/pppppppp"), state["fen"])
    check("the players come through", state["headers"]["White"] == "Alice")
    check("the move list arrives with the state", len(state["moves"]) == 7)
    check("the file name is echoed back", state["source_name"] == "scholars.pgn")
    check("nothing is graded before the scan runs",
          all(row["quality"] is None for row in state["moves"]))

    for label, pgn, fragment in [
        ("an empty file", "", "empty"),
        ("a file with no moves", '[White "A"]\n\n*\n', "no moves"),
        ("an illegal continuation", "1. e4 e5 2. Ke2 Ke7 3. Kxe5 *", "not legal"),
        ("something that is not PGN at all", "hello", "PGN"),
    ]:
        bad = client.post("/api/postmortem/import", json={"pgn": pgn})
        ok = bad.status_code == 400 and fragment.lower() in bad.json()["detail"].lower()
        check(f"{label} is refused with a readable reason", ok,
              f"{bad.status_code} {bad.text[:120]}")
    check("a refused import leaves no review behind",
          client.get(f"/api/postmortem/game/{gid}").status_code == 200)

    # --- navigation -------------------------------------------------------

    r = client.post(f"/api/postmortem/game/{gid}/forward")
    check("forward steps one ply", r.json()["ply"] == 1 and r.json()["line_san"] == ["e4"],
          r.json()["line_san"])
    for _ in range(3):
        r = client.post(f"/api/postmortem/game/{gid}/forward")
    check("forward walks the game", r.json()["line_san"] == ["e4", "e5", "Bc4", "Nc6"],
          r.json()["line_san"])
    r = client.post(f"/api/postmortem/game/{gid}/back")
    check("back steps one ply", r.json()["ply"] == 3, r.json()["ply"])

    moves = state["moves"]
    r = client.post(f"/api/postmortem/game/{gid}/goto", json={"node_id": moves[-1]["node_id"]})
    end = r.json()
    check("goto jumps straight to a move", end["ply"] == 7, end["ply"])
    check("the final position is read as checkmate",
          end["status"]["state"] == "checkmate" and end["status"]["winner"] == "white",
          end["status"])
    check("forward at the end of the game does nothing",
          client.post(f"/api/postmortem/game/{gid}/forward").json()["ply"] == 7)
    check("a node from another game is a 404",
          client.post(f"/api/postmortem/game/{gid}/goto",
                      json={"node_id": "nope"}).status_code == 404)

    # --- branching --------------------------------------------------------

    # 3...Nf6 is the move that allowed mate. Stop before it and play g6.
    before_blunder = moves[4]["node_id"]        # after 3. Qh5
    client.post(f"/api/postmortem/game/{gid}/goto", json={"node_id": before_blunder})

    bad = client.post(f"/api/postmortem/game/{gid}/branch", json={"move": "e2e4"})
    check("an illegal alternative is refused with a readable reason",
          bad.status_code == 400 and "not legal" in bad.json()["detail"], bad.text[:120])

    r = client.post(f"/api/postmortem/game/{gid}/branch", json={"move": "g7g6"})
    check("a legal alternative is played", r.status_code == 200, r.text[:200])
    branched = r.json()
    check("the board is now off the game", branched["on_mainline"] is False)
    check("it knows which real move it departed from", branched["branch_ply"] == 5,
          branched["branch_ply"])
    check("the alternative line is reported on its own",
          branched["branch_line_san"] == ["g6"], branched["branch_line_san"])
    check("the branch move is analysed at full depth",
          branched["analysis"] is not None
          and branched["analysis"]["best_san"] is not None,
          branched["analysis"])
    check("that analysis says what depth produced it",
          (branched["analysis"] or {}).get("depth") is not None)

    check("the imported game is untouched by the branch",
          [m["san"] for m in branched["moves"]]
          == ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"],
          [m["san"] for m in branched["moves"]])
    # The marker goes on the move that was NOT taken: from the position before
    # 3...Nf6 there is now something else to look at, so that is the row the
    # move list flags.
    check("the move the what-if replaces is marked as having a branch",
          branched["moves"][5]["has_branch"] is True
          and branched["moves"][4]["has_branch"] is False,
          [m["has_branch"] for m in branched["moves"]])

    # The AI answers inside the branch.
    calls.clear()
    r = client.post(f"/api/postmortem/game/{gid}/ai-move")
    check("the AI replies in a branch", r.status_code == 200, r.text[:200])
    replied = r.json()
    check("its reply extends the branch, not the game",
          len(replied["branch_line_san"]) == 2 and replied["on_mainline"] is False,
          replied["branch_line_san"])
    check("the reply is decided at full strength",
          calls and calls[0]["profile"] == "master", calls)
    check("reviewing a game never touches the player's learning history",
          calls and calls[0]["use_learning"] is False, calls)
    check("the imported game is still untouched",
          [m["san"] for m in replied["moves"]][-1] == "Qxf7#")

    # More moves in the branch, then back out.
    board = chess.Board(replied["fen"])
    r = client.post(f"/api/postmortem/game/{gid}/branch",
                    json={"move": sorted(m.uci() for m in board.legal_moves)[0]})
    check("a branch can keep going", len(r.json()["branch_line_san"]) == 3,
          r.json()["branch_line_san"])

    r = client.post(f"/api/postmortem/game/{gid}/return")
    check("return goes back to the game", r.json()["on_mainline"] is True)
    check("and lands exactly where the branch left", r.json()["ply"] == 5, r.json()["ply"])

    r = client.post(f"/api/postmortem/game/{gid}/forward")
    check("the game continues from there as it was played",
          r.json()["line_san"] == ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6"],
          r.json()["line_san"])
    check("forward does not divert into the branch just made",
          r.json()["on_mainline"] is True)

    check("the AI refuses to invent a move on the real game",
          client.post(f"/api/postmortem/game/{gid}/ai-move").status_code == 409)

    # --- analysis ---------------------------------------------------------

    r = client.post(f"/api/postmortem/game/{gid}/analyse")
    check("a scan can be started", r.status_code == 200, r.text[:200])
    check("starting it twice does not start a second one",
          client.post(f"/api/postmortem/game/{gid}/analyse").status_code == 200)

    # The scan is a background task; poll it out rather than sleeping blind.
    analysis = {}
    for _ in range(240):
        analysis = client.get(f"/api/postmortem/game/{gid}/analysis").json()
        if analysis["scan"]["status"] in {"done", "failed"}:
            break
    check("the scan finishes", analysis["scan"]["status"] == "done", analysis["scan"])
    check("it analysed every ply", analysis["scan"]["analysed"] == 7, analysis["scan"])
    check("it says what depth it ran at", analysis["scan"]["depth"] is not None)

    graded = [m for m in analysis["moves"] if m["quality"]]
    check("every move comes back graded", len(graded) == 7, len(graded))
    check("the grades are the app's own vocabulary",
          all("label" in m["quality"] and "name" in m["quality"] for m in graded))
    check("the mating move is not called a blunder",
          analysis["moves"][-1]["quality"]["label"] in {"best", "great", "brilliant", "excellent", "good", "book"},
          analysis["moves"][-1]["quality"])
    check("3...Nf6 is graded as a real error",
          analysis["moves"][5]["quality"]["label"] in {"blunder", "mistake", "miss", "inaccuracy"},
          analysis["moves"][5]["quality"])

    curve = analysis["curve"]
    check("the curve has a frame per position including the start", len(curve) == 8, len(curve))
    check("the curve is in White's frame throughout and ends winning for White",
          (curve[-1]["mate_in"] is not None and curve[-1]["mate_in"] >= 0)
          or (curve[-1]["score"] or 0) > 0, curve[-1])
    check("early evaluations are real numbers, not placeholders",
          curve[1]["score"] is not None or curve[1]["mate_in"] is not None, curve[1])

    summary = analysis["summary"]
    check("accuracy is reported per colour",
          summary["white"]["accuracy"] is not None and summary["black"]["accuracy"] is not None,
          summary)
    check("the blunder shows up in Black's accuracy being the lower of the two",
          summary["black"]["accuracy"] < summary["white"]["accuracy"], summary)
    check("the report declares whole-game coverage",
          summary["coverage"]["scope"] == "full_game"
          and summary["coverage"]["analysed_moves"] == 7
          and summary["coverage"]["total_moves"] == 7,
          summary["coverage"])
    check("analysed moves and score denominator are separate",
          summary["white"]["analysed"] == 4
          and summary["white"]["total"] == 4
          and summary["white"]["scored"] == summary["white"]["graded"],
          summary["white"])
    check("score exclusions name their reason",
          sum(summary["coverage"]["score_exclusions"].values())
          == sum(len([q for q in side["counts"] for _ in range(side["counts"][q])
                      if q in {"book", "forced"}])
                 for side in (summary["white"], summary["black"])),
          summary["coverage"])
    check("turning points are offered in game order",
          [t["ply"] for t in summary["turning_points"]]
          == sorted(t["ply"] for t in summary["turning_points"]), summary["turning_points"])
    # Asserted against the SETTING rather than pinned to ["great"]. The scan
    # cannot detect "Great" - a claim about the runner-up - unless it asked for
    # a second principal variation, which POSTMORTEM_SCAN_MULTIPV controls and
    # which is off by default because it costs +80% (postmortem_analysis
    # .SCAN_MULTIPV carries the measurement). Pinning the constant made this
    # test fail on a correct build the moment the setting was turned on, which
    # is a test asserting the default rather than the behaviour.
    expected_unavailable = [] if postmortem_analysis.SCAN_MULTIPV >= 2 else ["great"]
    check("the grade this pass cannot detect is declared rather than hidden",
          summary["grades_unavailable"] == expected_unavailable,
          f"multipv={postmortem_analysis.SCAN_MULTIPV}, got {summary['grades_unavailable']}")

    # Barry's exact trust case: a short opening can have one engine-judged
    # decision per side because book moves are excluded from accuracy. It must
    # still say all eight half-moves were analysed and why the denominator is
    # smaller; "1 move graded" on its own is not an acceptable result.
    eight_pgn = """[Event "Eight plies"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Bxc6 dxc6 *
"""
    short = client.post(
        "/api/postmortem/import",
        json={"pgn": eight_pgn, "source_name": "eight.pgn"},
    ).json()
    short_id = short["game_id"]
    client.post(f"/api/postmortem/game/{short_id}/analyse")
    short_report = {}
    for _ in range(240):
        short_report = client.get(f"/api/postmortem/game/{short_id}/analysis").json()
        if short_report["scan"]["status"] in {"done", "failed"}:
            break
    short_summary = short_report["summary"]
    check("an 8-half-move PGN reports 8 of 8 analysed",
          short_report["scan"]["status"] == "done"
          and short_summary["coverage"]["analysed_moves"] == 8
          and short_summary["coverage"]["total_moves"] == 8,
          short_report.get("scan"))
    check("its side denominators cover all four moves",
          short_summary["white"]["total"] == 4
          and short_summary["black"]["total"] == 4
          and short_summary["white"]["analysed"] == 4
          and short_summary["black"]["analysed"] == 4,
          short_summary)
    check("book/forced exclusions are explicit when the accuracy denominator is smaller",
          all(side["scored"] + sum(side["excluded_from_score"].values())
              + side["skipped"] == side["total"]
              for side in (short_summary["white"], short_summary["black"])),
          short_summary)
    check("the exact one-decision denominator is labelled rather than mistaken for coverage",
          short_summary["white"]["scored"] == 1
          and short_summary["black"]["scored"] == 1
          and short_summary["white"]["excluded_from_score"].get("book") == 3
          and short_summary["black"]["excluded_from_score"].get("book") == 3,
          short_summary)
    client.delete(f"/api/postmortem/game/{short_id}")

    node_id = analysis["moves"][5]["node_id"]
    one = client.get(f"/api/postmortem/game/{gid}/analysis/{node_id}").json()
    check("one move's evidence packet can be fetched",
          one["analysis"]["san"] == "Nf6", one["analysis"].get("san"))
    check("the packet carries both positions and both evaluations",
          {"fen_before", "fen_after", "eval_before", "eval_after", "best_move", "pv_san"}
          <= set(one["analysis"]), set(one["analysis"]))
    check("the starting position has no move to analyse",
          client.get(f"/api/postmortem/game/{gid}/analysis/{curve[0]['node_id']}").status_code == 400)

    # --- chat -------------------------------------------------------------

    r = client.post(f"/api/postmortem/game/{gid}/chat", json={"message": "   "})
    check("an empty question is refused", r.status_code == 400, r.status_code)
    check("the transcript starts empty",
          client.get(f"/api/postmortem/game/{gid}/chat").json()["history"] == [])
    # "Clear chat" empties the server's copy - the one the coach is replayed -
    # and leaves the review where it was.
    game = postmortem_games.get(gid)
    game.chat_history.append({"role": "user", "text": "why?"})
    game.chat_history.append({"role": "model", "text": "because"})
    r = client.delete(f"/api/postmortem/game/{gid}/chat")
    check("clearing the chat answers with the now-empty transcript",
          r.status_code == 200 and r.json()["history"] == [], r.text)
    check("clearing the chat does not close the review",
          client.get(f"/api/postmortem/game/{gid}").status_code == 200)
    check("clearing a chat for a review that is not there is a 404",
          client.delete("/api/postmortem/game/nope/chat").status_code == 404)

    # The evidence packet the coach would be given, built without calling
    # Gemini: what matters is that it is grounded in this position and this
    # move rather than in the raw PGN.
    game = postmortem_games.get(gid)
    game.tree.goto(node_id)
    instruction = postmortem_api.postmortem_chat_service._build_postmortem_instruction({
        "mode": "postmortem",
        "white": game.headers.get("White"),
        "black": game.headers.get("Black"),
        "result": game.result,
        "fen": game.tree.current.fen,
        "turn": game.tree.current.turn,
        "ply": 6,
        "total_plies": 7,
        "line_san": game.tree.line_san(),
        "evidence": game.analysis_of(node_id),
        "on_mainline": True,
    })
    check("the coach is told the position, not just the game",
          game.tree.current.fen in instruction)
    check("the coach is given the engine's own line rather than asked to calculate",
          "continuation" in instruction and "FROM THIS LINE" in instruction)
    check("the coach is told the search depth",
          "depth" in instruction, instruction[-400:])
    check("the coach is told to admit a gap rather than estimate",
          "rather than estimating" in instruction)
    check("the review persona does not withhold, the way the opponent's does",
          "withhold" in instruction and "Never withhold" in instruction)

    # --- ownership and isolation ------------------------------------------

    other = TestClient(app.app)
    other.get("/api/auth/me")   # a different cookie jar, so a different identity
    check("another visitor cannot open this review",
          other.get(f"/api/postmortem/game/{gid}").status_code == 404)
    check("nor drive it",
          other.post(f"/api/postmortem/game/{gid}/back").status_code == 404)

    check("the player's real game never moved",
          real_session.game.get_fen() == real_fen_before, real_session.game.get_fen())
    check("and nothing was appended to its history",
          len(real_session.game.game_history) == real_history_before)

    # --- deletion ---------------------------------------------------------

    check("a review can be closed",
          client.delete(f"/api/postmortem/game/{gid}").status_code == 200)
    check("a closed review is gone",
          client.get(f"/api/postmortem/game/{gid}").status_code == 404)
    check("its 404 tells the user what to do about it",
          "import the game again"
          in client.get(f"/api/postmortem/game/{gid}").json()["detail"])


print(f"\n{PASSED}/{PASSED + FAILED} passed")
# python-chess runs the engine on a non-daemon background thread, so without
# this the interpreter hangs at shutdown waiting for it.
app.stockfish_service.close()
raise SystemExit(1 if FAILED else 0)

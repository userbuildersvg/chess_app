"""
"Review this game": a finished Play game becomes a Post-Mortem review in one
request, from the server's own board - no PGN pasted, nothing trusted from
the browser. Real Stockfish (the scan starts), no Gemini, no DATABASE_URL.

What is asserted: the route refuses a game that is not over; a mated game
comes back as a review with the right result, seats, ply count and
termination; the scan is already running; the review is owned by the same
identity so the existing Post-Mortem routes accept it; Black as the player
is seated correctly; a promotion replays; and the events carry only the
metadata they should.
"""
import os
import sys
import time

os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ.setdefault("DISABLE_LANGFLOW", "true")

from fastapi.testclient import TestClient

import chess

import learning_events
import postmortem_state
import app

results = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if not ok and detail else ""))
    results.append(ok)


def session_of(client):
    client.get("/api/status")
    return app.player_sessions.for_identity(next(iter(app.player_sessions.identities())))


def play(session, *ucis):
    """Push moves onto the server's own game, alternating human/AI as the
    routes would - the game object does not care who pushed."""
    for i, uci in enumerate(ucis):
        r = session.game.make_player_move(uci) if i % 2 == 0 else session.game.make_langflow_move(uci)
        assert r["success"], (uci, r)


FOOLS_MATE = ("f2f3", "e7e5", "g2g4", "d8h4")  # White (the player) is mated
SCHOLARS = ("e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7")  # White mates

with TestClient(app.app) as c:
    sink = learning_events.events

    # 1. Not over -> refused, and no review created.
    s = session_of(c)
    before = postmortem_state.postmortem_games.count()
    r = c.post("/api/postmortem/from-play")
    check("an unfinished game is refused with 409", r.status_code == 409, r.text[:200])
    check("...and no review was created", postmortem_state.postmortem_games.count() == before)

    # 2. The player (White) gets mated: result 0-1, seats You / Gemini.
    play(s, *FOOLS_MATE)
    s.ai_difficulty = 7
    r = c.post("/api/postmortem/from-play")
    check("a finished game opens a review", r.status_code == 200, r.text[:300])
    st = r.json()
    check("the response is the review state", "game_id" in st and "moves" in st and "headers" in st, str(st)[:200])
    check("result is Black's win", st.get("result") == "0-1", st.get("result"))
    check("termination is checkmate", (st.get("termination") or {}).get("kind") == "checkmate", str(st.get("termination")))
    check("four plies", st.get("total_plies") == 4, st.get("total_plies"))
    h = st.get("headers", {})
    check("the player is seated as White, the coach as Black", h.get("White") == "You" and h.get("Black") == "Gemini", str(h))
    check("the event header says where it came from", h.get("Event") == "Zugzwang Play", str(h))
    check("the response says which colour the player had", st.get("player_color") == "white", st.get("player_color"))
    check("the response says it came from Play", st.get("origin") == "play", st.get("origin"))
    check("the scan is already running or done", (st.get("scan") or {}).get("status") in ("running", "done"), str(st.get("scan")))
    gid = st["game_id"]
    # Owned by this identity: the ordinary routes accept it.
    r2 = c.get(f"/api/postmortem/game/{gid}")
    check("the review is owned by the same identity", r2.status_code == 200, r2.text[:200])
    r3 = c.post(f"/api/postmortem/game/{gid}/forward")
    check("navigation works on it", r3.status_code == 200 and r3.json().get("ply") == 1, r3.text[:200])
    # Wait for the scan to finish (4 plies, fast).
    for _ in range(100):
        a = c.get(f"/api/postmortem/game/{gid}/analysis").json()
        if a["scan"]["status"] == "done":
            break
        time.sleep(0.3)
    check("the scan finishes", a["scan"]["status"] == "done", str(a["scan"]))
    # The Play game itself is untouched by the handoff.
    check("the Play game is still on its final position", s.game.board.is_checkmate())

    # Events.
    recent = sink.recent(100)
    started = [e for e in recent if e["event"] == "play_game_analysis_started"]
    done = [e for e in recent if e["event"] == "play_game_analysis_completed"]
    check("play_game_analysis_started fired", len(started) == 1, str(started))
    check("play_game_analysis_completed fired", len(done) == 1, str(done))
    if started:
        e = started[-1]
        check("it carries result, termination, difficulty, colour and ply count",
              e.get("result") == "0-1" and e.get("termination") == "checkmate" and e.get("difficulty") == 7
              and e.get("player_color") == "white" and e.get("total_moves") == 4 and e.get("source_mode") == "Play", str(e))
        check("it carries no PGN or move text",
              all(not isinstance(v, str) or len(v) <= 40 for k, v in e.items()), str(e))

    # 3. Player as Black, and the player wins by mate (Scholar's mate mirrored: let White be the AI).
    c.post("/api/set-color", json={"color": "black"})
    # set-color schedules an AI move in the background; wait until it either
    # lands or we take over the board ourselves.
    time.sleep(1.5)
    s = session_of(c)
    s.game.reset_game()
    # White = AI (make_langflow_move), Black = player: 1.e4 e5 2.Nf3? no - Black mates: 1.f3 e5 2.g4 Qh4#
    for i, uci in enumerate(FOOLS_MATE):
        r_ = s.game.make_langflow_move(uci) if i % 2 == 0 else s.game.make_player_move(uci)
        assert r_["success"]
    r = c.post("/api/postmortem/from-play")
    st = r.json()
    check("Black as the player: seated as Black", r.status_code == 200 and st["headers"].get("Black") == "You"
          and st["headers"].get("White") == "Gemini", str(st.get("headers")))
    check("...and player_color says black", st.get("player_color") == "black")
    check("...and the result says Black (the player) won", st.get("result") == "0-1")

    # 4. A promotion replays through the same pipeline (PostMortemGame is the
    #    class the route builds; the PGN it writes is what this parses).
    c.post("/api/set-color", json={"color": "white"})
    time.sleep(0.5)
    s = session_of(c)
    s.game.reset_game()
    play(s, "e2e4", "f7f5", "e4f5", "g7g6", "f5g6", "g8f6", "g6h7", "f6g8", "h7g8q")
    check("promotion line pushed on the Play board", s.game.board.piece_at(chess.G8).piece_type == chess.QUEEN)
    r = c.post("/api/postmortem/from-play")
    check("an unfinished promotion game is still refused (it is not over)", r.status_code == 409, r.text[:200])
    pgn = app.play_game_pgn(s)
    check("the PGN the route writes carries the promotion", "hxg8=Q" in pgn, pgn)
    g = postmortem_state.game_from_pgn(pgn)
    g.tree.goto(g.mainline[-1])
    check("the shared pipeline replays it", g.tree.board_at().piece_at(chess.G8).piece_type == chess.QUEEN)

    # 5. A stalemate.
    s.game.reset_game()
    # Quickest known stalemate: 1.e3 a5 2.Qh5 Ra6 3.Qxa5 h5 4.h4 Rah6 5.Qxc7 f6 6.Qxd7+ Kf7 7.Qxb7 Qd3 8.Qxb8 Qh7 9.Qxc8 Kg6 10.Qe6 stalemate
    play(s, "e2e3", "a7a5", "d1h5", "a8a6", "h5a5", "h7h5", "h2h4", "a6h6", "a5c7", "f7f6",
         "c7d7", "e8f7", "d7b7", "d8d3", "b7b8", "d3h7", "b8c8", "f7g6", "c8e6")
    check("the stalemate line is a stalemate", s.game.board.is_stalemate())
    r = c.post("/api/postmortem/from-play")
    st = r.json()
    check("a stalemate opens a review with a drawn result", r.status_code == 200 and st.get("result") == "1/2-1/2"
          and (st.get("termination") or {}).get("kind") == "stalemate", r.text[:200])

print()
print(f"{sum(results)}/{len(results)} passed")
app.stockfish_service.close()
sys.exit(0 if all(results) else 1)

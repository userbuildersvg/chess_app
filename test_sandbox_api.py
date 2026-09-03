"""
Integration test for the /api/sandbox/* endpoints.

Real Stockfish and the real app wiring; only the move *decision* is faked,
so the test is deterministic and spends no Gemini quota. What it is actually
checking is the two properties the sandbox exists to guarantee:

  1. Taking over the board branches instead of overwriting (decision 2).
  2. A sandbox session cannot disturb the real game's state or the
     cross-game learning DB (decision 1), and two sessions cannot disturb
     each other (decision 5).

Run:
    DISABLE_LANGFLOW=true /tmp/chessapp/bin/python test_sandbox_api.py
"""

import app
import sandbox_api
from fastapi.testclient import TestClient

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


# Record exactly what the sandbox asks the decider for, and hand back a
# fixed legal move so the test is deterministic.
calls = []


async def fake_decide(fen, color, *, difficulty=None, last_move=None, use_learning=True):
    calls.append({
        "fen": fen, "color": color, "difficulty": difficulty,
        "last_move": last_move, "use_learning": use_learning,
    })
    import chess
    board = chess.Board(fen)
    move = sorted(m.uci() for m in board.legal_moves)[0]
    return move, "Fake reason.", "gemini"


sandbox_api.configure(fake_decide)
client = TestClient(app.app)

# The real game's state before anything sandboxed happens.
real_fen_before = app.game.get_fen()
real_history_before = len(app.game.game_history)
real_game_id_before = app.current_game_id


# --- session lifecycle --------------------------------------------------

r = client.post("/api/sandbox/session", json={"title": "Sicilian demo", "difficulty": 14})
check("create session returns 200", r.status_code == 200, r.status_code)
state = r.json()
sid = state["session_id"]
check("new session starts from the initial position",
      state["fen"].startswith("rnbqkbnr/pppppppp"), state["fen"])
check("new session reports its difficulty", state["difficulty"] == 14)
check("new session has a root-only tree", len(state["tree"]["nodes"]) == 1)

check("unknown session is a 404",
      client.get("/api/sandbox/session/nosuch").status_code == 404)


# --- custom start positions --------------------------------------------

good = client.post("/api/sandbox/session", json={"start_fen": "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"})
check("a valid custom FEN is accepted", good.status_code == 200, good.status_code)

bad = client.post("/api/sandbox/session", json={"start_fen": "8/8/8/8/8/8/8/8 w - - 0 1"})
check("an unreachable position is rejected at the door, not sent to Stockfish",
      bad.status_code == 400, bad.status_code)


# --- scenario generation, with the Gemini leg faked --------------------

import httpx  # noqa: E402
import scenario_service  # noqa: E402


class FakeScenarioClient:
    def __init__(self, body):
        self.body = body

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": self.body}]}}]},
            request=httpx.Request("POST", url),
        )


scenario_service.scenario_service.api_key = "fake"
scenario_service.scenario_service.models = ["m-a"]
_orig_httpx = httpx.AsyncClient

httpx.AsyncClient = FakeScenarioClient(
    '{"kind":"material","white_pieces":["pawn","pawn"],'
    '"black_pieces":["queen","rook"],"side_to_move":"white","favors":"black",'
    '"difficulty":17,"title":"Desperate defence","description":"Hold on."}')
try:
    r = client.post("/api/sandbox/scenario",
                    json={"prompt": "black has a queen and a rook, white has 2 pawns"})
    check("scenario generation returns a session", r.status_code == 200, r.text[:200])
    sc = r.json()
    check("the generated position is legal",
          __import__("chess").Board(sc["fen"]).is_valid(), sc["fen"])
    check("the generated material matches the request",
          sorted(p.symbol() for p in __import__("chess").Board(sc["fen"]).piece_map().values())
          == sorted("KPPkqr"),
          sc["fen"])
    check("the scenario's difficulty reaches the session", sc["difficulty"] == 17)
    check("the scenario metadata is returned alongside the board",
          sc["scenario"]["title"] == "Desperate defence"
          and sc["scenario"]["prompt"].startswith("black has"), sc.get("scenario"))
    check("the generated session is a real, playable sandbox session",
          client.get(f"/api/sandbox/session/{sc['session_id']}").status_code == 200)

    empty = client.post("/api/sandbox/scenario", json={"prompt": "   "})
    check("an empty scenario prompt is a 400", empty.status_code == 400, empty.status_code)

    httpx.AsyncClient = FakeScenarioClient(
        '{"kind":"opening","opening_name":"Zugzwang Gambit"}')
    unknown = client.post("/api/sandbox/scenario", json={"prompt": "the Zugzwang Gambit"})
    check("an opening the app doesn't know is a 400, not a bad position",
          unknown.status_code == 400, unknown.status_code)
finally:
    httpx.AsyncClient = _orig_httpx


# --- the AI plays, through the app's own decision function --------------

r = client.post(f"/api/sandbox/session/{sid}/ai-move")
check("ai-move returns 200", r.status_code == 200, r.status_code)
after_ai = r.json()
check("the AI's move is in the tree", len(after_ai["tree"]["nodes"]) == 2)
check("the played move is reported", after_ai["played"]["move"] is not None)
check("the AI's explanation is kept", after_ai["played"]["explanation"] == "Fake reason.")
check("the selection source is surfaced", after_ai["selection_source"] == "gemini")
ai_node = after_ai["played"]["id"]

check("the sandbox passed its own difficulty, not the global slider",
      calls[-1]["difficulty"] == 14, calls[-1]["difficulty"])
check("the sandbox opted out of the cross-game learning DB",
      calls[-1]["use_learning"] is False)


# --- user takes over and branches --------------------------------------

r = client.post(f"/api/sandbox/session/{sid}/back")
check("back returns to the position before the AI moved",
      r.json()["current_id"] == after_ai["tree"]["root_id"])

r = client.post(f"/api/sandbox/session/{sid}/move", json={"move": "d2d4"})
check("the user's move is accepted", r.status_code == 200, r.status_code)
branched = r.json()
check("taking over branched instead of overwriting",
      len(branched["tree"]["nodes"]) == 3, len(branched["tree"]["nodes"]))
check("the AI's original line still exists after the branch",
      ai_node in branched["tree"]["nodes"])
check("the user's branch is now current", branched["node"]["move"] == "d2d4")
check("the user's move is marked as theirs", branched["node"]["source"] == "human")

bad_move = client.post(f"/api/sandbox/session/{sid}/move", json={"move": "e2e9"})
check("an illegal move is a 400, not a crash", bad_move.status_code == 400, bad_move.status_code)

r = client.post(f"/api/sandbox/session/{sid}/goto", json={"node_id": ai_node})
check("goto rewinds into the abandoned line", r.json()["current_id"] == ai_node)
check("goto to an unknown node is a 404",
      client.post(f"/api/sandbox/session/{sid}/goto",
                  json={"node_id": "nosuch"}).status_code == 404)


# --- alternatives, via real Stockfish ------------------------------------

r = client.get(f"/api/sandbox/session/{sid}/alternatives")
check("alternatives returns 200", r.status_code == 200, r.status_code)
alt = r.json()
check("alternatives ranks real moves from Stockfish",
      len(alt["ranked"]) > 0 and "score" in alt["ranked"][0], alt["ranked"][:1])


# --- reset restarts the line without losing the session ------------------
#
# Every one of these covers a way the endpoint used to be wrong: it took a
# CreateSessionRequest, so a bare `{}` silently reset difficulty to that
# model's default of 20, and it passed the absent start_fen straight to
# MoveTree, which builds the *standard* position - so restarting a generated
# endgame quietly threw the endgame away. The frontend's difficulty control
# calls this endpoint, and both bugs were invisible from the response.

ENDGAME = "8/8/4k3/8/8/4K3/4P3/8 w - - 0 1"
rs = client.post(
    "/api/sandbox/session",
    json={"start_fen": ENDGAME, "difficulty": 6, "title": "K+P"},
).json()
rsid = rs["session_id"]
client.post(f"/api/sandbox/session/{rsid}/move", json={"move": "e2e4"})

reset_bare = client.post(f"/api/sandbox/session/{rsid}/reset", json={}).json()
check("reset drops the moves played so far", reset_bare["line_san"] == [],
      reset_bare["line_san"])
check("reset keeps this session's own starting position, not the standard one",
      reset_bare["fen"] == ENDGAME, reset_bare["fen"])
check("reset with no difficulty keeps the session's difficulty",
      reset_bare["difficulty"] == 6, reset_bare["difficulty"])

reset_hard = client.post(
    f"/api/sandbox/session/{rsid}/reset", json={"difficulty": 18}
).json()
check("reset applies a new difficulty", reset_hard["difficulty"] == 18,
      reset_hard["difficulty"])
check("a new difficulty does not disturb the position",
      reset_hard["fen"] == ENDGAME, reset_hard["fen"])

reset_clamped = client.post(
    f"/api/sandbox/session/{rsid}/reset", json={"difficulty": 99}
).json()
check("reset clamps difficulty to the 1-20 slider range",
      reset_clamped["difficulty"] == 20, reset_clamped["difficulty"])

reset_moved = client.post(
    f"/api/sandbox/session/{rsid}/reset",
    json={"start_fen": "8/8/8/4k3/8/4K3/4P3/8 w - - 0 1"},
).json()
check("an explicit start_fen still wins over the session's own root",
      reset_moved["fen"] == "8/8/8/4k3/8/4K3/4P3/8 w - - 0 1", reset_moved["fen"])
check("reset leaves the session itself alive",
      reset_moved["session_id"] == rsid)
client.delete(f"/api/sandbox/session/{rsid}")


# --- two sessions do not touch each other -------------------------------

other = client.post("/api/sandbox/session", json={}).json()["session_id"]
client.post(f"/api/sandbox/session/{other}/move", json={"move": "g1f3"})
a_state = client.get(f"/api/sandbox/session/{sid}").json()
b_state = client.get(f"/api/sandbox/session/{other}").json()
check("session B's move did not appear in session A",
      b_state["line_san"] == ["Nf3"] and a_state["line_san"] != ["Nf3"],
      (a_state["line_san"], b_state["line_san"]))


# --- the real game is untouched by all of the above ---------------------

check("the real game's board never moved", app.game.get_fen() == real_fen_before)
check("the real game's history never grew", len(app.game.game_history) == real_history_before)
check("the real game's learning row was never rotated",
      app.current_game_id == real_game_id_before)
check("the global difficulty slider was not changed", app.ai_difficulty == 20, app.ai_difficulty)
check("the real game's reversal state was not touched",
      app.last_ai_move_by_color == {"white": None, "black": None},
      app.last_ai_move_by_color)


# --- deletion ------------------------------------------------------------

check("delete removes the session", client.delete(f"/api/sandbox/session/{sid}").status_code == 200)
check("a deleted session is gone", client.get(f"/api/sandbox/session/{sid}").status_code == 404)


print(f"\n{PASSED}/{PASSED + FAILED} passed")
# python-chess runs the engine on a non-daemon background thread, so without
# this the interpreter hangs at shutdown waiting for it. The app closes the
# shared engine from its FastAPI shutdown hook; a script that imports app
# never fires that hook and has to do it itself. Same reason
# test_decide_integration.py ends this way.
app.stockfish_service.close()
raise SystemExit(1 if FAILED else 0)

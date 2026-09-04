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
from sandbox_state import sandbox_sessions
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


# --- what the coach is told about a forced mate --------------------------
# A position built and verified as a mate in two produced a coach that named
# the right first move and then a second move that did not mate. Two causes,
# both here rather than in the model:
#
#   * _analyse_moves reports a forced mate in "mate_in" and leaves "score"
#     None, and the chat context printed the score - so every move in a mating
#     position was described to the coach as being worth "None".
#   * Only pv[0] survived the ranking, so the engine's own mating line was
#     computed and then thrown away, leaving the model to work it out itself.

MATE_IN_TWO = "7k/8/6K1/8/8/8/8/6Q1 w - - 0 1"

ranked = app.stockfish_service.get_ranked_moves(MATE_IN_TWO, 5)
ranked = app.stockfish_service.refine_candidates(MATE_IN_TWO, ranked)
best = ranked[0]

check("a forced mate is found at all", best["mate_in"] is not None, best)
check("a forced mate reports no centipawn score", best["score"] is None, best)
check("the ranking carries the engine's continuation",
      bool(best.get("pv")), best.get("pv"))

check("a mate is described as a mate, not as None",
      sandbox_api._score_text(best) == "mate in 2", sandbox_api._score_text(best))
check("an ordinary score still reads as pawns",
      sandbox_api._score_text({"score": 632, "mate_in": None}) == "+6.32",
      sandbox_api._score_text({"score": 632, "mate_in": None}))
check("a mate against you says so",
      sandbox_api._score_text({"score": None, "mate_in": -3}) == "mate in 3 against",
      sandbox_api._score_text({"score": None, "mate_in": -3}))
check("an unknown score does not print None",
      sandbox_api._score_text({"score": None, "mate_in": None}) == "unknown")

import chess as _chess

mate_board = _chess.Board(MATE_IN_TWO)
line = sandbox_api._line_text(mate_board, best)
check("the mating line is handed over in SAN", bool(line), line)

# The line is only worth giving the coach if it is actually a mate. This walks
# it and checks, which is the property the whole fix rests on.
walker = mate_board.copy()
for uci in best["pv"]:
    move = _chess.Move.from_uci(uci)
    if move not in walker.legal_moves:
        break
    walker.push(move)
check("the line handed over actually mates", walker.is_checkmate(),
      f"{line} -> {walker.fen()}")
check("the line is exactly the two moves plus the reply",
      line == "Qa1+ Kg8 Qg7#", line)

# A line that runs off the end of legality must stop, not raise.
check("an illegal continuation is truncated rather than raising",
      sandbox_api._line_text(mate_board, {"pv": ["g1a1", "a1a1"]}) == "Qa1+")
check("no continuation gives no line",
      sandbox_api._line_text(mate_board, {"pv": []}) is None)


# --- the eval bar's endpoint ---------------------------------------------
# Its own endpoint rather than a field on every state response: it is a
# full-depth search sharing one engine lock with move selection, and it is
# fetched only while the bar is actually showing.

esid = client.post("/api/sandbox/session", json={
    "difficulty": 5, "start_fen": MATE_IN_TWO, "title": "Mate in two",
}).json()["session_id"]

ev = client.get(f"/api/sandbox/session/{esid}/eval")
check("eval answers 200", ev.status_code == 200, ev.status_code)
body = ev.json()
check("eval names the node it describes",
      body["node_id"] == sandbox_sessions.get(esid).tree.current_id, body)
check("eval sees the forced mate", body["mate_in"] == 2, body)
check("a mate reports no centipawn score", body["score"] is None, body)

# White's absolute perspective, not the side to move's - a bar that flipped
# meaning with the turn would be unreadable.
# The mate-in-two position mirrored, so Black is the one mating. Checked
# for legality first, and that is not fussiness: an unreachable position
# does not come back from Stockfish as an error, it hangs or segfaults the
# engine the real game shares - see the note in move_quality.py. The first
# spelling of this line put the two kings a square apart and did exactly
# that to the test run.
black_mates = "6q1/8/8/8/8/6k1/8/7K b - - 0 1"
bsid = client.post("/api/sandbox/session", json={
    "difficulty": 5, "start_fen": black_mates, "title": "Black mates",
}).json()["session_id"]
bbody = client.get(f"/api/sandbox/session/{bsid}/eval").json()
check("a mate for Black is reported as negative",
      bbody["mate_in"] is not None and bbody["mate_in"] < 0, bbody)
client.delete(f"/api/sandbox/session/{bsid}")

check("eval on a missing session is a 404",
      client.get("/api/sandbox/session/nope/eval").status_code == 404)
client.delete(f"/api/sandbox/session/{esid}")


# --- the composer's intent classifier ------------------------------------
# Learner Mode has one input that both asks questions and builds positions, so
# something has to decide which was meant. The model does that; what is tested
# here is the contract around it, because the failure mode is not "a slightly
# odd reply" - a message misread as BUILD proposes throwing away the line the
# student is studying, and sandbox sessions have no undo.

import gemini_chat_service


class FakeChat:
    """Stands in for the Gemini call so the test spends no quota."""

    def __init__(self, reply, success=True):
        self.reply = reply
        self.success = success
        self.contexts = []

    async def send_message(self, message, history, context):
        self.contexts.append(context)
        return self.success, self.reply


real_chat_service = sandbox_api.sandbox_chat_service


def classify(reply, success=True):
    fake = FakeChat(reply, success)
    sandbox_api.sandbox_chat_service = fake
    try:
        response = client.post("/api/sandbox/classify", json={"message": "anything"})
        return response, fake
    finally:
        sandbox_api.sandbox_chat_service = real_chat_service


resp, fake = classify("BUILD")
check("a BUILD verdict is reported as build", resp.json()["intent"] == "build", resp.json())
check("the classifier asks for the intent persona, not the coach",
      fake.contexts and fake.contexts[0].get("mode") == "intent", fake.contexts)

resp, _ = classify("ASK")
check("an ASK verdict is reported as ask", resp.json()["intent"] == "ask", resp.json())

# The instruction asks for one bare word. A model that decides to be helpful
# must not read as ASK just because its explanation contains the word.
resp, _ = classify("BUILD - they want a new position set up")
check("a verdict with an explanation after it is still build",
      resp.json()["intent"] == "build", resp.json())
resp, _ = classify("**BUILD**")
check("a verdict the model decided to embolden is still build",
      resp.json()["intent"] == "build", resp.json())
resp, _ = classify("build")
check("a lowercase verdict is still build", resp.json()["intent"] == "build", resp.json())

# Anything unrecognisable is a question. This is the whole safety property:
# guessing "ask" costs an odd reply, guessing "build" offers to destroy a line.
resp, _ = classify("I'm not sure what you mean")
check("an unrecognisable verdict falls back to ask",
      resp.json()["intent"] == "ask", resp.json())
resp, _ = classify("POSITION")
check("a word that merely mentions positions is not a build",
      resp.json()["intent"] == "ask", resp.json())

# Gemini being unreachable must not surface as an error the composer has to
# handle - it has a safe reading available and takes it.
resp, _ = classify("all models unavailable", success=False)
check("an unavailable model answers 200, not an error", resp.status_code == 200, resp.status_code)
check("an unavailable model classifies as ask", resp.json()["intent"] == "ask", resp.json())
check("an unavailable model says it did not classify",
      resp.json()["classified"] is False, resp.json())

check("an empty message is refused",
      client.post("/api/sandbox/classify", json={"message": "   "}).status_code == 400)


# --- reset can rename the session ----------------------------------------
# The panel heading is session.title. A session opened by /scenario carries
# that scenario's name, so resetting it onto the standard position left the
# heading describing a board that was no longer there.

titled = client.post("/api/sandbox/session", json={"difficulty": 5, "title": "Hard Rook Endgame"}).json()
tid = titled["session_id"]
check("a session keeps the title it was opened with",
      titled["title"] == "Hard Rook Endgame", titled.get("title"))

kept = client.post(f"/api/sandbox/session/{tid}/reset", json={}).json()
check("a reset with no title leaves the title alone",
      kept["title"] == "Hard Rook Endgame", kept.get("title"))

STANDARD = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
renamed = client.post(
    f"/api/sandbox/session/{tid}/reset",
    json={"start_fen": STANDARD, "title": "Sandbox"},
).json()
check("reset to the standard position renames the session",
      renamed["title"] == "Sandbox", renamed.get("title"))
check("reset to the standard position puts the pieces back",
      renamed["fen"] == STANDARD, renamed["fen"])
check("reset to the standard position keeps the difficulty",
      renamed["difficulty"] == 5, renamed["difficulty"])
check("reset to the standard position drops the tree",
      len(renamed["tree"]["nodes"]) == 1, len(renamed["tree"]["nodes"]))

blank = client.post(
    f"/api/sandbox/session/{tid}/reset", json={"title": "   "},
).json()
check("a whitespace title is ignored rather than blanking the heading",
      blank["title"] == "Sandbox", blank.get("title"))

client.delete(f"/api/sandbox/session/{tid}")


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

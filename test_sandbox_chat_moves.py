"""
"Play Nf3" in the Learn chat (chat_moves.py, sandbox_api chat route).

    DISABLE_LANGFLOW=true /tmp/chessapp/bin/python test_sandbox_chat_moves.py

Needs Stockfish for the best-move half. No Gemini: an instruction never
reaches the model, and the classifier short-circuits it too.

What is proved: the parser takes explicit instructions in SAN, UCI,
"knight to f3" and castling, and leaves questions alone; resolution is
python-chess against the real board - illegal refused, ambiguous asked
about, never applied; through the API the board moves for a legal
instruction and stays put for a refusal; "best move" comes from the engine
and is legal; Review's own games are untouched (the sandbox is its own
store).
"""
import os

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")

import chess
from fastapi.testclient import TestClient

import app
import chat_moves as cm

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


section("parser: instructions")
for text, want in [
    ("Play Nf3.", {"kind": "move", "text": "Nf3"}),
    ("play nf3", None),                                   # lower-case n is not SAN
    ("Make e2e4 on the board", {"kind": "move", "text": "e2e4"}),
    ("Can you play d4 for White?", {"kind": "move", "text": "d4"}),
    ("Move my knight to f3", {"kind": "move", "text": "knight to f3"}),
    ("knight to f3", {"kind": "move", "text": "knight to f3"}),
    ("Play O-O", {"kind": "move", "text": "O-O"}),
    ("castle queenside", {"kind": "move", "text": "O-O-O"}),
    ("What is the best move here? Make it on the board.", {"kind": "referenced"}),
    ("make that move", {"kind": "referenced"}),
    ("play it", {"kind": "referenced"}),
    ("go with your suggestion", {"kind": "referenced"}),
    ("take with the bishop", {"kind": "capture", "square": None, "piece": "bishop", "victim": None}),
    ("capture on e5", {"kind": "capture", "square": "e5", "piece": None, "victim": None}),
    ("bishop takes knight", {"kind": "capture", "square": None, "piece": "bishop", "victim": "knight"}),
    ("promote to queen", {"kind": "promote", "piece": chess.QUEEN}),
    ("Play the best move.", {"kind": "best"}),
    ("play the engine move", {"kind": "best"}),
    ("Put the strongest move on the board", {"kind": "best"}),
]:
    check(f"parse {text!r}", cm.parse_request(text) == want, cm.parse_request(text))

section("parser: questions are left to the coach")
for text in ["What is the best move here?", "Why not Nf3?", "Is d4 good?", "What does the knight on f3 do?",
             "Show me a hard rook endgame as white", "Was e4 played here?", "explain the position",
             "Is it good to play it?", "should I play it", "can you take the pawn?", "What does it take to win?"]:
    check(f"not an instruction: {text!r}", cm.parse_request(text) is None, cm.parse_request(text))

section("resolution against the board")
b = chess.Board()
mv, why = cm.resolve_move(b, "Nf3")
check("SAN legal", mv == chess.Move.from_uci("g1f3") and why is None, why)
mv, why = cm.resolve_move(b, "g1f3")
check("UCI legal", mv == chess.Move.from_uci("g1f3"))
mv, why = cm.resolve_move(b, "knight to f3")
check("piece-to-square legal", mv == chess.Move.from_uci("g1f3"))
mv, why = cm.resolve_move(b, "Nf6")
check("illegal SAN refused with a reason", mv is None and "not legal" in why, why)
mv, why = cm.resolve_move(b, "e2e5")
check("illegal UCI refused", mv is None and "not legal" in why)
mv, why = cm.resolve_move(b, "queen to h5")
check("no such piece move refused", mv is None and "No queen" in why, why)
mv, why = cm.resolve_move(b, "Zz9")
check("nonsense refused", mv is None and "recognise" in why, why)
amb = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/2N1N3/PPPP1PPP/R1BQKB1R w KQkq - 0 1")
mv, why = cm.resolve_move(amb, "Nd5")
check("ambiguous SAN asks which", mv is None and "ambiguous" in why and "Ncd5" in why and "Ned5" in why, why)
mv, why = cm.resolve_move(amb, "knight to d5")
check("ambiguous piece-to-square asks which", mv is None and "More than one knight" in why, why)
mv, why = cm.resolve_move(amb, "Ncd5")
check("disambiguated SAN resolves", mv is not None and amb.san(mv) == "Ncd5")

section("captures, promotion, referenced move")
ital = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4")
mv, why = cm.resolve_capture(ital, {"square": "e5", "piece": None, "victim": None})
check("capture on e5 -> Nxe5", mv is not None and ital.san(mv) == "Nxe5", why)
mv, why = cm.resolve_capture(ital, {"square": None, "piece": "bishop", "victim": None})
check("take with the bishop -> Bxf7+", mv is not None and ital.san(mv) == "Bxf7+", why)
mv, why = cm.resolve_capture(ital, {"square": None, "piece": None, "victim": "pawn"})
check("take the pawn is ambiguous here -> asks", mv is None and "Bxf7+" in why and "Nxe5" in why, why)
mv, why = cm.resolve_capture(ital, {"square": "a1", "piece": None, "victim": None})
check("no capture there -> refused", mv is None and "no legal capture" in why, why)
promo = chess.Board("8/P7/8/8/8/8/8/k6K w - - 0 1")
for text in ("a8", "a7a8", "pawn to a8"):
    mv, why = cm.resolve_move(promo, text)
    check(f"{text!r} without a piece asks which", mv is None and "which piece" in why, why)
mv, why = cm.resolve_move(promo, "a8=R")
check("a8=R promotes to a rook", mv == chess.Move.from_uci("a7a8r"), why)
mv, why = cm.resolve_promotion(promo, chess.KNIGHT)
check("promote to knight resolves", mv == chess.Move.from_uci("a7a8n"), why)
mv, why = cm.resolve_promotion(chess.Board(), chess.QUEEN)
check("no promotion available -> refused", mv is None and "No pawn" in why, why)
mv, why = cm.referenced_move(chess.Board(), [{"role": "model", "text": "I would play Nf3 here, developing with tempo."}])
check("'that move' = the one move the coach named", mv == chess.Move.from_uci("g1f3"), why)
mv, why = cm.referenced_move(chess.Board(), [{"role": "model", "text": "Both e4 and d4 are fine openings."}])
check("two moves named -> asks which", mv is None and "e4" in why and "d4" in why, why)
mv, why = cm.referenced_move(chess.Board(), [{"role": "model", "text": "Develop your pieces first."}])
check("no move named -> (None, None) so the caller uses the engine", mv is None and why is None)
mv, why = cm.referenced_move(chess.Board(), [{"role": "model", "text": "Nf3 was strong."}, {"role": "user", "text": "ok"}])
check("the LAST coach turn is what 'that' means", mv == chess.Move.from_uci("g1f3"))

section("through the API")
with TestClient(app.app) as c:
    sid = c.post("/api/sandbox/session", json={}).json()["session_id"]
    fen0 = c.get(f"/api/sandbox/session/{sid}").json()["fen"]
    r = c.post(f"/api/sandbox/session/{sid}/chat", json={"message": "Play Nf3"})
    check("legal instruction -> 200, board changed, state returned", r.status_code == 200 and r.json()["board_changed"] is True
          and r.json()["state"]["line_san"] == ["Nf3"], r.text[:200])
    check("the chat confirms what was played", r.json()["reply"] == "I played Nf3 on the board.", r.json()["reply"])
    check("the transcript holds both turns", [t["role"] for t in r.json()["history"][-2:]] == ["user", "model"])
    r = c.post(f"/api/sandbox/session/{sid}/chat", json={"message": "Play Nf3"})
    check("illegal now (it is Black to move) -> refused, board unchanged",
          r.json()["board_changed"] is False and "not legal" in r.json()["reply"]
          and c.get(f"/api/sandbox/session/{sid}").json()["line_san"] == ["Nf3"], r.json()["reply"])
    r = c.post(f"/api/sandbox/session/{sid}/chat", json={"message": "knight to c6"})
    check("black's knight to c6 by piece name", r.json()["board_changed"] is True and r.json()["state"]["line_san"] == ["Nf3", "Nc6"])
    r = c.post(f"/api/sandbox/session/{sid}/chat", json={"message": "What is the best move? Make it on the board."})
    body = r.json()
    check("best move: played, legal, named by the engine", body["board_changed"] is True and len(body["state"]["line_san"]) == 3
          and body["reply"].startswith("I played the engine's preferred move, ") and body["state"]["line_san"][2] in body["reply"], body.get("reply"))
    check("moves are the user's in the tree, not the AI's", all(n["source"] in ("human", "setup") for n in body["state"]["tree"]["nodes"].values()))
    # "make that move" after the coach named one: seed the transcript the way a
    # real reply would land, then the instruction plays exactly that move.
    import sandbox_state as ss
    sess = ss.sandbox_sessions.get(sid)
    board_now = sess.tree.board_at()
    named = board_now.san(next(iter(board_now.legal_moves)))
    sess.chat_history.append({"role": "model", "text": f"Here I would consider {named} - it keeps things flexible."})
    r = c.post(f"/api/sandbox/session/{sid}/chat", json={"message": "make that move"})
    body = r.json()
    check("'make that move' plays the move the coach named", body["board_changed"] is True and body["state"]["line_san"][-1] == named
          and body["reply"] == f"I played {named} on the board.", body.get("reply"))
    r = c.post(f"/api/sandbox/session/{sid}/chat", json={"message": "take the king"})
    check("an impossible capture is refused and the board stays", r.json()["board_changed"] is False and "no legal capture" in r.json()["reply"], r.json()["reply"])
    r = c.post(f"/api/sandbox/session/{sid}/chat", json={"message": "promote to queen"})
    check("promotion with no pawn to promote is refused", r.json()["board_changed"] is False and "No pawn" in r.json()["reply"], r.json()["reply"])
    r = c.post("/api/sandbox/classify", json={"message": "Play e4"})
    check("the classifier never sends an instruction to BUILD", r.json() == {"intent": "ask", "classified": True, "instruction": True}, r.json())
    check("Review's store is untouched by all of this", c.get("/api/postmortem/game/nope").status_code == 404)
    # Play and Review never execute chat instructions: only the sandbox
    # router wires chat_moves in. Structural, so a future import shows here.
    import pathlib
    check("only sandbox_api imports chat_moves",
          "chat_moves" in pathlib.Path("sandbox_api.py").read_text()
          and all("chat_moves" not in pathlib.Path(f).read_text() for f in ("app.py", "postmortem_api.py", "learning_loop_api.py")))
    check("the starting position was the standard one", fen0.startswith("rnbqkbnr"))

print(f"\n{PASSED} passed, {FAILED} failed")
try:
    app.stockfish_service.close()
except Exception:
    pass
raise SystemExit(1 if FAILED else 0)

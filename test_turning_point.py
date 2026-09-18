"""
"Where did I start losing?" in Review chat (turning_point.py, postmortem_api.chat).

    DISABLE_LANGFLOW=true /tmp/chessapp/bin/python test_turning_point.py

Needs Stockfish for the scan. No Gemini: the chat model is faked, once to
answer and once to fail, because the selection must not depend on it.

What is proved: the question is recognised and ordinary questions are not;
selection is deterministic on synthetic evidence - the user's colour only,
book/forced excluded, band changes outrank raw loss, a close field is
"unclear" with candidates, no colour is said so; through the API a review
without a scan is told to analyse first, a scanned Scholar's mate names the
losing move with FEN/eval/preferred move that all come from the scan, the
board is not moved by the server, the model's failure falls back to a
complete deterministic reply, and the answer is in the transcript so a
remount keeps its buttons.
"""
import os
import time

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")

import chess
from fastapi.testclient import TestClient

import app
import postmortem_api
import turning_point as tp
from postmortem_state import postmortem_games

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


section("recognising the question")
for q in ["Where did I start losing?", "Where did I throw the advantage?", "What move changed the game?",
          "When did I lose my edge?", "Show me the position where it went wrong.", "Why did I lose this game?",
          "Where did my advantage disappear?", "what was the turning point", "where did it go wrong"]:
    check(f"asks: {q!r}", tp.is_question(q))
for q in ["Why was that a mistake?", "What should I have been looking for?", "what happens if I lose the exchange?",
          "Is Nf3 good?", "why did I lose the bishop?", "what is the best move", "explain the position"]:
    check(f"not the question: {q!r}", not tp.is_question(q))

section("selection on synthetic evidence")


def ev(ply, color, cpl, before, after, label="mistake", san=None, best="Xx1"):
    return {"ply": ply, "san": san or f"M{ply}", "uci": "a1a1", "color": color, "cpl": cpl,
            "quality": {"label": label, "name": label.title(), "cpl": cpl},
            "eval_before": {"score": before, "mate_in": None}, "eval_after": {"score": after, "mate_in": None},
            "best_san": best, "best_move": "b1b1", "pv_san": [best], "fen_before": f"F{ply}", "fen_after": f"F{ply + 1}",
            "node_id": f"n{ply}", "node_before_id": f"n{ply - 1}"}


# White (the user) is +120, plays a 300cp blunder to -180, never recovers.
game = [ev(1, "white", 0, 20, 20, "book"), ev(2, "black", 0, 20, 20, "book"),
        ev(3, "white", 40, 20, -20, "inaccuracy"), ev(4, "black", 150, -20, 120, "mistake"),
        ev(5, "white", 300, 120, -180, "blunder", san="Bf4", best="Bc3"), ev(6, "black", 10, -180, -190, "good"),
        ev(7, "white", 60, -190, -250, "inaccuracy")]
a = tp.select(game, "white")
check("clear turning point found", a["status"] == "clear", a)
t = a["turning_point"]
check("it is the user's 300cp blunder, not Black's 150", t["ply"] == 5 and t["san"] == "Bf4", t)
check("band change recorded: better -> losing", t["band_before"] == "better" and t["band_after"] == "losing", t)
check("never recovered", t["sustained"] is True)
check("engine preference carried from the evidence, not invented", t["best_san"] == "Bc3")
check("FENs and evals are the scan's", t["fen_before"] == "F5" and t["eval_before"] == {"score": 120, "mate_in": None})
check("node ids for the browser to jump to", t["node_id"] == "n5" and t["node_before_id"] == "n4")
check("confidence high for a big swing", a["confidence"] == "high" and a["caveat"] is None, a)
check("candidates lead with the turning point", a["candidates"][0]["ply"] == 5 and len(a["candidates"]) <= 3)
check("move label reads 3. Bf4", tp._label(t) == "3. Bf4", tp._label(t))

b = tp.select(game, "black")
check("as Black the pick is Black's own worst move", b["turning_point"] is not None and b["turning_point"]["ply"] == 4, b)

u = tp.select(game, None)
check("no colour -> biggest swing either side, with the caveat", u["status"] == "clear" and u["turning_point"]["ply"] == 5
      and "colour is not recorded" in u["caveat"], u)

drift = [ev(1, "white", 70, 30, -40, "inaccuracy"), ev(2, "black", 0, -40, -40, "good"),
         ev(3, "white", 65, -40, -105, "inaccuracy"), ev(4, "black", 0, -105, -105, "good"),
         ev(5, "white", 60, -105, -165, "inaccuracy")]
d = tp.select(drift, "white")
check("several small slips -> unclear, no single turning point", d["status"] == "unclear" and d["turning_point"] is None, d)
check("up to three candidates listed", 1 <= len(d["candidates"]) <= 3)
check("the fallback text says so honestly", tp.fallback_text(d).startswith("I do not see one clean losing move"), tp.fallback_text(d))

clean = [ev(1, "white", 0, 20, 20, "book"), ev(2, "black", 200, 20, 220, "blunder"), ev(3, "white", 0, 220, 220, "best")]
n = tp.select(clean, "white")
check("no losing move by the user -> none", n["status"] == "none" and n["candidates"] == [], n)

forced = [ev(1, "white", 500, 0, -500, "forced")]
check("a forced move is not a decision", tp.select(forced, "white")["status"] == "none")

text = tp.fallback_text(a)
check("deterministic reply names move, evals, loss and preferred move",
      "3. Bf4" in text and "+1.20" in text and "-1.80" in text and "3.0 pawns" in text and "Bc3" in text, text)
lines = "\n".join(tp.prompt_lines(a))
check("the prompt fences the model to the scan's facts", "not to pick a different move" in lines and "Bc3" in lines and "F5" in lines)

section("through the API")
SCHOLARS = """[Event "Casual"]
[White "You"]
[Black "Them"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""

with TestClient(app.app) as c:
    gid = c.post("/api/postmortem/import", json={"pgn": SCHOLARS}).json()["game_id"]
    game = postmortem_games.get(gid)
    game.player_color = "black"
    replies = []

    async def fake_send(message, history, context):
        replies.append(context)
        return True, "Explained."

    async def failing_send(message, history, context):
        replies.append(context)
        return False, "model down"

    real_send = postmortem_api.postmortem_chat_service.send_message
    postmortem_api.postmortem_chat_service.send_message = fake_send
    try:
        r = c.post(f"/api/postmortem/game/{gid}/chat", json={"message": "Where did I start losing?"})
        check("before the scan finishes: told to analyse first, no model call",
              r.status_code == 200 and "review analysis first" in r.json()["reply"] and not replies, r.json().get("reply"))
        analysis = {}
        for _ in range(600):
            analysis = c.get(f"/api/postmortem/game/{gid}/analysis").json()
            if analysis["scan"]["status"] in {"done", "failed"}:
                break
            time.sleep(0.2)
        check("the scan the question started has finished", analysis["scan"]["status"] == "done", analysis["scan"])

        c.post(f"/api/postmortem/game/{gid}/goto", json={"node_id": game.mainline[-1]})
        r = c.post(f"/api/postmortem/game/{gid}/chat", json={"message": "Where did I start losing?"})
        body = r.json()
        answer = body.get("turning_point")
        check("the answer carries the engine's turning point", r.status_code == 200 and answer and answer["status"] in ("clear", "unclear"), body)
        cand = (answer or {}).get("turning_point") or ((answer or {}).get("candidates") or [None])[0]
        check("it is one of Black's moves (the user's colour)", cand and cand["color"] == "black", cand)
        check("the losing move is 3...Nf6 (allowing Qxf7#)", cand and cand["ply"] == 6 and cand["san"] == "Nf6", cand)
        evidence = game.analysis_of(cand["node_id"])
        check("FEN before/after and preferred move are the scan's own, verbatim",
              evidence and cand["fen_before"] == evidence["fen_before"] and cand["fen_after"] == evidence["fen_after"]
              and cand["best_san"] == evidence["best_san"] and cand["eval_before"] == evidence["eval_before"], cand)
        check("node ids point at the mainline", cand["node_id"] == game.mainline[6] and cand["node_before_id"] == game.mainline[5])
        check("the model was handed the same facts to explain, not to choose",
              replies and replies[-1].get("turning_point") is not None and replies[-1]["turning_point"]["candidates"][0]["san"] == cand["san"])
        check("the server did not move the board (the browser decides)", c.get(f"/api/postmortem/game/{gid}").json()["ply"] == 7)
        check("the transcript's coach turn carries the answer for remounts",
              body["history"][-1]["role"] == "model" and body["history"][-1].get("turning_point", {}).get("candidates"))
        check("the transcript is served back with it", c.get(f"/api/postmortem/game/{gid}/chat").json()["history"][-1].get("turning_point") is not None)

        postmortem_api.postmortem_chat_service.send_message = failing_send
        r = c.post(f"/api/postmortem/game/{gid}/chat", json={"message": "What move changed the game?"})
        check("model down -> 200 with the deterministic answer, not a 502",
              r.status_code == 200 and "Nf6" in r.json()["reply"] and r.json()["turning_point"] is not None, r.text[:300])
        r = c.post(f"/api/postmortem/game/{gid}/chat", json={"message": "Why was that a mistake?"})
        check("an ordinary question still fails honestly as before (502)", r.status_code == 502, r.status_code)
        check("and carried no turning point into the prompt", replies[-1].get("turning_point") is None)
    finally:
        postmortem_api.postmortem_chat_service.send_message = real_send

print(f"\n{PASSED} passed, {FAILED} failed")
try:
    app.stockfish_service.close()
except Exception:
    pass
raise SystemExit(1 if FAILED else 0)

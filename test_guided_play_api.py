"""
Guided Play through the real wiring: decide_ai_move() with the flag, and the
two Play routes that carry it. Real Stockfish, faked Gemini, no key, no
DATABASE_URL.

What this proves that test_guided_play.py cannot: the flag actually reaches
the prompt, the section actually comes back apart from the explanation and
lands on the coach turn under its own key, standard mode is untouched, and
the events fire with the metadata they are meant to carry and nothing else.
"""
import asyncio
import os
import sys
import time

os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ.setdefault("DISABLE_LANGFLOW", "true")

import httpx
from fastapi.testclient import TestClient

import gemini_http
import learning_events
from opponent_profiles import PROFILE_IDS
import app

FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
seen = {}
results = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if not ok and detail else ""))
    results.append(ok)


class FakeClient:
    """Gemini that always picks the first shortlisted move and, when the
    prompt asked for one, appends a Watch out line."""
    is_closed = False

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **kwargs):
        text = json["contents"][0]["parts"][0]["text"]
        seen["prompt"] = text
        moves = [ln.split(". ", 1)[1].split(" ")[0]
                 for ln in text.splitlines() if ln[:1].isdigit() and ". " in ln]
        reply = f"{moves[0]}\nI take the centre and keep my pieces coordinated."
        if "Watch out" in text:
            reply += "\nWatch out: check whether your central pawn is still defended, and look at every check and capture I have next move."
        body = {"candidates": [{"content": {"parts": [{"text": reply}]}}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))


app.gemini_move_service.api_key = "fake-key"
_orig_client = httpx.AsyncClient
httpx.AsyncClient = FakeClient()
gemini_http.reset()

try:
    # --- decide_ai_move ------------------------------------------------------
    move, expl, source, decision = asyncio.run(app.decide_ai_move(FEN, "white"))
    check("standard mode still routes through Gemini", source == "gemini", source)
    check("standard mode's prompt names the opponent level", "Club opponent" in seen["prompt"], seen["prompt"][:400])
    check("standard mode's prompt does not let the coach admit a flaw", "left one of your pieces loose" not in seen["prompt"])
    check("standard mode's prompt has no Watch out section", "Watch out" not in seen["prompt"])
    check("standard mode's prompt has no board facts", "Board facts" not in seen["prompt"])
    check("standard explanation has no section", "Watch out" not in expl, expl)

    move, expl, source, decision = asyncio.run(app.decide_ai_move(FEN, "white", guided=True))
    check("guided mode routes through Gemini", source == "gemini", source)
    check("guided mode at club does not invite admitting a flaw", "left one of your pieces loose" not in seen["prompt"])
    check("guided mode still forbids naming the reply", "do NOT recommend, name or hint" in seen["prompt"])
    check("guided mode's prompt asks for the section", "Watch out" in seen["prompt"])
    check("guided mode's prompt carries engine facts for the shortlist",
          "Board facts" in seen["prompt"] and move in seen["prompt"].split("Board facts", 1)[1])
    check("guided mode's prompt still carries the FEN", FEN in seen["prompt"])
    check("the section comes back inside the explanation on its own line",
          "\nWatch out:" in expl, expl)
    _, _, _, _ = asyncio.run(app.decide_ai_move(FEN, "white", guided=True, profile="beginner"))
    check("guided mode at beginner may admit the move's own flaw",
          "left one of your pieces loose" in seen["prompt"] and "Still do not name the move" in seen["prompt"])
    check("a beginner's guided prompt still forbids naming the reply", "do NOT recommend, name or hint" in seen["prompt"])

    # --- the routes -----------------------------------------------------------
    sink = learning_events.events
    with TestClient(app.app) as c:
        # Human plays Black so it is the AI's move from the start; the
        # scheduled auto-move is the same background path /api/move uses.
        r = c.post("/api/set-color", json={"color": "black"})
        check("set-color as black", r.status_code == 200 and r.json().get("success"), r.text[:200])

        def wait_for_turns(n, tries=100):
            for _ in range(tries):
                st = c.get("/api/status").json()
                if len([t for t in st["chat_history"] if t.get("move")]) >= n:
                    return st
                time.sleep(0.2)
            return st

        # The colour switch schedules White's first move in the background
        # (standard, not guided). Let it land so the manual route below is
        # not refused as "already thinking", then start again from a known
        # state: reset keeps Black, and the AI is to move.
        wait_for_turns(1)
        c.post("/api/reset")
        r = c.post("/api/ai-move", json={"guided": True})
        check("manual guided AI move accepted", r.status_code == 200 and r.json().get("success"), r.text[:300])
        status = c.get("/api/status").json()
        turns = [t for t in status["chat_history"] if t.get("role") == "model" and t.get("move")]
        check("a coach turn exists after the AI moved", len(turns) >= 1, str(status.get("chat_history"))[:300])
        guided_turns = [t for t in turns if t.get("watch_out")]
        check("the guided AI move's coach turn carries watch_out",
              len(guided_turns) >= 1, str(turns)[:400])
        if guided_turns:
            t = guided_turns[-1]
            check("watch_out text is the section without its label",
                  t["watch_out"].startswith("check whether"), t["watch_out"])
            check("the turn text still ends with the section for replay",
                  t["text"].endswith(t["watch_out"]), t["text"])
            check("the explanation body precedes it in text",
                  "coordinated." in t["text"].split("Watch out:")[0], t["text"])
        history_expl = [h.get("explanation") for h in status["history"] if h.get("player") == "langflow"]
        check("the move list's hover explanation does not carry the section",
              bool(history_expl) and all("Watch out" not in (e or "") for e in history_expl), str(history_expl))

        # Standard mode on the same session: no section.
        c.post("/api/reset")
        r = c.post("/api/ai-move", json={"guided": False})
        status = c.get("/api/status").json()
        turns = [t for t in status["chat_history"] if t.get("role") == "model" and t.get("move")]
        check("a standard AI move's coach turn has no watch_out",
              bool(turns) and all("watch_out" not in t for t in turns), str(turns)[:300])

        # No body at all still works (older clients, curl).
        c.post("/api/reset")
        r = c.post("/api/ai-move")
        check("/api/ai-move without a body is still accepted", r.status_code == 200, r.text[:200])

        # /api/move carries the flag too.
        c.post("/api/set-color", json={"color": "white"})
        r = c.post("/api/move", json={"move": "e2e4", "guided": True})
        check("/api/move accepts guided", r.status_code == 200 and r.json().get("success"), r.text[:200])
        r = c.post("/api/move", json={"move": "e2e4"})
        check("/api/move without guided is unchanged (this one is refused only because it is not our turn or already played)",
              r.status_code == 200, r.text[:200])

        # The toggle itself is reported by the browser through the existing
        # events route, with the opponent profile it was flipped at.
        r = c.post("/api/learning-loop/events",
                   json={"name": "guided_play_enabled", "source_mode": "Play",
                         "opponent_profile": "club", "approx_elo": 1500})
        check("the events route accepts guided_play_enabled with a profile",
              r.status_code == 200 and r.json().get("recorded"), r.text[:200])
        toggled = [e for e in sink.recent(50) if e["event"] == "guided_play_enabled"]
        check("the toggle event carries the profile", toggled and toggled[-1].get("opponent_profile") == "club"
              and toggled[-1].get("approx_elo") == 1500, str(toggled))

        # The profile routes: seven ids, an unknown one refused, the choice
        # reported back with its Elo.
        r = c.post("/api/difficulty", json={"profile": "wizard"})
        check("an unknown profile is refused", r.status_code == 200 and r.json().get("success") is False
              and "allowed" in r.json(), r.text[:300])
        r = c.post("/api/difficulty", json={"profile": "beginner"})
        check("a profile is set and reported with its Elo", r.json()["profile"] == "beginner"
              and r.json()["approx_elo"] == 400, r.text[:300])
        r = c.get("/api/difficulty")
        check("the seven profiles are listed in order",
              [p["id"] for p in r.json()["profiles"]] == list(PROFILE_IDS), r.text[:300])
        check("status reports the profile", c.get("/api/status").json().get("opponent_profile") == "beginner")
        c.post("/api/difficulty", json={"profile": "master"})

    # --- events ---------------------------------------------------------------
    recent = sink.recent(200)
    expl_events = [e for e in recent if e["event"] == "ai_move_explanation_generated"]
    watch_events = [e for e in recent if e["event"] == "guided_watchout_generated"]
    check("ai_move_explanation_generated fired", len(expl_events) >= 2, str(recent)[-400:])
    check("guided_watchout_generated fired for the guided move only",
          len(watch_events) >= 1 and all(e.get("guided") is True for e in watch_events), str(watch_events))
    if expl_events:
        e = expl_events[-1]
        check("the event carries the profile, ply, side and source_mode=Play",
              e.get("opponent_profile") in PROFILE_IDS and isinstance(e.get("approx_elo"), int)
              and isinstance(e.get("ply_index"), int)
              and e.get("side_to_move") in ("white", "black") and e.get("source_mode") == "Play", str(e))
        check("the event carries the decision record",
              e.get("selected_by") in ("gemini", "fallback") and isinstance(e.get("n_candidates"), int)
              and isinstance(e.get("cpl"), int) and isinstance(e.get("rank_in_pool"), int), str(e))
        check("the event carries no move or position",
              not any(k in e for k in ("move", "fen", "pgn", "explanation")), str(e))
        check("the event carries no explanation text",
              all(not isinstance(v, str) or len(v) < 40 or v == e["actor"] for v in e.values()), str(e))
        check("the event says whether Gemini or the fallback produced it",
              e.get("source") in ("gemini", "stockfish_fallback"), str(e))
finally:
    httpx.AsyncClient = _orig_client
    gemini_http.reset()
    app.stockfish_service.close()

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

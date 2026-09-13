"""
Integration test: does app.decide_ai_move() actually route through Gemini?

Uses the real Stockfish staged search, with only the Gemini HTTP call
faked. This is the wiring the user cares about - "is the LLM choosing the
move, or has it quietly dropped out of the loop".
"""
import asyncio
import sys

import httpx

import gemini_http

import app


FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
seen_prompt = {}


class FakeClient:
    def __init__(self, pick_index=1, status=200, reply_move=None):
        self.pick_index = pick_index
        self.status = status
        # A literal move to answer with, whatever the shortlist - to prove a
        # move outside it is refused.
        self.reply_move = reply_move

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    # gemini_http checks this before reusing its cached client. A real

    # httpx.AsyncClient has it; a stand-in has to say it is open.

    is_closed = False


    async def post(self, url, json=None, **kwargs):
        text = json["contents"][0]["parts"][0]["text"]
        seen_prompt["text"] = text
        # Pick whichever move is listed at pick_index in the shortlist the
        # app actually sent us - proves the app's candidates reached Gemini.
        moves = [ln.split(". ", 1)[1].split(" ")[0]
                 for ln in text.splitlines() if ln[:1].isdigit() and ". " in ln]
        seen_prompt["moves"] = moves
        chosen = self.reply_move or moves[min(self.pick_index, len(moves) - 1)]
        body = {"candidates": [{"content": {"parts": [
            {"text": f"{chosen}\nI like this one - it keeps the initiative."}]}}]}
        return httpx.Response(self.status, json=body, request=httpx.Request("POST", url))


def with_fake(pick_index=1, status=200, key="fake-key", reply_move=None, profile="master"):
    app.gemini_move_service.api_key = key
    orig = httpx.AsyncClient
    httpx.AsyncClient = FakeClient(pick_index, status, reply_move)
    gemini_http.reset()
    try:
        return asyncio.run(app.decide_ai_move(FEN, "white", profile=profile))
    finally:
        httpx.AsyncClient = orig
        gemini_http.reset()


results = []

# 1. Gemini in the loop -> source must be "gemini", not a fallback
move, expl, source, decision = with_fake(pick_index=1)
n_offered = len(seen_prompt["moves"])
ok = source == "gemini" and move in seen_prompt["moves"]
print(f"{'PASS' if ok else 'FAIL'}  decide_ai_move routes through Gemini (source={source!r}, move={move})")
results.append(ok)

# 2. The prompt Gemini received really was the app's Stockfish shortlist
ok = len(seen_prompt["moves"]) >= 2 and FEN in seen_prompt["text"]
print(f"{'PASS' if ok else 'FAIL'}  real Stockfish shortlist reached Gemini: {seen_prompt['moves']}")
results.append(ok)

# 3. Gemini's own words come back as the explanation shown to the player
ok = "initiative" in expl.lower()
print(f"{'PASS' if ok else 'FAIL'}  Gemini's explanation is surfaced: {expl[:60]!r}")
results.append(ok)

# 4. Gemini's pick - not Stockfish's ranking - decides the move played.
#    Asserted against the shortlist actually sent on each call rather than
#    across calls: the engine process is reused and keeps its hash between
#    searches, so near-equal moves can legitimately swap order run to run.
move_a, _, source_a, _ = with_fake(pick_index=1)
list_a = list(seen_prompt["moves"])
move_b, _, source_b, _ = with_fake(pick_index=0)
list_b = list(seen_prompt["moves"])
ok = (source_a == source_b == "gemini"
      and move_a == list_a[1]          # took Gemini's 2nd-choice pick
      and move_b == list_b[0]
      and list_a[1] != list_a[0])      # and that differed from the engine's top
print(f"{'PASS' if ok else 'FAIL'}  Gemini's pick decides the move "
      f"(chose {move_a} over engine-top {list_a[0]}; then {move_b})")
results.append(ok)

# 5. Gemini down -> clean Stockfish fallback, still a legal move
move3, expl3, source3, d3 = with_fake(status=500)
ok = source3 == "stockfish_fallback" and move3 is not None
print(f"{'PASS' if ok else 'FAIL'}  falls back cleanly when Gemini is down (source={source3!r}, move={move3})")
results.append(ok)

# 6. No key -> the honest message, not a misleading "Langflow unavailable"
move4, expl4, source4, d4 = with_fake(key="")
ok = source4 == "stockfish_fallback" and "GEMINI_API_KEY" in expl4 or "no Gemini API key" in expl4
print(f"{'PASS' if ok else 'FAIL'}  no key gives an accurate reason: {expl4[:70]!r}")
results.append(ok)


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if not ok and detail else ""))
    results.append(ok)

# 7. The decision record: who chose, at what cost, from how many
check("decision carries the profile", decision["profile_id"] == "master" and decision["approx_elo"] == 2400, str(decision))
check("decision names the chooser", decision["selected_by"] == "gemini" and decision["fallback_reason"] is None
      and decision["chooser"] == "gemini")
check("decision cpl is a number", isinstance(decision["cpl"], int) and decision["cpl"] >= 0, str(decision["cpl"]))
check("decision counts the candidates Gemini saw", decision["n_candidates"] == n_offered, f"{decision['n_candidates']} vs {n_offered}")
check("decision carries depths and timings", decision["rank_depth"] > 0 and decision["search_depth"] > 0
      and decision["total_ms"] >= decision["engine_ms"] >= 0)
check("decision has no position text", not any(k in decision for k in ("fen", "pgn", "explanation")))
check("Gemini down -> fallback reason", d3["selected_by"] == "fallback" and d3["fallback_reason"] == "gemini_failed", str(d3["fallback_reason"]))
check("no key -> fallback reason", d4["selected_by"] == "fallback" and d4["fallback_reason"] == "no_llm")

# 8. A move outside the shortlist is refused, whatever Gemini says
move5, expl5, source5, d5 = with_fake(reply_move="a2a3")
check("a move outside the pool is refused", source5 == "stockfish_fallback" and move5 != "a2a3"
      and d5["fallback_reason"] == "gemini_invalid_move", f"{source5} {move5} {d5['fallback_reason']}")
check("the fallback is a shortlisted move", move5 in seen_prompt["moves"])

# 9. The profile reaches the prompt, in words a player would recognise
with_fake(pick_index=0, profile="beginner")
check("beginner block in the prompt", "OPPONENT PROFILE" in seen_prompt["text"]
      and "Level: Beginner, roughly 400 strength" in seen_prompt["text"])
check("the old 1-20 wording is gone", "out of 20" not in seen_prompt["text"])
check("the list is still the only allowed moves", "ONLY moves you may choose from" in seen_prompt["text"])

# 10. Master's pool is engine-strength; beginner's is not (the pool, not Gemini)
strong = [with_fake(pick_index=0, profile="master")[3]["cpl"] for _ in range(3)]
check("master's fallback-top move costs nothing", max(strong) <= 12, str(strong))

print()
print(f"{sum(results)}/{len(results)} passed")
app.stockfish_service.close()
sys.exit(0 if all(results) else 1)

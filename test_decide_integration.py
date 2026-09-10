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
    def __init__(self, pick_index=1, status=200):
        self.pick_index = pick_index
        self.status = status

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
        chosen = moves[min(self.pick_index, len(moves) - 1)]
        body = {"candidates": [{"content": {"parts": [
            {"text": f"{chosen}\nI like this one - it keeps the initiative."}]}}]}
        return httpx.Response(self.status, json=body, request=httpx.Request("POST", url))


def with_fake(pick_index=1, status=200, key="fake-key"):
    app.gemini_move_service.api_key = key
    orig = httpx.AsyncClient
    httpx.AsyncClient = FakeClient(pick_index, status)
    gemini_http.reset()
    try:
        return asyncio.run(app.decide_ai_move(FEN, "white"))
    finally:
        httpx.AsyncClient = orig
        gemini_http.reset()


results = []

# 1. Gemini in the loop -> source must be "gemini", not a fallback
move, expl, source = with_fake(pick_index=1)
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
move_a, _, source_a = with_fake(pick_index=1)
list_a = list(seen_prompt["moves"])
move_b, _, source_b = with_fake(pick_index=0)
list_b = list(seen_prompt["moves"])
ok = (source_a == source_b == "gemini"
      and move_a == list_a[1]          # took Gemini's 2nd-choice pick
      and move_b == list_b[0]
      and list_a[1] != list_a[0])      # and that differed from the engine's top
print(f"{'PASS' if ok else 'FAIL'}  Gemini's pick decides the move "
      f"(chose {move_a} over engine-top {list_a[0]}; then {move_b})")
results.append(ok)

# 5. Gemini down -> clean Stockfish fallback, still a legal move
move3, expl3, source3 = with_fake(status=500)
ok = source3 == "stockfish_fallback" and move3 is not None
print(f"{'PASS' if ok else 'FAIL'}  falls back cleanly when Gemini is down (source={source3!r}, move={move3})")
results.append(ok)

# 6. No key -> the honest message, not a misleading "Langflow unavailable"
move4, expl4, source4 = with_fake(key="")
ok = source4 == "stockfish_fallback" and "GEMINI_API_KEY" in expl4 or "no Gemini API key" in expl4
print(f"{'PASS' if ok else 'FAIL'}  no key gives an accurate reason: {expl4[:70]!r}")
results.append(ok)

print()
print(f"{sum(results)}/{len(results)} passed")
app.stockfish_service.close()
sys.exit(0 if all(results) else 1)

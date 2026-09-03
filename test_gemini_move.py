"""
Tests gemini_move_service against a faked Gemini API - no key needed.

Covers the paths that matter: a good pick, a hallucinated move, prose
around the answer, a dead model falling through to the next, a content
block, and no key at all.
"""
import asyncio
import json
import sys

import httpx

from gemini_move_service import GeminiMoveService

CANDIDATES = [
    {"move": "f3g5", "score": 34, "mate_in": None},
    {"move": "d2d3", "score": 22, "mate_in": None},
    {"move": "d2d4", "score": 10, "mate_in": None},
]
FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"


def reply(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class FakeClient:
    """Stands in for httpx.AsyncClient; replays a scripted list of responses."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.calls.append(url)
        status, body = self.script.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def run(name, script, expect_move, expect_success, key="fake-key", models=None, check=None):
    svc = GeminiMoveService(api_key=key, models=models or ["model-a", "model-b", "model-c"])
    fake = FakeClient(list(script))
    orig = httpx.AsyncClient
    httpx.AsyncClient = fake
    try:
        move, expl, ok = asyncio.run(svc.choose_move_from_candidates(FEN, CANDIDATES))
    finally:
        httpx.AsyncClient = orig

    passed = (move == expect_move) and (ok == expect_success)
    if check and passed:
        passed = check(move, expl, ok, fake)
    print(f"{'PASS' if passed else 'FAIL'}  {name}")
    if not passed:
        print(f"      got move={move!r} success={ok} expl={expl!r} calls={len(fake.calls)}")
    return passed


results = []

# 1. Clean answer in the requested format
results.append(run(
    "picks the move it was given",
    [(200, reply("f3g5\nThe knight jumps to g5 to hit f7 and force a concession."))],
    "f3g5", True,
    check=lambda m, e, ok, f: "knight" in e.lower() and "f3g5" not in e,
))

# 2. Model wraps the answer in prose instead of following the format
results.append(run(
    "tolerates prose around the move",
    [(200, reply("I think I'll play d2d4 here, grabbing the centre while I can."))],
    "d2d4", True,
))

# 3. Model invents a move that isn't on the shortlist -> must be rejected
results.append(run(
    "rejects a move outside the shortlist",
    [(200, reply("e1g1\nCastling looks safest.")),
     (200, reply("e1g1\nCastling looks safest.")),
     (200, reply("e1g1\nCastling looks safest."))],
    None, False,
    check=lambda m, e, ok, f: len(f.calls) == 3,  # tried every model before giving up
))

# 4. First model dead (404), second overloaded (503), third answers
results.append(run(
    "falls through dead models to a live one",
    [(404, {"error": "not found"}),
     (503, {"error": "overloaded"}),
     (200, reply("d2d3\nSolid and quiet - I keep the position closed."))],
    "d2d3", True,
    check=lambda m, e, ok, f: len(f.calls) == 3,
))

# 5. Content policy block -> stop immediately, don't burn the chain
results.append(run(
    "stops on a content block instead of retrying",
    [(200, {"promptFeedback": {"blockReason": "SAFETY"}})],
    None, False,
    check=lambda m, e, ok, f: len(f.calls) == 1 and "SAFETY" in e,
))

# 6. All models fail -> caller gets a clean failure to fall back on
results.append(run(
    "reports failure when every model is down",
    [(500, {}), (500, {}), (500, {})],
    None, False,
))

# 7. No API key at all -> fails immediately, no HTTP attempted
results.append(run(
    "fails fast with no API key",
    [],
    None, False, key="",
    check=lambda m, e, ok, f: len(f.calls) == 0 and "GEMINI_API_KEY" in e,
))

# 8. available flag drives app.py's selector choice
svc_no_key = GeminiMoveService(api_key="", models=["m"])
svc_key = GeminiMoveService(api_key="k", models=["m"])
ok8 = (svc_no_key.available is False) and (svc_key.available is True)
print(f"{'PASS' if ok8 else 'FAIL'}  available flag reflects key presence")
results.append(ok8)

# 9. The prompt actually contains the shortlist and forbids inventing moves
prompt = GeminiMoveService(api_key="k")._build_prompt(FEN, CANDIDATES, {"difficulty": 20})
ok9 = all(c["move"] in prompt for c in CANDIDATES) and "ONLY" in prompt and FEN in prompt
print(f"{'PASS' if ok9 else 'FAIL'}  prompt lists every candidate and constrains the choice")
results.append(ok9)

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

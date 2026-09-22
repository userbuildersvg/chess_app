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

import gemini_http
from opponent_profiles import get_profile
from gemini_move_service import GeminiMoveService

CANDIDATES = [
    {"move": "f3g5", "score": 34, "mate_in": None},
    {"move": "d2d3", "score": 22, "mate_in": None},
    {"move": "d2d4", "score": 10, "mate_in": None},
]
FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"


def reply(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class FakeGemini:
    """
    Stands in for gemini_http.post; replays a scripted response per model.

    Keyed by MODEL rather than by call order, because the service hedges: if
    the lead model has not answered within HEDGE_DELAY the next one is started
    alongside it, so "the second request made" is not reliably "model-b". A
    script keyed by model says what it means whatever the timing does.

    A scripted entry may be a `delay` in seconds instead of a response, which
    is how a hanging model is simulated without waiting out a real timeout.
    """

    def __init__(self, script):
        self.script = dict(script)
        self.calls = []

    async def __call__(self, url, payload, api_key, timeout):
        model = url.rsplit("/models/", 1)[-1].split(":")[0]
        self.calls.append(model)
        entry = self.script.get(model, (500, {"error": "unscripted model"}))
        if isinstance(entry, dict) and "delay" in entry:
            # A model that hangs. `timeout` is honoured so the service sees the
            # same exception shape httpx would raise.
            await asyncio.sleep(min(entry["delay"], timeout))
            if entry["delay"] >= timeout:
                raise httpx.ReadTimeout("simulated hang", request=None)
            entry = entry["then"]
        status, body = entry
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def run(name, script, expect_move, expect_success, key="fake-key", models=None, check=None):
    svc = GeminiMoveService(api_key=key, models=models or ["model-a", "model-b", "model-c"])
    fake = FakeGemini(script)
    orig = gemini_http.post
    gemini_http.post = fake
    # Per-model cooldowns outlive one case (they are keyed by model name for
    # the whole process), so each case starts from a healthy provider.
    gemini_http.reset_cooldowns()
    try:
        move, expl, ok = asyncio.run(svc.choose_move_from_candidates(FEN, CANDIDATES))
    finally:
        gemini_http.post = orig

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
    {"model-a": (200, reply("f3g5\nThe knight jumps to g5 to hit f7 and force a concession."))},
    "f3g5", True,
    check=lambda m, e, ok, f: "knight" in e.lower() and "f3g5" not in e,
))

# 2. Model wraps the answer in prose instead of following the format
results.append(run(
    "tolerates prose around the move",
    {"model-a": (200, reply("I think I'll play d2d4 here, grabbing the centre while I can."))},
    "d2d4", True,
))

# 3. Model invents a move that isn't on the shortlist -> must be rejected
results.append(run(
    "rejects a move outside the shortlist",
    {m: (200, reply("e1g1\nCastling looks safest.")) for m in ("model-a", "model-b", "model-c")},
    None, False,
    # Two models, then the caller's own engine move - not the whole chain.
    check=lambda m, e, ok, f: len(f.calls) == 2,
))

# 4. First model dead (404), second answers. The third is scripted to prove
#    it is never reached: one move is worth two provider calls, not six.
results.append(run(
    "falls through a dead model to a live one",
    {"model-a": (404, {"error": "not found"}),
     "model-b": (200, reply("d2d3\nSolid and quiet - I keep the position closed.")),
     "model-c": (200, reply("e2e4\nnever asked"))},
    "d2d3", True,
    check=lambda m, e, ok, f: len(f.calls) == 2,
))

# 5. Content policy block -> stop immediately, don't burn the chain
results.append(run(
    "stops on a content block instead of retrying",
    {"model-a": (200, {"promptFeedback": {"blockReason": "SAFETY"}})},
    None, False,
    check=lambda m, e, ok, f: len(f.calls) == 1 and "SAFETY" in e,
))

# 6. All models fail -> caller gets a clean failure to fall back on
results.append(run(
    "reports failure when every model is down",
    {m: (500, {}) for m in ("model-a", "model-b", "model-c")},
    None, False,
))

# 7. No API key at all -> fails immediately, no HTTP attempted
results.append(run(
    "fails fast with no API key",
    {},
    None, False, key="",
    check=lambda m, e, ok, f: len(f.calls) == 0 and "GEMINI_API_KEY" in e,
))

# --- The hedge -------------------------------------------------------------
#
# An AI move's Gemini time was measured at 2.5s to 19s on the dev stack, and
# the 19s was two models HANGING for the full 6s timeout each before a third
# answered normally - not a slow model. So a model that has not answered within
# HEDGE_DELAY no longer blocks the ones behind it.
#
# What must NOT change: nothing is cut. The full payload still goes to every
# model tried, every reply is parsed in full, and a merely-slow model still
# wins if it answers first.

# 9. A hanging lead model does not cost the whole timeout.
results.append(run(
    "a hanging model is overtaken rather than waited out",
    {"model-a": {"delay": 30, "then": None},
     "model-b": (200, reply("d2d3\nQuiet and solid."))},
    "d2d3", True,
    check=lambda m, e, ok, f: "model-b" in f.calls,
))

# 10. The lead model still wins when it simply answers.
results.append(run(
    "a model that answers promptly is not hedged away from",
    {"model-a": (200, reply("f3g5\nHitting f7.")),
     "model-b": (200, reply("d2d4\nCentre.")),
     "model-c": (200, reply("d2d3\nQuiet."))},
    "f3g5", True,
    check=lambda m, e, ok, f: f.calls[0] == "model-a",
))

# 8. available flag drives app.py's selector choice
svc_no_key = GeminiMoveService(api_key="", models=["m"])
svc_key = GeminiMoveService(api_key="k", models=["m"])
ok8 = (svc_no_key.available is False) and (svc_key.available is True)
print(f"{'PASS' if ok8 else 'FAIL'}  available flag reflects key presence")
results.append(ok8)

# 9. The prompt actually contains the shortlist and forbids inventing moves
prompt = GeminiMoveService(api_key="k")._build_prompt(FEN, CANDIDATES, {"profile": get_profile("club")})
ok9 = (all(c["move"] in prompt for c in CANDIDATES) and "ONLY" in prompt and FEN in prompt
       and "Level: Club" in prompt and "1500" in prompt)
print(f"{'PASS' if ok9 else 'FAIL'}  prompt lists every candidate and constrains the choice")
results.append(ok9)

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

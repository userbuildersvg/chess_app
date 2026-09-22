"""
Tests the narration piece of Sandbox Learner Mode against a faked Gemini API
- no key needed, no network, no quota spent.

Two halves:

  1. gemini_narration_service itself - prompt contents, the fallback chain,
     and that it never raises into a background task's void.
  2. The wiring in sandbox_api - that narration really is fired *in
     parallel* (the move endpoint does not wait for it), that it lands on
     the right node, and that a result arriving after a rewind is discarded
     rather than attached to the wrong move.

Run:
    DISABLE_LANGFLOW=true /tmp/chessapp/bin/python test_sandbox_narration.py
"""

import asyncio
import time

import httpx

import gemini_http

import gemini_narration_service as narration_module
from gemini_narration_service import GeminiNarrationService

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


def reply(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class FakeClient:
    """Stands in for httpx.AsyncClient; replays a scripted list of responses."""

    def __init__(self, script, delay=0.0):
        self.script = script
        self.delay = delay
        self.calls = []

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
        self.calls.append({"url": url, "json": json})
        if self.delay:
            await asyncio.sleep(self.delay)
        status, body = self.script.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def with_fake(coro_fn, script, delay=0.0):
    """Run `coro_fn(fake)` with httpx.AsyncClient swapped for a fake."""
    fake = FakeClient(list(script), delay=delay)
    orig = httpx.AsyncClient
    httpx.AsyncClient = fake
    gemini_http.reset()
    # Cooldowns are per MODEL NAME and live for the process, which is right in
    # production and wrong between two cases that reuse "m-a": the 429s one
    # case scripts would park the model the next case wants to call. Cleared
    # per case so each one starts from a healthy provider.
    gemini_http.reset_cooldowns()
    try:
        return asyncio.run(coro_fn(fake)), fake
    finally:
        httpx.AsyncClient = orig
        gemini_http.reset()


CONTEXT = {
    "move": "g1f3",
    "san": "Nf3",
    "mover": "white",
    "source": "ai",
    "fen_before": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "fen_after": "rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 1 1",
    "line_san": [],
    "explanation": "Developing toward the centre.",
    "title": "Sicilian demo",
    "alternatives": [
        {"move": "e2e4", "san": "e4", "score": 40},
        {"move": "d2d4", "san": "d4", "score": 35},
        {"move": "g1f3", "san": "Nf3", "score": 30},
    ],
}


# --- the prompt says the right things -----------------------------------

svc = GeminiNarrationService(api_key="fake", models=["m-a", "m-b", "m-c"])
prompt = svc.build_prompt(CONTEXT)

check("prompt states the move that was played", "Nf3" in prompt)
check("prompt gives the position before the move", CONTEXT["fen_before"] in prompt)
check("prompt carries the scenario title", "Sicilian demo" in prompt)
check("prompt passes the player's own stated reason",
      "Developing toward the centre." in prompt)
check("prompt offers the engine's alternatives for contrast",
      "e4 (eval 40)" in prompt and "d4 (eval 35)" in prompt, prompt[-400:])
check("prompt does not offer the played move back as an alternative",
      "Nf3 (eval 30)" not in prompt)
check("prompt constrains length so narration stays speakable",
      "Two or three sentences" in prompt)
check("prompt forbids markdown, which would render as literal asterisks",
      "no markdown" in prompt.lower())

student = svc.build_prompt({**CONTEXT, "source": "human"})
check("a student's own move is framed as theirs to react to",
      "taken over the board" in student)

first = svc.build_prompt({**CONTEXT, "line_san": []})
check("the first move of a demo is labelled as such", "(this is the first move)" in first)
later = svc.build_prompt({**CONTEXT, "line_san": ["e4", "c5"]})
check("a later move carries the line so far", "Moves so far: e4 c5" in later)


# --- the service talks to Gemini and falls through dead models ----------

(ok, text), fake = with_fake(lambda f: svc.narrate(dict(CONTEXT)),
                             [(200, reply("  Knight to f3 eyes e5 and d4.  "))])
check("a good response is returned, trimmed",
      ok and text == "Knight to f3 eyes e5 and d4.", (ok, text))
check("exactly one model was called on success", len(fake.calls) == 1)

# One request may try two models and no more (gemini_http.MAX_PROVIDER_ATTEMPTS):
# a dead lead still falls through to a live model, and a third would be quota
# spent on a person who is already waiting. The third entry is scripted anyway,
# to prove it is never reached.
svc2 = GeminiNarrationService(api_key="fake", models=["m-a", "m-b", "m-c"])
(ok, text), fake = with_fake(lambda f: svc2.narrate(dict(CONTEXT)),
                             [(503, {"error": "overloaded"}),
                              (200, reply("It grips the centre.")),
                              (200, reply("never asked"))])
check("falls through a dead model to a live one", ok and text == "It grips the centre.")
check("one narration costs at most two provider calls", len(fake.calls) == 2, len(fake.calls))

svc3 = GeminiNarrationService(api_key="fake", models=["m-a", "m-b"])
(ok, text), fake = with_fake(lambda f: svc3.narrate(dict(CONTEXT)),
                             [(429, {}), (429, {})])
check("reports failure when every model is down", ok is False and "unavailable" in text)

svc4 = GeminiNarrationService(api_key="fake", models=["m-a", "m-b"])
(ok, text), fake = with_fake(
    lambda f: svc4.narrate(dict(CONTEXT)),
    [(200, {"promptFeedback": {"blockReason": "SAFETY"}}), (200, reply("unused"))])
check("a content block stops immediately instead of retrying",
      ok is False and len(fake.calls) == 1, len(fake.calls))

nokey = GeminiNarrationService(api_key="", models=["m-a"])
check("no API key means unavailable, not a crash", nokey.available is False)
ok, text = asyncio.run(nokey.narrate(dict(CONTEXT)))
check("narrating without a key fails fast with a clear reason",
      ok is False and "GEMINI_API_KEY" in text)


# --- the last working model is preferred next time ----------------------

svc5 = GeminiNarrationService(api_key="fake", models=["m-a", "m-b", "m-c"])
with_fake(lambda f: svc5.narrate(dict(CONTEXT)), [(500, {}), (200, reply("x"))])
check("remembers which model worked", svc5._last_good_model == "m-b")
(ok, _), fake = with_fake(lambda f: svc5.narrate(dict(CONTEXT)), [(200, reply("y"))])
check("tries the remembered model first next time",
      fake.calls[0]["url"].split("/models/")[1].startswith("m-b"), fake.calls[0]["url"])


# --- the chain is not led by moves' model; known-good leads may be shared ---

from gemini_chat_service import GEMINI_CHAT_MODELS
from gemini_move_service import GeminiMoveService

move_lead = GeminiMoveService(api_key="x").models[0]
chat_lead = GEMINI_CHAT_MODELS[0]
narr_lead = narration_module.GEMINI_NARRATION_MODELS[0]
check("narration does not compete with move selection's lead model",
      narr_lead != move_lead,
      f"narration={narr_lead} moves={move_lead} chat={chat_lead}")
check("the model that hangs is last, not first",
      narration_module.GEMINI_NARRATION_MODELS[-1] == "gemini-3.6-flash")


# --- concurrency is capped so a fast demo can't flood the shared key ----

async def flood():
    svc = GeminiNarrationService(api_key="fake", models=["m-a"])
    svc._semaphore = asyncio.Semaphore(2)
    live = {"now": 0, "peak": 0}

    class CountingClient(FakeClient):
        # gemini_http checks this before reusing its cached client. A real
        # httpx.AsyncClient has it; a stand-in has to say it is open.
        is_closed = False

        async def post(self, url, json=None, **kwargs):
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            await asyncio.sleep(0.05)
            live["now"] -= 1
            return httpx.Response(200, json=reply("ok"), request=httpx.Request("POST", url))

    fake = CountingClient([])
    orig = httpx.AsyncClient
    httpx.AsyncClient = fake
    gemini_http.reset()
    try:
        await asyncio.gather(*(svc.narrate(dict(CONTEXT)) for _ in range(6)))
    finally:
        httpx.AsyncClient = orig
        gemini_http.reset()
    return live["peak"]

peak = asyncio.run(flood())
check("never more than the configured number of calls in flight",
      peak <= 2, f"peak={peak}")


# --- wiring: narration is fired in parallel, not awaited -----------------

import os as _os

# The closed beta gate is fail-closed by default (`beta_service.beta_required`),
# so with it left alone every request in this file would be answered 403 by
# `beta_gate.py` before reaching the route under test. Switched off here rather
# than worked around, because this suite is testing what the routes do and not
# who may reach them - that is `test_beta_access.py`, which asserts among other
# things that this default is ON when nobody says otherwise.
_os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")

import app  # noqa: E402
import sandbox_api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sandbox_state import NARRATION_PENDING, NARRATION_READY  # noqa: E402


async def fake_decide(fen, color, *, profile=None, last_move=None, use_learning=True):
    import chess
    return sorted(m.uci() for m in chess.Board(fen).legal_moves)[0], "Because.", "gemini", {}


sandbox_api.configure(fake_decide)

# NOTE: TestClient must be entered as a context manager here. Used bare, it
# builds a fresh blocking portal - and therefore a fresh event loop - for
# every single request, so a task detached by one request is destroyed the
# moment that request returns, and narration sits at "pending" forever. That
# is purely a test-harness artifact: uvicorn runs one long-lived loop, which
# is what makes the fire-and-attach design work in the real app.
NARRATION_DELAY = 0.6
slow = FakeClient([(200, reply("A calm developing move."))] * 50, delay=NARRATION_DELAY)
orig_client = httpx.AsyncClient
httpx.AsyncClient = slow
gemini_http.reset()
narration_module.gemini_narration_service.api_key = "fake"
narration_module.gemini_narration_service.models = ["m-a"]
narration_module.gemini_narration_service._semaphore = None

try:
  with TestClient(app.app) as client:
    sid = client.post("/api/sandbox/session", json={"title": "Narration check"}).json()["session_id"]

    t0 = time.time()
    r = client.post(f"/api/sandbox/session/{sid}/ai-move").json()
    elapsed = time.time() - t0
    node_id = r["played"]["id"]

    check("the move endpoint returned without waiting for narration",
          elapsed < NARRATION_DELAY, f"{elapsed:.2f}s vs {NARRATION_DELAY}s narration")
    check("the move is reported as narration-pending",
          r["played"]["narration_status"] == NARRATION_PENDING, r["played"]["narration_status"])
    check("no narration text is present yet", r["played"]["narration"] is None)

    # Give the detached task time to land.
    deadline = time.time() + 10
    status = None
    while time.time() < deadline:
        got = client.get(f"/api/sandbox/session/{sid}/narration/{node_id}").json()
        status = got["status"]
        if status != NARRATION_PENDING:
            break
        time.sleep(0.1)

    check("narration arrives and is marked ready", status == NARRATION_READY, status)
    check("narration text lands on the node it was generated for",
          got["narration"] == "A calm developing move.", got)

    whole = client.get(f"/api/sandbox/session/{sid}/narration").json()
    check("the line endpoint lists narration for played moves only",
          len(whole["line"]) == 1 and whole["line"][0]["node_id"] == node_id, whole)

    # A student's own move is not narrated unless asked for.
    client.post(f"/api/sandbox/session/{sid}/back")
    quiet = client.post(f"/api/sandbox/session/{sid}/move", json={"move": "d2d4"}).json()
    check("a student's move is not narrated by default",
          quiet["node"]["narration_status"] == "none", quiet["node"]["narration_status"])

    client.post(f"/api/sandbox/session/{sid}/back")
    loud = client.post(f"/api/sandbox/session/{sid}/move",
                       json={"move": "e2e4", "narrate": True}).json()
    check("a student's move is narrated when asked for",
          loud["node"]["narration_status"] == NARRATION_PENDING,
          loud["node"]["narration_status"])

    # Narration disabled on the session must not fire at all.
    off = client.post("/api/sandbox/session", json={"narration_enabled": False}).json()["session_id"]
    r_off = client.post(f"/api/sandbox/session/{off}/ai-move").json()
    check("narration_enabled=false suppresses narration entirely",
          r_off["played"]["narration_status"] == "none", r_off["played"]["narration_status"])

    # A result arriving after the node is gone must be dropped, not
    # attached to whatever is current now. This is the property that lets
    # narration run in parallel safely.
    gone = client.post("/api/sandbox/session", json={}).json()["session_id"]
    g = client.post(f"/api/sandbox/session/{gone}/ai-move").json()
    stale_node = g["played"]["id"]
    client.post(f"/api/sandbox/session/{gone}/reset", json={})
    time.sleep(NARRATION_DELAY + 0.5)
    after_reset = client.get(f"/api/sandbox/session/{gone}").json()
    check("narration for a discarded branch does not resurface on the new tree",
          stale_node not in after_reset["tree"]["nodes"]
          and all(n["narration"] is None for n in after_reset["tree"]["nodes"].values()),
          after_reset["tree"]["nodes"])
finally:
    httpx.AsyncClient = orig_client
    gemini_http.reset()
    app.stockfish_service.close()

print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

"""
The per-model cooldown, and the ceiling on how many models one action costs.

Why this suite exists
---------------------

One user action could cost six or seven provider calls. The chains are
ordered lists, every model in them is tried on any failure, and a provider
having a bad minute - 429 RESOURCE_EXHAUSTED on the popular models, 503
under load - meant the whole list was walked at one request each while
somebody waited. Two things fix it and both are invisible to a typecheck:

  1. a model that has just refused is not asked again for a while, and
  2. one logical request tries at most two models before the deterministic
     fallback every service already has.

Both live in `gemini_http`, so this suite tests them there - and then checks
that each of the five service chains actually reads them, because a cap that
one service forgets to apply is the one that spends the quota.

No network, no key, no database: everything here is pure bookkeeping.

    python test_gemini_cooldown.py
"""
import sys
import time

import gemini_http

passed = 0
failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"PASS  {label}")
    else:
        failed += 1
        print(f"FAIL  {label}{f' - {detail}' if detail else ''}")


class FakeRequest:
    def __init__(self, url):
        self.url = url


class FakeResponse:
    """Just enough of httpx.Response for `note()`."""

    def __init__(self, model, status, headers=None, body=None):
        self.status_code = status
        self.headers = headers or {}
        self.request = FakeRequest(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        )
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

# --- the model name comes out of the URL -----------------------------------

check("the model is read from a generateContent URL",
      gemini_http.model_of(URL.format("gemini-3.5-flash")) == "gemini-3.5-flash")
check("...and from one carrying a query string",
      gemini_http.model_of(URL.format("gemini-3.5-flash") + "?alt=sse") == "gemini-3.5-flash")
check("an unfamiliar URL yields no model rather than a wrong one",
      gemini_http.model_of("https://example.test/v1/chat") == "")

# --- 429 parks that model, and only that model ------------------------------

gemini_http.reset_cooldowns()
gemini_http.note(FakeResponse("m-busy", 429), feature="chat")
check("a 429 cools the model that sent it", gemini_http.cooling_for("m-busy") > 0)
check("...for minutes, not seconds, when no delay is named",
      gemini_http.cooling_for("m-busy") > 120, gemini_http.cooling_for("m-busy"))
check("...and leaves every other model eligible",
      gemini_http.cooling_for("m-other") == 0)

# --- the provider's own retry figure is respected ---------------------------

gemini_http.reset_cooldowns()
gemini_http.note(FakeResponse("m-header", 429, headers={"Retry-After": "12"}))
left = gemini_http.cooling_for("m-header")
check("Retry-After is respected over the default", 10 < left <= 12, left)

gemini_http.reset_cooldowns()
gemini_http.note(FakeResponse(
    "m-body", 429,
    body={"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                 "retryDelay": "41s"}]}},
))
left = gemini_http.cooling_for("m-body")
check("Google's retryDelay in the body is respected", 38 < left <= 41, left)

gemini_http.reset_cooldowns()
gemini_http.note(FakeResponse("m-greedy", 429, headers={"Retry-After": "99999"}))
check("a huge retry figure is capped rather than taking the model out for good",
      gemini_http.cooling_for("m-greedy") <= 900, gemini_http.cooling_for("m-greedy"))

# --- 503 is a queue, not a wall ---------------------------------------------

gemini_http.reset_cooldowns()
gemini_http.note(FakeResponse("m-loaded", 503))
left = gemini_http.cooling_for("m-loaded")
check("a 503 cools the model briefly", 0 < left <= 90, left)
check("...much more briefly than a 429", left < 300, left)

# --- success clears it -------------------------------------------------------

gemini_http.reset_cooldowns()
gemini_http.note(FakeResponse("m-recovered", 429))
gemini_http.note(FakeResponse("m-recovered", 200))
check("a model that answers is eligible again at once",
      gemini_http.cooling_for("m-recovered") == 0)

# --- expiry ------------------------------------------------------------------

gemini_http.reset_cooldowns()
gemini_http.cool("m-brief", 0.05, reason="test")
check("a cooled model is skipped while it is cooling", gemini_http.cooling_for("m-brief") > 0)
time.sleep(0.08)
check("...and comes back by itself when the time is up",
      gemini_http.cooling_for("m-brief") == 0)

# --- the ceiling -------------------------------------------------------------

chain = ["a", "b", "c", "d", "e", "f"]
gemini_http.reset_cooldowns()
check("one action tries two models, not six", gemini_http.eligible(chain, 2) == ["a", "b"])

gemini_http.cool("a", 60, reason="test")
check("a cooled lead is skipped and does NOT use up an attempt",
      gemini_http.eligible(chain, 2) == ["b", "c"])

gemini_http.reset_cooldowns()
for m in chain:
    gemini_http.cool(m, 60, reason="test")
check("every model cooling means no provider call at all",
      gemini_http.eligible(chain, 2) == [])

gemini_http.reset_cooldowns()
check("an empty chain stays empty", gemini_http.eligible([], 2) == [])

# --- the services actually use it --------------------------------------------
#
# The cap is only real if every chain reads it. A service that forgets is the
# one that spends the quota, and nothing else in the suite would notice.

gemini_http.reset_cooldowns()

from gemini_move_service import GeminiMoveService
from gemini_chat_service import GeminiChatService
from gemini_narration_service import GeminiNarrationService
from scenario_service import ScenarioService
from diagnosis_service import DiagnosisService

LONG = ["m1", "m2", "m3", "m4", "m5", "m6"]
services = [
    ("move selection", GeminiMoveService(api_key="x", models=list(LONG))),
    ("chat", GeminiChatService(api_key="x", models=list(LONG))),
    ("narration", GeminiNarrationService(api_key="x", models=list(LONG))),
    ("scenario", ScenarioService(api_key="x", models=list(LONG))),
    ("diagnosis", DiagnosisService(api_key="x", models=list(LONG))),
]

for name, service in services:
    order = service._model_order()
    check(f"{name} tries at most two models for one request", len(order) <= 2, order)

gemini_http.cool("m1", 120, reason="test")
for name, service in services:
    order = service._model_order()
    check(f"{name} skips a cooling model", "m1" not in order, order)
    check(f"{name} still has a fallback to try", len(order) == 2, order)

gemini_http.reset_cooldowns()
for m in LONG:
    gemini_http.cool(m, 120, reason="test")
for name, service in services:
    check(f"{name} stops calling the provider when the whole chain is cooling",
          service._model_order() == [], service._model_order())

# --- the deadlines exist and are sane ----------------------------------------

import gemini_move_service
import gemini_chat_service
import gemini_narration_service
import scenario_service
import diagnosis_service

for name, module, ceiling in [
    ("move selection", gemini_move_service, 10),
    ("chat", gemini_chat_service, 20),
    ("narration", gemini_narration_service, 20),
    ("scenario", scenario_service, 20),
    ("diagnosis", diagnosis_service, 15),
]:
    total = getattr(module, "TOTAL_TIMEOUT", None)
    check(f"{name} has a ceiling on the whole request", isinstance(total, float), total)
    check(f"...and it is at most {ceiling}s", total is not None and total <= ceiling, total)


# ===========================================================================
# What a dead provider costs
# ===========================================================================
#
# The ceilings above are numbers in a module; these are the numbers the user
# actually waits. The provider is replaced by one that always says 503 (and
# takes a moment about it, like a real overloaded endpoint), and each service
# is asked for something. What is measured is how long the service spends
# before handing back the deterministic answer it always had, and how many
# provider calls it made getting there.

import asyncio

CALLS = []


class DeadResponse:
    """A provider that is up, and refusing."""

    def __init__(self, model):
        self.status_code = 503
        self.headers = {}
        self.request = FakeRequest(URL.format(model))
        self.text = '{"error": "overloaded"}'

    def json(self):
        return {"error": {"message": "overloaded"}}


async def dead_post(url, payload, api_key, timeout, *, feature=""):
    CALLS.append(gemini_http.model_of(url))
    await asyncio.sleep(0.05)  # a real endpoint is never instant
    response = DeadResponse(gemini_http.model_of(url))
    gemini_http.note(response, feature=feature)
    return response


def timed(coro_fn):
    gemini_http.reset_cooldowns()
    CALLS.clear()
    original = gemini_http.post
    gemini_http.post = dead_post
    started = time.monotonic()
    try:
        result = asyncio.run(coro_fn())
    finally:
        gemini_http.post = original
    return result, time.monotonic() - started, list(CALLS)


CHAIN = ["m1", "m2", "m3", "m4", "m5", "m6"]

# --- move selection ---------------------------------------------------------
move_service = GeminiMoveService(api_key="x", models=list(CHAIN))
(move, _explanation, ok), elapsed, calls = timed(
    lambda: move_service.choose_move_from_candidates(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        [{"move": "e2e4", "san": "e4"}, {"move": "d2d4", "san": "d4"}],
    )
)
check("a dead provider fails move selection rather than hanging", ok is False, ok)
check("...having tried two models, not six", len(calls) <= 2, calls)
check("...well inside the move ceiling", elapsed < 10, f"{elapsed:.1f}s")
check("...and no move is invented when the model never answered", move is None, move)

# --- chat -------------------------------------------------------------------
chat_service = GeminiChatService(api_key="x", models=list(CHAIN))
(ok, reply), elapsed, calls = timed(
    lambda: chat_service.send_message("what should I play?", [], {"fen": "8/8/8/8/8/8/8/K6k w - - 0 1"})
)
check("a dead provider fails chat rather than hanging", ok is False)
check("...having tried two models, not six", len(calls) <= 2, calls)
check("...well inside the chat ceiling", elapsed < 18, f"{elapsed:.1f}s")
check("...and says so calmly, without a stack trace or a model name",
      "could not reach the coach" in reply.lower() and "m1" not in reply, reply)

# --- diagnosis --------------------------------------------------------------
diagnosis = DiagnosisService(api_key="x", models=list(CHAIN))
EVIDENCE = {
    "san": "Nf3", "uci": "g1f3", "color": "white", "ply": 5, "phase": "opening",
    "fen_before": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "cpl": 120, "best_san": "e4", "best_move": "e2e4",
    "eval_before": {"score": 20, "mate_in": None},
    "eval_after": {"score": -100, "mate_in": None},
    "pv_san": ["e4", "e5"], "depth": 14, "quality": {"label": "mistake", "name": "Mistake"},
}
(ok, _result), elapsed, calls = timed(lambda: diagnosis.diagnose(EVIDENCE, "I wanted to develop"))
check("a dead provider fails diagnosis rather than hanging", ok is False)
check("...having tried two models, not six", len(calls) <= 2, calls)
check("...inside the 15s the correction card is allowed", elapsed < 15, f"{elapsed:.1f}s")

gemini_http.reset_cooldowns()
print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)

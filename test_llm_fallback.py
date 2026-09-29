"""
DeepSeek as the fallback provider, inside the same attempt budget.

What this proves, with a scripted transport and no network or real key:

  1. Gemini answers  -> DeepSeek is never called (1 provider call).
  2. Gemini 503/429/timeout -> DeepSeek is called once (2 calls, never more).
  3. Gemini fails, DeepSeek answers -> the user gets normal coaching output
     (chat, AI move from the shortlist, saved-lesson diagnosis).
  4. Both fail -> each service's deterministic fallback, with calm copy.
  5/6. DeepSeek without a key, or disabled -> skipped, Gemini-only as before.
  7. A timeout on either provider cools that provider/model.
  8. Every service's chain is capped at 2, DeepSeek last, even after it won.
  9. No key, prompt, provider body or reply text reaches the logs.

    python test_llm_fallback.py
"""
import asyncio
import logging
import os
import sys

import httpx

import gemini_http
from gemini_chat_service import COACH_UNAVAILABLE, GeminiChatService
from gemini_move_service import GeminiMoveService
from gemini_narration_service import GeminiNarrationService
from scenario_service import ScenarioService
from diagnosis_service import DiagnosisService, fallback_diagnosis

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


# --- everything logged anywhere is kept, to be searched at the end ----------

LOGGED = []


class Keep(logging.Handler):
    def emit(self, record):
        LOGGED.append(record.getMessage())


logging.getLogger().addHandler(Keep())
logging.getLogger().setLevel(logging.DEBUG)

GEMINI_KEY = "gemini-test-key-SECRET1"
DS_KEY = "ds-test-key-SECRET2"
PROMPT_MARKER = "PROMPT-MARKER-7731"
BODY_MARKER = "PROVIDER-BODY-5520"
SLOT = "deepseek/deepseek-flash"

ENV_KEYS = ["DEEPSEEK_ENABLED", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
            "DEEPSEEK_TIMEOUT_SECONDS", "LLM_PROVIDERS",
            "LLM_SIMULATE_GEMINI_FAILURE", "LLM_SIMULATE_DEEPSEEK_FAILURE"]


def env(**values):
    """Reset the provider env to a known state, then apply `values`."""
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update({"DEEPSEEK_ENABLED": "true", "DEEPSEEK_API_KEY": DS_KEY})
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    gemini_http.reset_cooldowns()


class Transport:
    """
    Stands in for the shared client. `gemini` / `deepseek` is "ok", an HTTP
    status, or "timeout". Every call is recorded with its URL and headers.
    """

    def __init__(self, gemini="ok", deepseek="ok", reply="REPLY-MARKER fine"):
        self.gemini, self.deepseek, self.reply = gemini, deepseek, reply
        self.calls = []

    async def post(self, url, json=None, headers=None, timeout=None):
        provider = "deepseek" if "deepseek" in url else "gemini"
        self.calls.append({"provider": provider, "url": url, "headers": headers or {}, "json": json})
        behaviour = getattr(self, provider)
        request = httpx.Request("POST", url)
        await asyncio.sleep(0.01)
        if behaviour == "timeout":
            raise httpx.ReadTimeout("", request=request)
        if isinstance(behaviour, int):
            return httpx.Response(behaviour, json={"error": {"message": BODY_MARKER}}, request=request)
        if provider == "gemini":
            body = {"candidates": [{"content": {"parts": [{"text": self.reply}]}}]}
        else:
            body = {"choices": [{"message": {"role": "assistant", "content": self.reply}}]}
        return httpx.Response(200, json=body, request=request)

    def providers(self):
        return [c["provider"] for c in self.calls]


def run(transport, coro_fn):
    original = gemini_http.client
    gemini_http.client = lambda: transport
    try:
        return asyncio.run(coro_fn())
    finally:
        gemini_http.client = original


CHAIN = ["g1", "g2", "g3"]
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
CANDIDATES = [{"move": "e2e4", "san": "e4"}, {"move": "d2d4", "san": "d4"}]
CTX = {"fen": "8/8/8/8/8/8/8/K6k w - - 0 1"}
EVIDENCE = {
    "san": "Nf3", "uci": "g1f3", "color": "white", "ply": 5, "phase": "opening",
    "fen_before": FEN, "cpl": 120, "best_san": "e4", "best_move": "e2e4",
    "eval_before": {"score": 20, "mate_in": None},
    "eval_after": {"score": -100, "mate_in": None},
    "pv_san": ["e4", "e5"], "depth": 14, "quality": {"label": "mistake", "name": "Mistake"},
}
DIAGNOSIS_JSON = (
    '{"theme": "TACTICAL_OVERLOOK", "missed_factor": "You did not look at the centre first.",'
    ' "diagnosis": "You wanted to develop, but the position asked for a stronger central claim.",'
    ' "confidence": 0.6, "uncertainty": ""}'
)


def chat(t):
    svc = GeminiChatService(api_key=GEMINI_KEY, models=list(CHAIN))
    return run(t, lambda: svc.send_message(f"why? {PROMPT_MARKER}", [], CTX))


def move(t, svc=None):
    svc = svc or GeminiMoveService(api_key=GEMINI_KEY, models=list(CHAIN))
    return run(t, lambda: svc.choose_move_from_candidates(FEN, CANDIDATES))


def diagnose(t):
    svc = DiagnosisService(api_key=GEMINI_KEY, models=list(CHAIN))
    return run(t, lambda: svc.diagnose(EVIDENCE, f"I wanted to develop {PROMPT_MARKER}"))


# --- 1. Gemini succeeds -> DeepSeek not called ------------------------------
env()
t = Transport()
ok, reply = chat(t)
check("1. Gemini answers: chat succeeds", ok and "REPLY-MARKER" in reply, reply)
check("1. ...with exactly one provider call, and it is Gemini", t.providers() == ["gemini"], t.providers())

# --- 2. Gemini 503 / 429 / timeout -> DeepSeek once -------------------------
for failure in (503, 429, "timeout"):
    env()
    t = Transport(gemini=failure)
    ok, reply = chat(t)
    check(f"2. Gemini {failure}: DeepSeek answers the chat", ok and "REPLY-MARKER" in reply, reply)
    check(f"2. ...Gemini once then DeepSeek once (2 calls total)",
          t.providers() == ["gemini", "deepseek"], t.providers())
    if failure == "timeout":
        check("7. a Gemini timeout cools that model", gemini_http.cooling_for("g1") > 0)

# The DeepSeek request itself: official endpoint, key only in the header.
ds = t.calls[-1]
check("DeepSeek is called at {base}/chat/completions",
      ds["url"] == "https://api.deepseek.com/chat/completions", ds["url"])
check("...with Authorization: Bearer <key>", ds["headers"].get("Authorization") == f"Bearer {DS_KEY}")
check("...and the key is not in the URL", DS_KEY not in ds["url"])
check("...on the configured model", ds["json"]["model"] == "deepseek-flash", ds["json"]["model"])
check("...with the system instruction and the user turn translated",
      ds["json"]["messages"][0]["role"] == "system" and ds["json"]["messages"][-1]["role"] == "user")
check("...and the Gemini key is not sent to DeepSeek", GEMINI_KEY not in str(ds["headers"]))

env(DEEPSEEK_BASE_URL="https://ds.example.test/", DEEPSEEK_MODEL="deepseek-reasoner")
t = Transport(gemini=503)
chat(t)
check("DEEPSEEK_BASE_URL and DEEPSEEK_MODEL are honoured",
      t.calls[-1]["url"] == "https://ds.example.test/chat/completions"
      and t.calls[-1]["json"]["model"] == "deepseek-reasoner", t.calls[-1]["url"])

# --- 3. simulated Gemini failure + DeepSeek success -> normal output --------
env(LLM_SIMULATE_GEMINI_FAILURE="true")
t = Transport()
ok, reply = chat(t)
check("3. simulated Gemini failure: chat still answers via DeepSeek", ok and "REPLY-MARKER" in reply)
check("3. ...the simulated failure made no network call", t.providers() == ["deepseek"], t.providers())

t = Transport(reply="e2e4\nI take the centre with my pawn.")
mv, explanation, ok = move(t)
check("3. AI move: DeepSeek picks from the shortlist", ok and mv == "e2e4", (mv, ok))
check("3. ...and explains it", "centre" in explanation, explanation)

t = Transport(reply="h2h4\nA move nobody offered.")
mv, _e, ok = move(t)
check("3. a DeepSeek move outside the shortlist is refused (engine move instead)", not ok and mv is None)

t = Transport(reply=DIAGNOSIS_JSON)
ok, result = diagnose(t)
check("3. saved lesson: DeepSeek writes a validated diagnosis", ok and result.get("theme") == "TACTICAL_OVERLOOK",
      result)
check("3. ...asked in DeepSeek's JSON mode",
      t.calls[-1]["json"].get("response_format") == {"type": "json_object"}
      and "json" in t.calls[-1]["json"]["messages"][0]["content"])

# --- 4. both fail -> deterministic fallback ---------------------------------
env(LLM_SIMULATE_GEMINI_FAILURE="true", LLM_SIMULATE_DEEPSEEK_FAILURE="true")
t = Transport()
ok, reply = chat(t)
check("4. both fail: chat gives the calm coach-unavailable copy", not ok and reply == COACH_UNAVAILABLE, reply)
for word in ("deepseek", "gemini", "timeout", "traceback", "simulated", "401"):
    check(f"4. ...which does not say '{word}'", word not in reply.lower())
mv, _e, ok = move(t)
check("4. AI move: no move invented, so the engine's pick is played", not ok and mv is None)
ok, result = diagnose(t)
check("4. saved lesson: diagnosis fails over ...", not ok)
card = fallback_diagnosis(EVIDENCE, "I wanted to develop")
check("4. ...to the deterministic, engine-backed card", card.get("source") != "coach" and card.get("theme"), card)
check("4. and no provider was actually called", t.calls == [], t.providers())

env()
t = Transport(gemini=503, deepseek=401)
ok, reply = chat(t)
check("4. real Gemini 503 + DeepSeek 401: calm copy, no provider error",
      reply == COACH_UNAVAILABLE and "401" not in reply)
check("...and a rejected key parks DeepSeek rather than retrying it", gemini_http.cooling_for(SLOT) > 60)

# --- 5 / 6. DeepSeek absent -> skipped ---------------------------------------
for label, values in [("5. no key", {"DEEPSEEK_API_KEY": None}),
                      ("6. disabled", {"DEEPSEEK_ENABLED": "false"}),
                      ("6. unset entirely", {"DEEPSEEK_ENABLED": None, "DEEPSEEK_API_KEY": None}),
                      ("6. not in LLM_PROVIDERS", {"LLM_PROVIDERS": "gemini"})]:
    env(**values)
    t = Transport(gemini=503)
    ok, reply = chat(t)
    check(f"{label}: DeepSeek is never called", "deepseek" not in t.providers(), t.providers())
    check(f"{label}: the Gemini-only chain is unchanged (2 Gemini models)",
          t.providers() == ["gemini", "gemini"], t.providers())
    check(f"{label}: the app still answers calmly", reply == COACH_UNAVAILABLE)
    t = Transport()
    ok, reply = chat(t)
    check(f"{label}: and Gemini alone still works", ok and "REPLY-MARKER" in reply)

# --- 7. DeepSeek timeout cools DeepSeek -------------------------------------
env()
t = Transport(gemini=503, deepseek="timeout")
chat(t)
check("7. a DeepSeek timeout cools DeepSeek", 0 < gemini_http.cooling_for(SLOT) <= 60,
      gemini_http.cooling_for(SLOT))
gemini_http.reset_cooldowns()
gemini_http.cool(SLOT, 60, reason="test")
check("7. ...so the next request does not select it",
      SLOT not in gemini_http.eligible(CHAIN, 2), gemini_http.eligible(CHAIN, 2))
check("...and Gemini gets its usual two models back while it cools",
      gemini_http.eligible(CHAIN, 2) == ["g1", "g2"])

# --- 8. the budget ------------------------------------------------------------
env()
services = [
    ("move", GeminiMoveService(api_key=GEMINI_KEY, models=list(CHAIN))),
    ("chat", GeminiChatService(api_key=GEMINI_KEY, models=list(CHAIN))),
    ("narration", GeminiNarrationService(api_key=GEMINI_KEY, models=list(CHAIN))),
    ("scenario", ScenarioService(api_key=GEMINI_KEY, models=list(CHAIN))),
    ("diagnosis", DiagnosisService(api_key=GEMINI_KEY, models=list(CHAIN))),
]
for name, svc in services:
    check(f"8. {name}: one Gemini model then DeepSeek, 2 total", svc._model_order() == ["g1", SLOT],
          svc._model_order())
    svc._last_good_model = SLOT
    check(f"8. {name}: DeepSeek stays the fallback even after it answered",
          svc._model_order() == ["g1", SLOT], svc._model_order())
    svc.api_key = ""
    check(f"8. {name}: Gemini without a key -> DeepSeek only, 1 call", svc._model_order() == [SLOT],
          svc._model_order())
    avail = getattr(svc, "available", True)
    check(f"8. {name}: and the service still counts as available",
          (avail() if callable(avail) else avail) is True)

env(LLM_PROVIDERS="deepseek")
check("8. LLM_PROVIDERS=deepseek -> DeepSeek only", gemini_http.eligible(CHAIN, 2) == [SLOT])
env(LLM_PROVIDERS="deepseek,gemini")
check("8. the order is configurable", gemini_http.eligible(CHAIN, 2) == [SLOT, "g1"])
env()
check("8. a cap of 1 keeps Gemini primary", gemini_http.eligible(CHAIN, 1) == ["g1"])

# Every failure shape, every service that runs a chain: never more than 2 calls.
for gemini_failure in (503, 429, "timeout"):
    for ds_failure in (503, 429, "timeout", 401):
        env()
        t = Transport(gemini=gemini_failure, deepseek=ds_failure)
        chat(t)
        move(t)
        diagnose(t)
        # Three logical requests on one transport: the cooldowns make the
        # later ones cheaper, never dearer.
        check(f"8. Gemini {gemini_failure} + DeepSeek {ds_failure}: at most 2 calls per request "
              f"({len(t.calls)} for 3 requests)", len(t.calls) <= 6, t.providers())
        env()
        t = Transport(gemini=gemini_failure, deepseek=ds_failure)
        move(t)
        check(f"8. ...one AI move alone: {t.providers()}", len(t.calls) <= 2
              and t.providers().count("deepseek") <= 1, t.providers())

# Move selection hedges (runs the fallback alongside a slow lead). Still 2.
env()
t = Transport(gemini="timeout", reply="d2d4\nSolid.")
mv, _e, ok = move(t)
check("8. a hanging Gemini on a move: DeepSeek answers inside the move ceiling", ok and mv == "d2d4", (mv, ok))
check("8. ...with 2 calls, not 4", t.providers() == ["gemini", "deepseek"], t.providers())

# --- 9. nothing sensitive in the logs ----------------------------------------
logged = "\n".join(LOGGED)
for label, secret in [("the Gemini key", GEMINI_KEY), ("the DeepSeek key", DS_KEY),
                      ("the prompt / user text", PROMPT_MARKER), ("a provider response body", BODY_MARKER),
                      ("the model's reply text", "REPLY-MARKER"), ("an Authorization header", "Bearer")]:
    check(f"9. {label} never appears in the logs", secret not in logged)
check("9. each call is logged as sanitized metadata",
      "llm_call provider=deepseek model=deepseek/deepseek-flash" in logged and "outcome=" in logged)

env(DEEPSEEK_ENABLED=None, DEEPSEEK_API_KEY=None)
print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)

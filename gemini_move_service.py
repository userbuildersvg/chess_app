"""
gemini_move_service.py

Gemini picks the AI's move, talking to the Gemini REST API directly - no
Langflow in the path.

Why this exists
---------------
Move selection used to go: app.py -> langflow_service -> a Langflow server
-> the "Chess" flow -> Gemini. That indirection cost a whole extra service
to run and keep alive (a real problem on a small host, where Langflow's
own memory footprint is the largest thing in the deployment), and it fails
in the most annoying possible way: when Langflow isn't reachable, every AI
move silently degrades to "Stockfish-calculated move (Langflow
unavailable)" - the LLM, i.e. the entire point of the app, quietly drops
out of the loop.

gemini_chat_service.py had already made this exact move for the chat
feature - direct httpx calls to generateContent with a model fallback
chain. This module does the same for move selection, so the app's core
behaviour no longer depends on a second service being up.

Contract
--------
`choose_move_from_candidates(fen, candidates)` deliberately mirrors
`ChessLangflowManager.choose_move_from_candidates()`, returning the same
`(move, explanation, success)` tuple, so app.py's decide_ai_move() can use
either one without caring which is behind it.

The shortlist is authority, not suggestion: Gemini is shown only the
Stockfish-ranked candidate window and its answer is validated against that
exact list. Anything it invents outside the list is rejected and reported
as a failure, which sends decide_ai_move() to its Stockfish fallback. That
is what keeps an LLM in the loop from ever producing an illegal or
difficulty-breaking move.
"""
import os
import re
import logging
from typing import Optional

import asyncio
import httpx

import gemini_http

# Shares the chat service's endpoint constant. The model chain is NOT
# shared - see GEMINI_MOVE_MODELS below for why move selection orders its
# own by latency. Chat's docstring explains why a chain exists at all:
# model availability on a given key varies between requests.
from gemini_chat_service import GEMINI_API_BASE

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Move selection keeps its own chain rather than inheriting the chat one.
# Chat is a background convenience; this call sits between the human moving
# and the AI replying, so it is ordered by measured latency first.
#
# Measured on this project's key (2 calls each, real move-selection prompt,
# all working models picked the same move - with a 3-move pre-vetted
# shortlist the model's job is narrow, so speed costs very little quality):
#
#   gemini-3.5-flash-lite     0.88s   OK
#   gemini-3.5-flash          3.09s   OK   (slightly richer explanations)
#   gemini-3.7-flash          3.24s   OK   (intermittent ReadTimeout)
#   gemini-3.1-flash-lite     6.97s   OK
#   gemini-3.6-flash          7.89s   OK   (frequently ReadTimeouts)
#   gemini-3.8-flash            -     503
#   gemini-flash-latest         -     503
#   gemini-pro-latest           -     429
#   gemini-2.5-flash / -pro     -     404 "no longer available to new users"
#
# Note gemini-2.5-* is *listed* by the models endpoint on this key but 404s
# when actually called - don't trust the listing alone. The floating
# aliases (…-latest) are deliberately not first: they silently change model
# underneath you, which is exactly the kind of surprise this chain exists
# to absorb rather than cause.
GEMINI_MOVE_MODELS = [
    m.strip() for m in os.environ.get("GEMINI_MOVE_MODELS", "").split(",") if m.strip()
] or [
    # Ordered by RELIABILITY first, then latency - not by raw benchmark
    # speed. gemini-3.5-flash-lite measured fastest (0.88s) in a quiet
    # two-call probe and then ReadTimed-out on most moves under real play,
    # so a model's isolated best case is not a safe way to rank it here.
    # gemini-3.5-flash answered consistently in both, and writes the better
    # explanation of the two, which is the part the player actually reads.
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]

# Much shorter than the chat timeout on purpose: this call sits directly
# between the human moving and the AI replying, and there is always a good
# Stockfish move ready to fall back to. Waiting for an LLM that may never
# answer is worse than playing the engine's pick now.
#
# 6s is chosen from measurement, not taste: every model that works answers
# in 0.9-3.3s, so anything past ~6s is hanging rather than thinking. The
# old 12s value meant one flaky model in the chain silently added 12s to
# *every* move before falling through - which is exactly what a chain is
# supposed to prevent.
REQUEST_TIMEOUT = float(os.environ.get("GEMINI_MOVE_TIMEOUT", "6"))

# How long to give one model before ALSO asking the next one.
#
# The chain is a fallback list, and tried strictly in order it has a bad
# property: discovering that a model is hanging costs the full REQUEST_TIMEOUT,
# and it is paid again for each hanging model before a working one is reached.
# Measured on the dev stack, an AI move's Gemini time ranged from 2.5s to 19s,
# and the 19s was two 6s timeouts plus one normal answer - not a slow model.
#
# So after this long, the next model is started ALONGSIDE the first rather than
# after it, and the first usable answer wins. A model that was merely slow
# still gets to win; a model that has hung simply stops being the only hope.
#
# 1.4s is chosen from the same measurement §7 records: every model that works
# answers in 0.9-3.3s, so a model still silent at 1.4s is more likely to be
# hanging than thinking - but it is not yet abandoned, because it might not be.
#
# The cost is a second request on the slow tail only, and it goes to a
# DIFFERENT model, so it does not compete for the quota of the one already
# struggling - which is the same argument §5 makes for the six chains leading
# with six different models. Set to 0 to disable hedging entirely.
HEDGE_DELAY = float(os.environ.get("GEMINI_MOVE_HEDGE_DELAY", "1.4"))

# The most requests in flight at once for a single move. Three is the point
# where a fourth is not buying latency any more, just quota.
MAX_IN_FLIGHT = int(os.environ.get("GEMINI_MOVE_MAX_IN_FLIGHT", "3"))

# Prefixes a reason that means "stop the whole race", not "try another
# model". A content-policy refusal is the only one: every model on the key
# will refuse the same prompt, so hedging across them just spends quota to
# be told the same thing three times.
_BLOCKED = "\x00blocked\x00"

UCI_PATTERN = re.compile(r"\b[a-h][1-8][a-h][1-8][qrbn]?\b")


class GeminiMoveService:
    def __init__(self, api_key: str = None, models: Optional[list] = None):
        # Read the key at construction rather than import time so a key set
        # after this module is first imported (a .env loaded late, a test)
        # still takes effect.
        self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", GEMINI_API_KEY)
        self.models = list(models) if models is not None else list(GEMINI_MOVE_MODELS)
        # The model that last answered successfully, tried first next time.
        #
        # This is worth real seconds per move. Model availability on a given
        # key is volatile (see gemini_chat_service's docstring), and a model
        # that is *timing out* rather than refusing costs the full request
        # timeout before the chain moves on - paid again on every single
        # move. Sticking to whatever worked last turns that into a one-off
        # cost instead of a permanent tax, while still re-trying the
        # preferred order from scratch whenever the sticky model fails.
        self._last_good_model: Optional[str] = None

    def _model_order(self) -> list:
        """Preferred chain, with the last known-good model moved to front."""
        if self._last_good_model and self._last_good_model in self.models:
            return [self._last_good_model] + [m for m in self.models if m != self._last_good_model]
        return list(self.models)

    @property
    def available(self) -> bool:
        """Whether this service can be used at all - app.py checks this to
        decide between Gemini, Langflow and engine-only operation."""
        return bool(self.api_key) and bool(self.models)

    def _build_prompt(self, fen: str, candidates: list, context: dict = None) -> str:
        context = context or {}
        lines = []
        for i, c in enumerate(candidates):
            if c.get("mate_in") is not None:
                score_text = f"forced mate in {c['mate_in']}"
            elif c.get("score") is not None:
                score_text = f"{c['score']} centipawns"
            else:
                score_text = "unscored"
            san = f" ({c['san']})" if c.get("san") else ""
            lines.append(f"{i + 1}. {c['move']}{san} - engine evaluation: {score_text}")
        candidates_text = "\n".join(lines)

        prompt = [
            "You are the AI opponent in a chess game. Choose your next move.",
            f"Position (FEN): {fen}",
        ]
        if context.get("move_history_san"):
            prompt.append(f"Moves so far: {', '.join(context['move_history_san'])}")
        if context.get("difficulty") is not None:
            prompt.append(
                f"Your difficulty setting is {context['difficulty']} out of 20. The "
                f"shortlist below has already been narrowed to match that strength - "
                f"you do not need to weaken your choice yourself."
            )
        prompt += [
            "",
            "These are the ONLY moves you may choose from. They are pre-validated as "
            "legal, and evaluations are from your own side's perspective (higher is "
            "better for you):",
            candidates_text,
            "",
            "Respond in exactly this format:",
            "Line 1: the move in UCI format, nothing else (for example: e2e4)",
            "Line 2: one or two sentences on why you chose it, in your own voice as "
            "the opponent. Talk about the idea behind the move - a plan, a threat, a "
            "weakness - not the engine numbers.",
            "",
            "The move on line 1 MUST be copied exactly from the list above.",
        ]
        return "\n".join(prompt)

    @staticmethod
    def _parse(text: str, candidate_ucis: list) -> tuple:
        """
        Pull a candidate move and an explanation out of Gemini's reply.

        Returns (move_or_None, explanation). The move is only ever one of
        `candidate_ucis` - the first UCI token in the reply that appears in
        the shortlist wins, which tolerates the model wrapping its answer
        in prose ("I'll play e2e4 here because...") without accepting a
        move it invented.
        """
        if not text:
            return None, ""

        chosen = None
        for match in UCI_PATTERN.findall(text.lower()):
            if match in candidate_ucis:
                chosen = match
                break

        # Explanation = everything that isn't the bare move line. Falls back
        # to the whole reply when the model didn't follow the line format.
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        body = [ln for ln in lines if not (chosen and ln.lower().strip(" .:-") == chosen)]
        explanation = " ".join(body).strip() or text.strip()
        return chosen, explanation

    async def choose_move_from_candidates(self, fen: str, candidates: list, context: dict = None) -> tuple:
        """
        Ask Gemini to pick one move from the Stockfish-ranked shortlist.

        Returns (move, explanation, success), matching
        ChessLangflowManager.choose_move_from_candidates() so the two are
        interchangeable in decide_ai_move().

        Never raises for an expected failure (no key, model down, bad
        answer): it returns success=False with a human-readable reason, and
        the caller falls back to the engine's own pick.
        """
        if not candidates:
            return None, "No candidate moves to choose from", False
        if not self.api_key:
            return None, "no GEMINI_API_KEY configured for the server", False
        if not self.models:
            return None, "no Gemini models configured", False

        candidate_ucis = [c["move"] for c in candidates]
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text": self._build_prompt(fen, candidates, context)}],
            }],
        }

        order = self._model_order()

        # Ask ONE model, and say what came back.
        #
        # Split out of the loop it used to live in so the same attempt can be
        # run sequentially or hedged without two copies of the parsing. Returns
        # (move, explanation) on success, or (None, reason) - it never raises,
        # because a dead model is an expected outcome here and the caller has a
        # Stockfish move ready either way.
        async def attempt(model: str):
            url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
            try:
                # One shared, pooled connection instead of a new client - and so
                # a new TLS handshake - per call. Measured against the real
                # endpoint: a median of 530ms per call, 28%, with the request and
                # the response byte-identical to what this sent before. See
                # gemini_http.
                response = await gemini_http.post(
                    url, payload, self.api_key, REQUEST_TIMEOUT
                )
            except asyncio.CancelledError:
                # Another model won the race. Not a failure, and not worth a
                # line in the log - this is the hedge working.
                raise
            except Exception as e:
                # Report the exception *type*, not just str(e): httpx's timeout
                # exceptions stringify to an empty message, so a model that was
                # silently eating the whole request timeout logged as "failed: "
                # with no reason at all.
                reason = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                logger.warning(f"⚠️ Gemini move request to {model} failed ({reason})")
                return None, f"couldn't reach Gemini ({model}): {reason}"

            if response.status_code != 200:
                logger.warning(
                    f"⚠️ Gemini move HTTP {response.status_code} for {model}: {response.text[:200]}"
                )
                return None, f"HTTP {response.status_code} for {model}"

            try:
                data = response.json()
                api_candidates = data.get("candidates", [])
                if not api_candidates:
                    block_reason = data.get("promptFeedback", {}).get("blockReason")
                    if block_reason:
                        # A different model will not un-block a content policy
                        # refusal. Marked so the caller stops the whole race
                        # rather than burning the chain on the same answer.
                        return None, _BLOCKED + f"Gemini declined to answer ({block_reason})"
                    return None, f"empty response from {model}"
                parts = api_candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
            except Exception as e:
                logger.warning(f"⚠️ Failed to parse Gemini move response from {model}: {e}")
                return None, f"couldn't parse response from {model}: {e}"

            move, explanation = self._parse(text, candidate_ucis)
            if move:
                return move, explanation

            # A reply that names no shortlisted move is a bad answer, not a
            # dead model - but another model is still the cheapest recovery.
            logger.warning(
                f"⚠️ Gemini ({model}) picked nothing from {candidate_ucis} - reply was: {text[:160]}"
            )
            return None, f"{model} did not choose a shortlisted move"

        # --- The race --------------------------------------------------------
        #
        # Start the lead model. If it has not answered within HEDGE_DELAY, start
        # the next one alongside it rather than waiting out REQUEST_TIMEOUT to
        # discover it has hung - which is what a measured 19s move turned out to
        # be. First usable answer wins and the rest are cancelled.
        #
        # With HEDGE_DELAY at 0 this degrades to exactly the sequential chain it
        # replaced: one model at a time, each given the full timeout.
        tasks = {}
        last_error = "no models tried"
        remaining = list(order)
        try:
            while remaining or tasks:
                if remaining and len(tasks) < MAX_IN_FLIGHT:
                    model = remaining.pop(0)
                    tasks[asyncio.ensure_future(attempt(model))] = model
                if not tasks:
                    break
                # Wait for an answer, or for the hedge delay to expire so the
                # next model can be brought in. No timeout once the chain is
                # exhausted or the in-flight cap is reached: at that point
                # REQUEST_TIMEOUT is the only thing left to wait for.
                hedgeable = remaining and len(tasks) < MAX_IN_FLIGHT and HEDGE_DELAY > 0
                done, _ = await asyncio.wait(
                    tasks.keys(),
                    timeout=HEDGE_DELAY if hedgeable else None,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    # Nobody answered in time - loop round and hedge.
                    logger.info(
                        f"⏱ Gemini move: hedging after {HEDGE_DELAY}s "
                        f"({', '.join(tasks.values())} still out)"
                    )
                    continue
                for task in done:
                    model = tasks.pop(task)
                    try:
                        move, detail = task.result()
                    except asyncio.CancelledError:
                        continue
                    except Exception as e:  # pragma: no cover - attempt catches its own
                        last_error = f"{model} raised {type(e).__name__}: {e}"
                        continue
                    if move:
                        if model != order[0]:
                            logger.info(f"ℹ️ Gemini move succeeded on fallback model {model}")
                        if self._last_good_model != model:
                            logger.info(f"ℹ️ Preferring {model} for subsequent move requests")
                            self._last_good_model = model
                        logger.info(f"✅ Gemini chose {move} from {len(candidates)} candidates")
                        return move, detail, True
                    if detail.startswith(_BLOCKED):
                        return None, detail[len(_BLOCKED):], False
                    last_error = detail
        finally:
            # Whatever is still running lost the race, or the winner has been
            # found. Either way nobody is going to read their answer, and a
            # request left running holds a connection in the shared pool.
            for task in tasks:
                task.cancel()

        return None, last_error, False


# Global instance, mirroring the gemini_chat_service/stockfish_service pattern.
gemini_move_service = GeminiMoveService()

"""
gemini_narration_service.py

Teaching narration for Sandbox Learner Mode: one short piece of commentary
per demonstrated move, in a coach's voice.

How this differs from the move explanation we already have
----------------------------------------------------------

`gemini_move_service` already returns an `explanation` with every move -
but that is the *player's* voice justifying its own choice ("I'm staking a
claim in the centre"). Narration is the *coach's* voice explaining the move
to someone watching: what it accomplishes, what it rules out, and - the
part that only the sandbox can offer - what the alternatives were and why
they lose out. Stockfish has already ranked the whole legal move list to
pick the shortlist, so those alternatives cost nothing extra to include.

Why a third model chain
-----------------------

Moves and chat already lead with deliberately different models because they
share one API key and competed for quota - with both on gemini-3.5-flash,
chat started getting HTTP 429 from move traffic on the very first question.

Narration makes that worse: it is fired *in parallel* with play, so a
sandbox demonstration can have a narration call in flight at the same time
as the next move's selection call. So this gets its own chain, led by a
model neither of the others leads with, and its own concurrency cap.

Note the chain here is led by a model that measured *slower* in probing
(gemini-3.1-flash-lite at ~7s). That is deliberate and not a mistake:
narration never blocks a user-visible response - it lands on its node
whenever it arrives - so staying out of the way of move selection matters
much more than finishing first.
"""
import asyncio
import logging
import os
from typing import Optional

import httpx

import gemini_http

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Ordered fallback chain, same "degrade gracefully" approach as moves and
# chat. Led by gemini-3.1-flash-lite: measured working on this key, and it
# is neither move selection's lead (gemini-3.5-flash) nor chat's lead
# (gemini-3.7-flash), which is the whole point - see the module docstring.
# gemini-3.6-flash is last everywhere in this project because it does not
# refuse requests, it *hangs*, so leading with it burns the full timeout.
GEMINI_NARRATION_MODELS = [
    m.strip() for m in os.environ.get("GEMINI_NARRATION_MODELS", "").split(",") if m.strip()
] or [
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
]

# Narration is background work: nothing is waiting on it, so it can afford a
# longer timeout than move selection's 6s. It still needs *a* timeout,
# because a hung model would otherwise hold a task and a semaphore slot for
# the life of the process.
REQUEST_TIMEOUT = float(os.environ.get("GEMINI_NARRATION_TIMEOUT", "20"))

# Ceiling on narration calls in flight at once, across all sessions. A fast
# demonstration (or several at once, once this is public) would otherwise
# fire one call per ply with nothing to throttle them, which is exactly how
# the shared key gets rate-limited. Queued calls simply wait.
MAX_CONCURRENT = int(os.environ.get("GEMINI_NARRATION_CONCURRENCY", "2"))


class GeminiNarrationService:
    def __init__(self, api_key: str = GEMINI_API_KEY, models: Optional[list] = None):
        self.api_key = api_key
        self.models = list(models) if models is not None else list(GEMINI_NARRATION_MODELS)
        # Same trick as chat and moves: remember what last worked, so a
        # hanging or rate-limited model costs its timeout once per server
        # run instead of once per move.
        self._last_good_model: Optional[str] = None
        self._semaphore: Optional[asyncio.Semaphore] = None

    @property
    def available(self) -> bool:
        return bool(self.api_key) and bool(self.models)

    def _get_semaphore(self) -> asyncio.Semaphore:
        # Created lazily, on the running loop, rather than at import time.
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        return self._semaphore

    def _model_order(self) -> list:
        if self._last_good_model and self._last_good_model in self.models:
            return [self._last_good_model] + [m for m in self.models if m != self._last_good_model]
        return list(self.models)

    def build_prompt(self, context: dict) -> str:
        """
        Build the narration prompt.

        `context` keys (all optional except move/fen_before):
          move, san, mover, fen_before, fen_after, line_san, explanation,
          alternatives (list of {move, san, score}), title, is_capture,
          is_check, is_game_over, source.

        Kept as a separate method so tests can assert what the model is
        actually being told without making an HTTP call.
        """
        line = " ".join(context.get("line_san") or []) or "(this is the first move)"
        lines = [
            "You are a chess coach narrating a demonstration game, move by move, "
            "to a student who is watching the board.",
            "",
            f"Scenario: {context.get('title') or 'open play'}",
            f"Moves so far: {line}",
            f"Position before this move (FEN): {context.get('fen_before')}",
            f"The move just played: {context.get('san') or context.get('move')} "
            f"by {context.get('mover') or 'unknown'}.",
        ]
        if context.get("source") == "human":
            lines.append(
                "This move was played by the student, who has taken over the board - "
                "so react to their choice rather than describing your own plan."
            )
        if context.get("explanation"):
            lines.append(f"The player's own stated reason was: {context['explanation']}")

        alternatives = context.get("alternatives") or []
        if alternatives:
            # Filter on the UCI field, not the display label: the label may
            # be SAN ("Nf3") while context["move"] is UCI ("g1f3"), so
            # comparing the two silently never matches and offers the move
            # that was actually played back as an alternative to it.
            shown = ", ".join(
                f"{a.get('san') or a.get('move')} (eval {a.get('score')})"
                for a in alternatives[:4]
                if a.get("move") != context.get("move")
            )
            if shown:
                lines.append(
                    f"Engine alternatives in this position, with centipawn evaluations: {shown}. "
                    "Reference one of these only if the contrast genuinely teaches something."
                )
        if context.get("is_check"):
            lines.append("This move gives check.")
        if context.get("is_game_over"):
            lines.append("This move ends the game.")

        lines += [
            "",
            "Write the narration for this one move.",
            "Rules:",
            "- Two or three sentences. This is spoken alongside the move, not an essay.",
            "- Say what the move actually achieves in this position - a concrete square, "
            "piece, threat or plan. Avoid generic filler like 'a solid developing move'.",
            "- Plain prose. No move numbers, no headings, no bullet points, no markdown.",
            "- Do not repeat the move notation back; the student can see it on the board.",
        ]
        return "\n".join(lines)

    async def narrate(self, context: dict) -> tuple:
        """
        Generate narration for one move.

        Returns (success, text_or_error). Never raises: the caller is a
        background task whose failure must not affect the move that was
        already played.
        """
        if not self.api_key:
            return False, "No GEMINI_API_KEY configured."
        if not self.models:
            return False, "No Gemini narration models configured."

        payload = {"contents": [{"role": "user", "parts": [{"text": self.build_prompt(context)}]}]}
        last_error = "no models tried"

        async with self._get_semaphore():
            for model in self._model_order():
                url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
                try:
                    # One shared, pooled connection instead of a new client - and so a new
                    # TLS handshake - per call. Measured against the real endpoint: a
                    # median of 530ms per call, 28%, with the request and the response
                    # byte-identical to what this sent before. See gemini_http.
                    response = await gemini_http.post(
                        url, payload, self.api_key, REQUEST_TIMEOUT
                    )
                except asyncio.CancelledError:
                    # The session was deleted or the server is shutting down.
                    # Propagate rather than swallowing it as a failure.
                    raise
                except Exception as e:
                    reason = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                    logger.warning(f"⚠️ Gemini narration request to {model} failed ({reason})")
                    last_error = f"couldn't reach Gemini ({model}): {reason}"
                    continue

                if response.status_code != 200:
                    logger.warning(
                        f"⚠️ Gemini narration HTTP {response.status_code} for {model}: "
                        f"{response.text[:200]}"
                    )
                    last_error = f"HTTP {response.status_code} for {model}"
                    continue

                try:
                    data = response.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        block_reason = data.get("promptFeedback", {}).get("blockReason")
                        if block_reason:
                            # A different model will not un-block this.
                            return False, f"Gemini declined to narrate ({block_reason})."
                        last_error = f"empty response from {model}"
                        continue
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts).strip()
                    if not text:
                        last_error = f"empty response from {model}"
                        continue
                    if self._last_good_model != model:
                        logger.info(f"ℹ️ Preferring {model} for subsequent narration")
                        self._last_good_model = model
                    return True, text
                except Exception as e:
                    logger.warning(f"⚠️ Failed to parse Gemini narration from {model}: {e}")
                    last_error = f"couldn't parse response from {model}: {e}"
                    continue

        return False, f"All Gemini models are currently unavailable ({last_error})."


# Global instance, mirroring the other services' module-level pattern.
gemini_narration_service = GeminiNarrationService()

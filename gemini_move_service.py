"""
gemini_move_service.py

Move selection - given a FEN and a Stockfish-ranked candidate shortlist,
ask Gemini to pick one - calling Gemini's REST API directly instead of
routing the request through Langflow.

Why this exists
---------------
The Langflow "Chess" flow this replaces is four nodes: ChatInput ->
Prompt Template -> Gemini -> ChatOutput. Every part of the prompt is
already assembled in Python before the request is sent (see the
`input_text` built in langflow_service.choose_from_candidates), so the
flow contributes no logic of its own - it forwards a string to Gemini and
hands the reply back.

Running that as a second service costs a Render instance with 2 GB of RAM.
Langflow is killed by the OOM reaper (exit 137) on anything smaller, and
neither the free nor the Starter plan offers more than 512 MB. Paying for
2 GB to proxy one HTTP call isn't a good trade for a hobby deployment, and
dropping the service also removes a publicly reachable Langflow UI from the
attack surface entirely.

The prompt below is deliberately identical to the one the flow received,
so move-selection behaviour doesn't change - only the transport does.

Langflow is NOT removed from the project. docker-compose.yml still runs it
locally, and setting MOVE_SELECTOR=langflow restores the original path, so
the flow can still be edited and experimented with in its UI.

Model handling mirrors gemini_chat_service.py: a fallback chain rather than
a single hardcoded model, because model availability on this project's key
proved genuinely volatile (see that module's docstring for the evidence).
"""

import os
import re
import logging
from typing import Optional, List, Tuple

import httpx

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Same ordered fallback chain as the chat service - each tried only if every
# model before it failed for this particular request.
GEMINI_MOVE_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3-flash-preview",
]
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
REQUEST_TIMEOUT = 30.0

UCI_PATTERN = r'\b[a-h][1-8][a-h][1-8][qrbn]?\b'


class GeminiMoveService:
    def __init__(self, api_key: str = GEMINI_API_KEY, models: Optional[list] = None):
        self.api_key = api_key
        self.models = list(models) if models is not None else list(GEMINI_MOVE_MODELS)

    def _build_prompt(self, fen: str, candidates: list) -> str:
        """
        Byte-for-byte the prompt langflow_service.choose_from_candidates fed
        into the flow's ChatInput. Kept identical on purpose: the point of
        this module is to change the transport, not the AI's behaviour.
        """
        lines = []
        for i, c in enumerate(candidates):
            if c.get("score") is not None:
                score_text = f"score: {c['score']}"
            else:
                score_text = f"mate in {c['mate_in']}"
            lines.append(f"{i+1}. {c['move']} ({score_text})")
        candidates_text = "\n".join(lines)

        return (
            f"Chess position (FEN): {fen}\n\n"
            f"Here are the top candidate moves, ranked by engine evaluation (higher score = better for you):\n"
            f"{candidates_text}\n\n"
            f"Choose ONE move from this exact list. Respond with ONLY the move in UCI format "
            f"(e.g. e2e4) on the first line, nothing else. On the next line, briefly explain your choice."
        )

    def _extract_uci_move(self, text: str, legal_moves: List[str]) -> Optional[str]:
        """
        First UCI-looking token that is actually one of the offered
        candidates. Same rule the Langflow path used - the model sometimes
        mentions other squares while explaining itself, so matching against
        the candidate list rather than taking the first match is what keeps
        a stray mention from being played.
        """
        for match in re.findall(UCI_PATTERN, text.lower()):
            if match in legal_moves:
                return match
        return None

    def _extract_explanation(self, text: str) -> str:
        """Everything after the first line, which is the move itself."""
        parts = text.strip().split("\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""

    async def choose_move_from_candidates(self, fen: str, candidates: list) -> Tuple[Optional[str], str, bool]:
        """
        Returns (move, explanation, success) - the same shape
        ChessLangflowManager.choose_move_from_candidates returns, so app.py
        can swap between the two without caring which is in use.

        On failure the caller falls back to Stockfish's own top pick, so
        returning (None, reason, False) degrades the game rather than
        breaking it.
        """
        if not self.api_key:
            return None, "no GEMINI_API_KEY configured for the server", False
        if not self.models:
            return None, "no Gemini models configured", False

        candidate_ucis = [c["move"] for c in candidates]
        payload = {
            "contents": [{"role": "user", "parts": [{"text": self._build_prompt(fen, candidates)}]}]
        }

        last_error = "no models tried"
        for model in self.models:
            url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={self.api_key}"
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    response = await client.post(url, json=payload)
            except Exception as e:
                logger.warning(f"⚠️ Gemini move request to {model} failed: {e}")
                last_error = f"couldn't reach Gemini ({model}): {e}"
                continue

            if response.status_code != 200:
                logger.warning(f"⚠️ Gemini move HTTP {response.status_code} for {model}: {response.text[:200]}")
                last_error = f"HTTP {response.status_code} for {model}"
                continue

            try:
                data = response.json()
                results = data.get("candidates", [])
                if not results:
                    block_reason = data.get("promptFeedback", {}).get("blockReason")
                    if block_reason:
                        return None, f"Gemini declined to respond ({block_reason})", False
                    last_error = f"empty response from {model}"
                    continue
                parts = results[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts).strip()
            except Exception as e:
                logger.warning(f"⚠️ Failed to parse Gemini move response from {model}: {e}")
                last_error = f"couldn't parse response from {model}: {e}"
                continue

            chosen = self._extract_uci_move(text, candidate_ucis)
            if not chosen:
                last_error = f"no candidate move found in {model}'s reply"
                logger.warning(f"⚠️ {last_error}: {text[:150]}")
                continue

            if model != self.models[0]:
                logger.info(f"ℹ️ Gemini move selection succeeded on fallback model {model}")
            return chosen, self._extract_explanation(text), True

        return None, f"All Gemini models are currently unavailable ({last_error})", False


# Global instance, mirroring the gemini_chat_service/stockfish_service pattern.
gemini_move_service = GeminiMoveService()

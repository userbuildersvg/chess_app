"""
gemini_chat_service.py

Mid-game chat: lets you ask the AI about the current position - its
reasoning, what it expects you to play, general commentary - while a game
is in progress.

Deliberately bypasses Langflow entirely. The existing "Chess" flow (see
langflow_service.py) is tuned for one narrow job: given a FEN and a
Stockfish-ranked candidate shortlist, return exactly one UCI move plus a
short explanation. Open-ended conversation ("why not the other rook move",
"what are you worried about here") doesn't fit that shape, and bending the
flow to also handle free-text chat would risk breaking the move-selection
path that already works. So this is a brand new, separate code path: the
backend calls Gemini's REST API directly with httpx (already a project
dependency - see langflow_service.py's own use of it), using the
GEMINI_API_KEY env var (added to the chess-ai-platform service's own
environment in docker-compose.yml - see the `environment:` block).

The "Chess" flow in Langflow is untouched by this file and needs no changes
for the chat feature to work.

Model availability on this project's key turned out to be genuinely
volatile: a single batch of direct curl tests against generateContent, run
seconds apart, came back with gemini-2.5-flash/-pro/-lite all 404
("no longer available to new users"), gemini-3.7-flash and
gemini-flash-latest both 503 ("high demand"), gemini-pro-latest 429, while
gemini-3.6-flash, gemini-3.5-flash, gemini-3.1-flash-lite,
gemini-flash-lite-latest, and gemini-3-flash-preview all returned 200.
Hardcoding any single one of those is fragile - it can flip to
unavailable between one game and the next for reasons that have nothing to
do with this code. So instead of a single model name, GEMINI_CHAT_MODELS
below is a fallback chain, tried in order until one responds successfully -
the same "degrade gracefully instead of hard-failing" philosophy this
project already uses for AI moves (Gemini candidate selection falling back
to Stockfish's own top pick in decide_ai_move(), over in app.py).
"""
import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Ordered fallback chain - confirmed working (HTTP 200 on a direct
# generateContent call) for this project's key as of when this was written.
# Listed roughly strongest/newest first; each one is tried only if every
# model before it in the list failed (any non-200 response, not just
# rate-limiting) for this particular chat request.
GEMINI_CHAT_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3-flash-preview",
]
GEMINI_CHAT_MODEL = GEMINI_CHAT_MODELS[0]
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
REQUEST_TIMEOUT = 30.0

# How many past chat turns (user+model pairs) to carry forward as context.
# Trimmed rather than sent in full so a very long chat during a very long
# game doesn't balloon every request's payload/latency.
MAX_HISTORY_TURNS = 20


class GeminiChatService:
    def __init__(self, api_key: str = GEMINI_API_KEY, models: Optional[list] = None):
        self.api_key = api_key
        self.models = list(models) if models is not None else list(GEMINI_CHAT_MODELS)

    def _build_system_instruction(self, game_context: dict) -> str:
        """
        Grounds every reply in the actual current game state, so answers
        are about *this* position/game rather than chess in general - plus
        whatever the learning service has picked up about this opponent and
        the AI's own track record (see learning_service.get_prompt_context_summary()),
        so the chat can reference real history instead of talking in the
        abstract.
        """
        history_text = ", ".join(game_context.get("move_history_san", [])) or "(no moves played yet)"
        lines = [
            "You are the AI opponent in a chess game, chatting with the human player "
            "mid-game. Answer naturally and concisely - a few sentences, not an essay, "
            "unless they specifically ask for a deep breakdown.",
            f"You are playing {game_context.get('ai_color', 'unknown')}; "
            f"the human is playing {game_context.get('player_color', 'unknown')}.",
            f"Current position (FEN): {game_context.get('fen', 'unknown')}",
            f"Moves played so far (SAN): {history_text}",
            f"Current AI difficulty setting: {game_context.get('difficulty', 'unknown')} "
            f"(higher = stronger play).",
        ]
        last_explanation = game_context.get("last_ai_explanation")
        if last_explanation:
            lines.append(f"Your reasoning for your most recent move was: {last_explanation}")
        if game_context.get("is_game_over"):
            lines.append("The game has already ended - answer accordingly rather than discussing future moves as if it's still your turn.")
        learning_context = game_context.get("learning_context")
        if learning_context:
            lines.append(f"What you've learned about this opponent so far: {learning_context}")
        lines.append(
            "You can discuss your plans, evaluate the position, predict what the human "
            "might play, and explain past moves. Don't reveal a specific winning move you "
            "haven't played yet if it would just hand the human your next move outright - "
            "general plans and evaluations are fine."
        )
        return "\n".join(lines)

    async def send_message(self, message: str, chat_history: list, game_context: dict) -> tuple:
        """
        chat_history: list of {"role": "user" | "model", "text": str}, oldest
        first - NOT including `message` itself yet.
        game_context: dict with fen, move_history_san, player_color, ai_color,
        difficulty, is_game_over, last_ai_source, last_ai_explanation,
        learning_context (see app.py's /api/chat endpoint for how it's built).

        Tries self.models in order, falling through to the next one on any
        non-200 response (model unavailable, overloaded, rate-limited,
        whatever) - see the module docstring for why a single hardcoded
        model turned out not to be reliable enough on its own. A content
        policy block is the one case that returns immediately instead of
        falling through, since a different model isn't going to un-block it.

        Returns (success, reply_or_error_message).
        """
        if not self.api_key:
            return False, "Chat is unavailable - no GEMINI_API_KEY is configured for the server."
        if not self.models:
            return False, "Chat is unavailable - no Gemini models configured."

        trimmed = chat_history[-(MAX_HISTORY_TURNS * 2):] if chat_history else []
        contents = [{"role": turn["role"], "parts": [{"text": turn["text"]}]} for turn in trimmed]
        contents.append({"role": "user", "parts": [{"text": message}]})

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": self._build_system_instruction(game_context)}]},
        }

        last_error = "no models tried"
        for model in self.models:
            url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={self.api_key}"
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    response = await client.post(url, json=payload)
            except Exception as e:
                logger.warning(f"⚠️ Gemini chat request to {model} failed: {e}")
                last_error = f"couldn't reach Gemini ({model}): {e}"
                continue

            if response.status_code != 200:
                error_text = response.text[:300]
                logger.warning(f"⚠️ Gemini chat HTTP {response.status_code} for {model}: {error_text}")
                last_error = f"HTTP {response.status_code} for {model}"
                continue

            try:
                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    block_reason = data.get("promptFeedback", {}).get("blockReason")
                    if block_reason:
                        return False, f"Gemini declined to respond ({block_reason})."
                    last_error = f"empty response from {model}"
                    continue
                parts = candidates[0].get("content", {}).get("parts", [])
                reply = "".join(p.get("text", "") for p in parts).strip()
                if not reply:
                    last_error = f"empty response from {model}"
                    continue
                if model != self.models[0]:
                    logger.info(f"ℹ️ Gemini chat succeeded on fallback model {model} (earlier model(s) unavailable)")
                return True, reply
            except Exception as e:
                logger.warning(f"⚠️ Failed to parse Gemini chat response from {model}: {e}")
                last_error = f"couldn't parse response from {model}: {e}"
                continue

        return False, f"All Gemini models are currently unavailable ({last_error})."


# Global instance, mirroring the learning_service/stockfish_service module-level pattern.
gemini_chat_service = GeminiChatService()

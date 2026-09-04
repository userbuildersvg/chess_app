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
# Ordered by measured reliability, and deliberately NOT led by whatever
# move selection uses. Chat and move selection share one API key, so if
# both lead with the same model they compete for its quota - that is not
# hypothetical: with move selection on gemini-3.5-flash, chat started
# getting HTTP 429 from that model on the very first question. Leading with
# a different model keeps a busy game from starving the coach.
#
# gemini-3.6-flash is last, not first: it does not refuse requests, it
# *hangs*, so leading with it spent the entire request timeout on every
# single message before falling through.
GEMINI_CHAT_MODELS = [
    m.strip() for m in os.environ.get("GEMINI_CHAT_MODELS", "").split(",") if m.strip()
] or [
    "gemini-3.7-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-flash-lite-latest",
    "gemini-3.6-flash",
]
GEMINI_CHAT_MODEL = GEMINI_CHAT_MODELS[0]

# The sandbox coach chat is a FIFTH caller on one API key, alongside move
# selection, real-game chat, narration and scenario generation. It gets its
# own chain, led by a model none of the other four leads with, for exactly the
# reason the others do: when move selection and chat both led with
# gemini-3.5-flash, chat took an HTTP 429 from move traffic on the very first
# question. Learner Mode is worse than the real game for this - a single ply
# can already have a move call and a narration call in flight - so adding a
# third concurrent caller to an existing chain would have been the same
# mistake a second time.
#
# gemini-3.6-flash stays last here too: it does not refuse, it hangs, so
# leading with it spends the whole timeout on every message.
GEMINI_SANDBOX_CHAT_MODELS = [
    m.strip() for m in os.environ.get("GEMINI_SANDBOX_CHAT_MODELS", "").split(",") if m.strip()
] or [
    "gemini-3-flash-preview",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.6-flash",
]
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
# Chat can afford to wait longer than move selection (which has a Stockfish
# move ready to play instead), but not 30s per dead model - that was the
# whole reason a reply took the better part of a minute to arrive.
REQUEST_TIMEOUT = float(os.environ.get("GEMINI_CHAT_TIMEOUT", "15"))

# How many past chat turns (user+model pairs) to carry forward as context.
# Trimmed rather than sent in full so a very long chat during a very long
# game doesn't balloon every request's payload/latency.
MAX_HISTORY_TURNS = 20


# The classifier's whole job, and the reason it is a separate system
# instruction rather than a question asked of the coach: the coach is primed to
# be helpful and conversational, so asked "is this a request for a new
# position?" it tends to answer the chess question instead. This instruction
# forbids prose entirely and allows exactly two tokens.
#
# The bias is deliberately toward ASK. Getting it wrong in the ASK direction
# costs a slightly odd reply; getting it wrong in the BUILD direction proposes
# throwing away the line the student is studying, and sandbox sessions have no
# undo. So anything that could be read as a question about the current position
# is a question.
INTENT_INSTRUCTION = """You classify a single message sent to a chess coaching tool. Answer with exactly one word and nothing else.

BUILD - the user is asking to be GIVEN a new position to study, or to change what is on the board. Examples: "a hard rook endgame as white", "set up the Sicilian Najdorf", "show me a king and pawn ending", "give me something easier", "put me in a losing position".

ASK - anything else, including every question about the position already on the board, its plans, its history, the moves played, chess in general, or the tool itself. Examples: "why not Nf3?", "what should I be looking at here?", "was that a blunder?", "explain the last move", "what is zugzwang?", "who is winning?".

If the message could plausibly be either, answer ASK.

Answer: BUILD or ASK."""


class GeminiChatService:
    def __init__(self, api_key: str = GEMINI_API_KEY, models: Optional[list] = None):
        self.api_key = api_key
        self.models = list(models) if models is not None else list(GEMINI_CHAT_MODELS)
        # Remember which model last answered and try it first next time, so
        # a model that hangs or is rate-limited costs its timeout once per
        # server run rather than once per message.
        self._last_good_model: Optional[str] = None

    def _model_order(self) -> list:
        if self._last_good_model and self._last_good_model in self.models:
            return [self._last_good_model] + [m for m in self.models if m != self._last_good_model]
        return list(self.models)

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

    def _build_sandbox_instruction(self, context: dict) -> str:
        """
        The Learner Mode persona.

        Deliberately not the same voice as the real game's chat. There the
        model is the OPPONENT, and is told to withhold a winning move it has
        not played yet so the chat cannot be used to extract its next move.
        Here it is a COACH working through a position with a student, and
        withholding the answer is the opposite of the job - so that rule is
        absent rather than merely softened.

        It is also given the engine's ranked alternatives when they are to
        hand, which is what lets "why not Nf3?" be answered against what
        Stockfish actually thinks rather than from the model's own opinion.
        This is what the old "Why not?" panel showed as a list; asking in
        prose reaches the same data.

        And it is given the engine's principal variation. Ranked first moves
        alone were not enough: on a position built and verified as a mate in
        two, the coach named the right first move and then invented a second
        that did not mate. Calculating a forced line is the thing a language
        model is least able to do and the thing Stockfish had already done, so
        the line is handed over rather than asked for.
        """
        line_text = ", ".join(context.get("line_san", [])) or "(nothing played yet)"
        lines = [
            "You are a chess coach working through a position with a student in a "
            "practice sandbox. Answer naturally and concisely - a few sentences, not "
            "an essay, unless they ask for a deep breakdown.",
            "This is a teaching sandbox, not a game you are trying to win. Never "
            "withhold a move or an evaluation to avoid giving something away: if the "
            "student asks what the best move is, tell them, and say why.",
            f"Current position (FEN): {context.get('fen', 'unknown')}",
            f"Side to move: {context.get('turn', 'unknown')}",
            f"The line played to reach it (SAN): {line_text}",
            f"Engine strength being demonstrated: {context.get('difficulty', 'unknown')} "
            f"(higher = stronger play).",
        ]
        scenario = context.get("scenario_description")
        if scenario:
            lines.append(f"The student asked to study: {scenario}")
        alternatives = context.get("alternatives")
        if alternatives:
            lines.append(
                "Stockfish's ranking of the legal moves in this position, best first, "
                "with scores from the side to move's point of view - a number in pawns, "
                f"or a distance to forced mate: {alternatives}. "
                "Use these when the student asks why a move was or was not played, and "
                "prefer them to your own guess."
            )
        best_line = context.get("best_line")
        if best_line:
            lines.append(
                "Stockfish's own continuation from here, in SAN, starting with the move "
                f"it ranks first: {best_line}. "
                "When the student asks how a line finishes - how to force the mate, how "
                "to convert the ending - answer FROM THIS LINE rather than calculating "
                "your own. It is the sequence the engine actually searched to produce "
                "the score above. If you give a different move order, you are guessing, "
                "and on a forced mate a guess is simply wrong. Where this line runs out "
                "before the point the student asked about, say that it does rather than "
                "continuing it yourself."
            )
        explanation = context.get("last_explanation")
        if explanation:
            lines.append(f"The reason given for the most recent move was: {explanation}")
        narration = context.get("last_narration")
        if narration:
            lines.append(f"Your last piece of coaching on this line was: {narration}")
        if context.get("is_game_over"):
            lines.append("The line has reached a finished position - no legal moves remain.")
        lines.append(
            "Refer to moves in standard algebraic notation. If the student asks about a "
            "move that is not legal here, say so plainly rather than analysing it."
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
            # The sandbox passes mode="sandbox" and gets the coach persona;
            # everything else keeps the opponent persona it already had.
            "systemInstruction": {"parts": [{"text": (
                INTENT_INSTRUCTION
                if game_context.get("mode") == "intent"
                else self._build_sandbox_instruction(game_context)
                if game_context.get("mode") == "sandbox"
                else self._build_system_instruction(game_context)
            )}]},
        }

        last_error = "no models tried"
        for model in self._model_order():
            url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={self.api_key}"
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    response = await client.post(url, json=payload)
            except Exception as e:
                # Log the exception *type*: httpx timeout exceptions have an
                # empty str(), so this used to read "failed: " with no cause.
                reason = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                logger.warning(f"⚠️ Gemini chat request to {model} failed ({reason})")
                last_error = f"couldn't reach Gemini ({model}): {reason}"
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
                if model != self._model_order()[0]:
                    logger.info(f"ℹ️ Gemini chat succeeded on fallback model {model} (earlier model(s) unavailable)")
                if self._last_good_model != model:
                    logger.info(f"ℹ️ Preferring {model} for subsequent chat messages")
                    self._last_good_model = model
                return True, reply
            except Exception as e:
                logger.warning(f"⚠️ Failed to parse Gemini chat response from {model}: {e}")
                last_error = f"couldn't parse response from {model}: {e}"
                continue

        return False, f"All Gemini models are currently unavailable ({last_error})."


# Global instance, mirroring the learning_service/stockfish_service module-level pattern.
gemini_chat_service = GeminiChatService()

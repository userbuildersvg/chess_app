"""
The structured diagnosis: Gemini interprets, and is not allowed to assert.

THE DIVISION THIS FILE ENFORCES
-------------------------------
`postmortem_analysis.build_evidence` already established every chess fact
about the move under discussion - both FENs, both evaluations, the engine's
preference, its line, the centipawn loss, the grade, and the depth all of that
was searched to. Those are facts, they came from Stockfish, and they are what
the player is shown.

The model's job is the one thing the engine cannot do: say what the player
appears to have been trying to do, what they did not see, and which of eight
named patterns this was. It receives the facts and returns an interpretation.

**It never returns a chess fact, and the validator below is what makes that
true rather than aspirational.** Three rules, all mechanical:

1. `theme` must be one of `learning_loop.THEMES`. Not "close to one" - in it.
   A controlled vocabulary is the entire basis of recurrence (§11 of the
   brief); a model free to coin a ninth theme makes "you have seen this
   before" unfalsifiable.
2. Every move-shaped token in the prose must be **legal in the position the
   move was played from**. A coach may reasonably mention an alternative the
   player could have considered; it may not mention a move that does not
   exist. This is checked against `chess.Board(fen_before)`, not against a
   list we hope is complete.
3. Every evaluation-shaped number in the prose must match one the engine
   actually produced. A model that writes "+2.4" when the engine said +0.9 is
   inventing evidence, and the fact that it sounds right is precisely the
   danger.

A reply that breaks any of them is **rejected, not repaired**. Repairing it
would mean guessing what it meant to say, which is the same failure wearing a
different hat.

WHEN THE MODEL IS UNAVAILABLE
-----------------------------
`fallback_diagnosis` builds a card from the evidence alone: the theme from
what the engine's move actually does, the prose from the taxonomy. It is
marked `source: "engine"` and carries a lower confidence, and the UI says so.
This exists because of the brief's §21 - a trustworthy failure beats a
convincing lie - and because the alternative, a dead end where the player has
already typed what they were trying to do, is the worst moment in the loop to
have nothing to say.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

import chess
import httpx

from learning_loop import THEMES, is_theme

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Its own chain, led by a model nothing else leads with. CLAUDE.md §5: five
# callers already share one key, and a chain whose head is another caller's
# head competes for that model's quota rather than spreading the load.
GEMINI_DIAGNOSIS_MODELS = [
    m.strip() for m in os.environ.get("GEMINI_DIAGNOSIS_MODELS", "").split(",") if m.strip()
] or [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
]

REQUEST_TIMEOUT = float(os.environ.get("GEMINI_DIAGNOSIS_TIMEOUT", "20"))

# Caps. The card is read on a phone next to a chessboard, and a model asked
# for "a sentence" will sometimes write a paragraph.
MAX_DIAGNOSIS_CHARS = 420
MAX_FACTOR_CHARS = 160
MAX_UNCERTAINTY_CHARS = 200


class DiagnosisError(Exception):
    """The model's answer could not be trusted. Carries why, for the log."""


# --- validation -------------------------------------------------------------

# Move-shaped tokens that are unambiguously moves: a piece move, a pawn
# capture, castling, a promotion. Anything matching this and not legal in the
# position is a fabricated move.
_SAN_RE = re.compile(
    r"\b(?:O-O-O|O-O|[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8](?:=[QRBN])?)[+#]?\b"
)

# A bare square - "e4" - is the hard case, and getting it wrong in the obvious
# direction breaks the coach rather than protecting the player. In chess prose
# a bare square is usually a *reference* ("the knight on g8", "the f7 square"),
# and treating every one as a claimed pawn move rejected perfectly good
# writing on the first test run. But "you should have played e5" IS a claimed
# move, and a fabricated one there is exactly what this file exists to catch.
#
# So a bare square counts as a move only when something immediately before it
# says a move is being named. This is the one place the check is heuristic,
# and the gap it leaves is narrow and deliberate: a fabricated pawn move
# phrased without a verb ("e5, and you are fine") passes. Piece moves and
# captures - which is nearly everything a coach names - are caught outright.
_PLAYED_SQUARE_RE = re.compile(
    r"\b(?:play|plays|played|playing|push|pushes|pushed|advance|advances|advanced|"
    r"consider|considered|prefer|prefers|preferred|answer|answered|meet|met|with|after|"
    r"instead of|try|tried)\s+(?:the\s+)?([a-h][1-8](?:=[QRBN])?)[+#]?\b",
    re.I,
)
# Evaluation-shaped numbers: +1.4, -0.35, and "mate in 3".
_EVAL_RE = re.compile(r"[+-]\d+\.\d+")
_MATE_RE = re.compile(r"\bmate in (\d+)\b", re.I)


def _legal_sans(fen: str) -> set:
    """Every legal move in the position, in SAN, with check marks stripped."""
    try:
        board = chess.Board(fen)
    except ValueError:
        return set()
    out = set()
    for move in board.legal_moves:
        san = board.san(move)
        out.add(san)
        out.add(san.rstrip("+#"))
    return out


def _allowed_evals(evidence: dict) -> set:
    """The pawn-unit numbers the engine actually produced for this move."""
    allowed = set()
    for key in ("eval_before", "eval_after"):
        ev = evidence.get(key) or {}
        score = ev.get("score")
        if score is not None:
            # Both roundings, because a model quoting our own number may round
            # it either way and that is not fabrication.
            allowed.add(f"{score / 100:+.1f}")
            allowed.add(f"{score / 100:+.2f}")
    return allowed


def _allowed_mates(evidence: dict) -> set:
    out = set()
    for key in ("eval_before", "eval_after"):
        ev = evidence.get(key) or {}
        mate_in = ev.get("mate_in")
        if mate_in is not None:
            out.add(abs(int(mate_in)))
    return out


def unsupported_claims(text: str, evidence: dict) -> list:
    """
    Everything in `text` that the evidence does not support.

    Empty list means the prose makes no chess claim we cannot trace. This is
    the function the whole module exists for; `validate` is its caller.
    """
    problems = []
    fen_before = evidence.get("fen_before")
    if fen_before:
        legal = _legal_sans(fen_before)
        # The played move is legal in `fen_before` by construction, and the
        # engine's best move is too, so both are already in `legal`.
        named = set(_SAN_RE.findall(text or ""))
        named.update(_PLAYED_SQUARE_RE.findall(text or ""))
        for token in named:
            bare = token.rstrip("+#")
            if bare not in legal and token not in legal:
                problems.append(f"move not legal in this position: {token}")
    allowed = _allowed_evals(evidence)
    for token in set(_EVAL_RE.findall(text or "")):
        if token not in allowed:
            problems.append(f"evaluation the engine did not produce: {token}")
    mates = _allowed_mates(evidence)
    for token in set(_MATE_RE.findall(text or "")):
        if int(token) not in mates:
            problems.append(f"mate claim the engine did not produce: mate in {token}")
    return problems


def validate(raw: dict, evidence: dict) -> dict:
    """
    A model reply, checked and normalised - or `DiagnosisError`.

    Never returns a partially-trusted card. Either every rule above holds, or
    the caller falls back to the deterministic one.
    """
    if not isinstance(raw, dict):
        raise DiagnosisError("reply was not a JSON object")

    theme = raw.get("theme") or raw.get("pattern_theme")
    if not is_theme(theme):
        raise DiagnosisError(f"theme not in the taxonomy: {theme!r}")

    def text(key, cap, required=True):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            if required:
                raise DiagnosisError(f"{key} missing or not a string")
            return None
        return value.strip()[:cap]

    diagnosis = text("diagnosis", MAX_DIAGNOSIS_CHARS)
    missed_factor = text("missed_factor", MAX_FACTOR_CHARS)
    uncertainty = text("uncertainty", MAX_UNCERTAINTY_CHARS, required=False)

    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        raise DiagnosisError("confidence was not a number")
    confidence = max(0.0, min(1.0, confidence))

    problems = []
    for field in (diagnosis, missed_factor, uncertainty):
        if field:
            problems.extend(unsupported_claims(field, evidence))
    if problems:
        raise DiagnosisError("; ".join(sorted(set(problems))[:4]))

    return {
        "theme": theme,
        "diagnosis": diagnosis,
        "missed_factor": missed_factor,
        "uncertainty": uncertainty,
        "confidence": confidence,
        "source": "coach",
    }


# --- the deterministic fallback ---------------------------------------------


def classify_from_evidence(evidence: dict) -> str:
    """
    Which theme the engine's own facts point at, with no model involved.

    Conservative on purpose: three cases it can actually tell apart from the
    position, and `TACTICAL_OVERLOOK` for everything else. It is better for
    the fallback to be vague and right than specific and guessed.
    """
    fen_before = evidence.get("fen_before")
    best_uci = evidence.get("best_move")
    played_uci = evidence.get("uci")
    if not fen_before or not best_uci:
        return "TACTICAL_OVERLOOK"
    try:
        board = chess.Board(fen_before)
        best = chess.Move.from_uci(best_uci)
    except ValueError:
        return "TACTICAL_OVERLOOK"
    if best not in board.legal_moves:
        return "TACTICAL_OVERLOOK"
    if board.gives_check(best):
        return "FORCING_MOVE_MISSED"
    if board.is_capture(best):
        played_was_capture = False
        try:
            played = chess.Move.from_uci(played_uci) if played_uci else None
            played_was_capture = bool(played and played in board.legal_moves
                                      and board.is_capture(played))
        except ValueError:
            pass
        return "CAPTURE_RECALCULATION" if not played_was_capture else "TACTICAL_OVERLOOK"
    return "TACTICAL_OVERLOOK"


def fallback_diagnosis(evidence: dict, intent: str) -> dict:
    """
    A card built from the engine's facts alone, honestly labelled.

    Says less than the coach would and says nothing the evidence does not
    already contain. `source: "engine"` is what the UI keys its "the coach was
    unavailable" line off - the player is never left thinking a model looked
    at this when none did.
    """
    theme = classify_from_evidence(evidence)
    best_san = evidence.get("best_san")
    played_san = evidence.get("san")
    cpl = evidence.get("cpl")
    where = f"{played_san}" if played_san else "this move"
    instead = f" The engine prefers {best_san} here." if best_san else ""
    cost = f" It measures the difference at {cpl} centipawns." if isinstance(cpl, int) and cpl > 0 else ""
    return {
        "theme": theme,
        "diagnosis": (
            f"The coach could not be reached, so this is the engine's reading only. "
            f"{where} was not the strongest choice in this position.{instead}{cost}"
        ),
        "missed_factor": THEMES[theme]["description"],
        "uncertainty": (
            "This was classified from the engine's own move, not from an explanation of your "
            "thinking. Treat the pattern as a starting point rather than a verdict."
        ),
        # Deliberately low. It is a classification from one fact, and the card
        # carries that number into the recurrence count.
        "confidence": 0.3,
        "source": "engine",
    }


# --- the model ---------------------------------------------------------------


def _extract_json(text: str) -> dict:
    """Same tolerance `scenario_service` needs: models fence their JSON."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise DiagnosisError(f"reply was not JSON: {e}")
    raise DiagnosisError("reply contained no JSON object")


def _eval_text(ev) -> str:
    if not ev:
        return "unknown"
    mate_in = ev.get("mate_in")
    if mate_in is not None:
        return "checkmate" if mate_in == 0 else f"mate in {abs(mate_in)} for {'White' if mate_in > 0 else 'Black'}"
    score = ev.get("score")
    return "unknown" if score is None else f"{score / 100:+.2f}"


def build_instruction(evidence: dict, intent: str, prior: Optional[dict]) -> str:
    themes = "\n".join(f"  {key} - {value['description']}" for key, value in THEMES.items())
    quality = (evidence.get("quality") or {}).get("label")
    prior_line = ""
    if prior:
        prior_line = (
            f"\nThis player already has an open correction for {prior['theme']} "
            f"({prior['occurrence_count']} occurrence(s)). If this decision is the same pattern, "
            f"use that same theme. Do not force it if it is genuinely a different pattern.\n"
        )
    return f"""You are a chess coach diagnosing ONE decision from a game the player has already finished. You will be given facts established by Stockfish. Interpret them. Do not add to them.

THE FACTS (from the engine - these are the only chess facts that exist for you)
  Move played: {evidence.get('san')} ({evidence.get('uci')}) by {evidence.get('color')}, ply {evidence.get('ply')}, {evidence.get('phase')}
  Position before: {evidence.get('fen_before')}
  Position after: {evidence.get('fen_after')}
  Evaluation before: {_eval_text(evidence.get('eval_before'))}
  Evaluation after: {_eval_text(evidence.get('eval_after'))}
  Engine's preferred move: {evidence.get('best_san') or 'none recorded'}
  Engine's line from there: {' '.join(evidence.get('pv_san') or []) or 'none recorded'}
  Centipawn loss: {evidence.get('cpl') if evidence.get('cpl') is not None else 'not measured'}
  Grade: {quality or 'not graded'}
  Search depth: {evidence.get('depth')}

WHAT THE PLAYER SAYS THEY WERE TRYING TO DO
  "{intent}"

Take that at face value. It is evidence about their thinking, not a claim about the position. Do not psychoanalyse them, do not describe their personality, and do not speculate about their strength or their habits.
{prior_line}
CHOOSE EXACTLY ONE THEME from this list, by its exact token:
{themes}

HARD RULES
  - Mention only moves that are legal in the position before the move. Any other move will be rejected.
  - Quote only the evaluation numbers given above. Do not compute, estimate or round to a different number.
  - Do not refer to other games, earlier mistakes, or anything you were not told here.
  - If the facts do not support a confident reading, say so in "uncertainty" and lower "confidence".

Reply with ONLY this JSON object and nothing else:
{{
  "theme": "<one token from the list>",
  "missed_factor": "<one short sentence: the thing on the board they did not account for>",
  "diagnosis": "<two or three sentences, addressed to the player as 'you', connecting what they were trying to do to what the position actually required>",
  "confidence": <number between 0 and 1>,
  "uncertainty": "<what would make this reading wrong, or an empty string>"
}}"""


class DiagnosisService:
    def __init__(self, api_key: str = GEMINI_API_KEY, models: Optional[list] = None):
        self.api_key = api_key
        self.models = list(models) if models is not None else list(GEMINI_DIAGNOSIS_MODELS)
        self._last_good_model: Optional[str] = None

    def available(self) -> bool:
        return bool(self.api_key and self.models)

    def _model_order(self) -> list:
        if self._last_good_model and self._last_good_model in self.models:
            return [self._last_good_model] + [m for m in self.models if m != self._last_good_model]
        return list(self.models)

    async def diagnose(self, evidence: dict, intent: str, prior: Optional[dict] = None) -> tuple:
        """
        (ok, payload). `payload` is a validated diagnosis dict, or a reason.

        The caller decides what to do with a failure - `learning_loop_api`
        falls back to `fallback_diagnosis` rather than surfacing an error,
        because by this point the player has already told us what they were
        trying to do and deserves an answer.
        """
        if not self.available():
            return False, "no Gemini API key or model chain configured"

        instruction = build_instruction(evidence, intent, prior)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": "Diagnose this decision."}]}],
            "systemInstruction": {"parts": [{"text": instruction}]},
            # Low temperature: this is a classification with a fixed output
            # shape, not a piece of writing. It also measurably reduces how
            # often the model reaches for a move that is not on the board.
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }

        last_error = "no models tried"
        for model in self._model_order():
            url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={self.api_key}"
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    response = await client.post(url, json=payload)
            except Exception as e:
                reason = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                logger.warning(f"⚠️ Diagnosis request to {model} failed ({reason})")
                last_error = reason
                continue
            if response.status_code != 200:
                logger.warning(f"⚠️ Diagnosis HTTP {response.status_code} for {model}")
                last_error = f"HTTP {response.status_code} for {model}"
                continue
            try:
                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    block = data.get("promptFeedback", {}).get("blockReason")
                    if block:
                        return False, f"Gemini declined to respond ({block})"
                    last_error = f"empty response from {model}"
                    continue
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                result = validate(_extract_json(text), evidence)
            except DiagnosisError as e:
                # The interesting failure: the model answered and the answer
                # could not be trusted. Logged loudly because a rising rate
                # here is the trust metric CLAUDE.md §16 calls the
                # unsupported-claim rate.
                logger.warning(f"🚫 Diagnosis from {model} rejected - {e}")
                last_error = f"rejected: {e}"
                continue
            except Exception as e:
                logger.warning(f"⚠️ Could not read diagnosis from {model}: {e}")
                last_error = f"unreadable reply from {model}: {e}"
                continue
            if self._last_good_model != model:
                logger.info(f"ℹ️ Preferring {model} for subsequent diagnoses")
                self._last_good_model = model
            return True, result

        return False, last_error


diagnosis_service = DiagnosisService()

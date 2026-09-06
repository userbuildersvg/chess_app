"""
HTTP surface for the learning loop: /api/learning-loop/*.

Its own router for the reason `sandbox_api` and `postmortem_api` have theirs -
this is a whole flow, not a section of the game - and it deliberately owns
almost no chess. Every endpoint here is a thin join between three things that
already existed before it:

    postmortem_state / postmortem_analysis   the game, and the engine's
                                             evidence about one decision
    diagnosis_service                        the model's reading of that
                                             evidence, validated
    learning_loop                            the card, and where it lives

NOT `/api/learning`. `app.py` already serves `/api/learning/summary`, which is
the cross-game learning panel and a different feature entirely. Two features
under one prefix is how somebody later "cleans up" one and breaks the other.

WHY THERE IS NO "PLAY THE BETTER MOVE" ENDPOINT
------------------------------------------------
Because Post-Mortem already has one. Trying the engine's move is a branch, and
`POST /api/postmortem/game/{id}/branch` has validated, played and answered
branches since Post-Mortem shipped - with the real legal-move handling, the
real move tree and the real Stockfish-proposes-Gemini-decides reply. Adding a
second path to do the same thing would mean two implementations of "is this
move legal here", which is the one thing the brief was most explicit about not
doing. The loop's UI calls the existing endpoint and this router only records
that it happened (`branch_matched_best`).

OWNERSHIP
---------
Every endpoint resolves the caller through `identity_of(request)` and reads
corrections through `CorrectionStore`, which is **keyed by identity**. There is
no lookup here that takes a correction id without also taking the identity, so
there is no route by which one visitor reads another's card - not because a
check is remembered at each call site, but because the unkeyed call does not
exist. Reviews are reached through `postmortem_api._require`, which already
enforces the same thing and answers a foreign game with the 404 a missing one
gets.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import chess
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import learning_events
import postmortem_analysis
import retest_bank
from diagnosis_service import diagnosis_service, fallback_diagnosis
from identity import identity_of
from learning_loop import STATUSES, THEMES, corrections
from learning_loop import PracticeAttempt
from postmortem_api import _require as require_game
from rate_limit import limit_diagnosis, limit_learning_event, limit_practice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning-loop", tags=["learning-loop"])


# The presets offered before the free-text box. Server-side so the UI cannot
# drift from what the diagnosis prompt is told the options were, and short
# because the question is "what were you trying to do", not a survey.
#
# "I wasn't sure" is on the list on purpose. It is the honest answer often
# enough that leaving it off would push people into picking a plan they did
# not have, which is worse evidence than no answer.
INTENT_PRESETS = [
    {"id": "improve_piece", "label": "Improve a piece"},
    {"id": "attack", "label": "Attack"},
    {"id": "defend", "label": "Defend"},
    {"id": "king_safety", "label": "Improve king safety"},
    {"id": "prepare", "label": "Prepare another move"},
    {"id": "capture", "label": "Capture something"},
    {"id": "unsure", "label": "I wasn't sure"},
]
_PRESET_LABELS = {p["id"]: p["label"] for p in INTENT_PRESETS}

MAX_INTENT_CHARS = 300


# --- reference data ---------------------------------------------------------


@router.get("/themes")
def get_themes():
    """
    The taxonomy, the intent presets, and which themes can actually be
    practised. The UI renders from this rather than from its own copy, so the
    eight themes cannot come to mean one thing here and another there.
    """
    return {
        "themes": [
            {
                "id": key,
                "label": value["label"],
                "description": value["description"],
                "check": value["check"],
                # Honest, and load-bearing for the UI: five of the eight have
                # no certified position. See retest_bank's docstring.
                "practice_available": retest_bank.has_position(key),
            }
            for key, value in THEMES.items()
        ],
        "intent_presets": INTENT_PRESETS,
        "statuses": list(STATUSES),
    }


@router.get("/corrections")
def list_corrections(http: Request):
    """This player's cards. Scoped by identity; there is no unscoped form."""
    identity = identity_of(http)
    cards = corrections.list_for(identity)
    return {
        "corrections": [c.to_dict() for c in cards],
        "count": len(cards),
    }


# --- the diagnosis ----------------------------------------------------------


class DiagnoseRequest(BaseModel):
    game_id: str
    node_id: Optional[str] = None
    intent: str = ""
    intent_preset: Optional[str] = None


def _evidence_for(game, node_id: str) -> dict:
    """
    The engine's packet for the move that produced `node_id`.

    Reuses the scan's result when the scan has already reached this move, and
    otherwise analyses the single ply at full depth - the same function
    Post-Mortem's own "analyse this position" uses. Either way the packet is
    `postmortem_analysis`'s, never assembled here: this module must not become
    a second place that decides what an evaluation is.
    """
    existing = game.analysis_of(node_id)
    if existing:
        return existing

    node = game.tree.nodes.get(node_id)
    if node is None or node.parent_id is None or not node.move:
        raise HTTPException(
            status_code=400,
            detail="There is no move to diagnose here - step to a move you actually played.",
        )
    parent = game.tree.nodes.get(node.parent_id)
    if parent is None:
        raise HTTPException(status_code=400, detail="That move's position is no longer available.")

    evidence = postmortem_analysis.analyse_single_ply(parent.fen, node.move)
    if evidence is None:
        # Stockfish could not answer. Say so; do not invent a packet. The
        # brief's §21: if the engine fails, fabricate nothing.
        raise HTTPException(
            status_code=503,
            detail="The engine could not analyse that move just now. Try again in a moment.",
        )
    evidence["ply"] = game.ply_of(node_id)
    game.record(node_id, evidence)
    return evidence


@router.post("/diagnose", dependencies=[Depends(limit_diagnosis)])
async def diagnose(request: DiagnoseRequest, http: Request):
    """
    Intent in, correction card out.

    The order matters and is the whole point of the loop: the player has
    already said what they were trying to do before the model is asked
    anything, so the diagnosis is answering their account of the decision
    rather than narrating the centipawn drop.
    """
    identity = identity_of(http)
    game = require_game(request.game_id, http)
    node_id = request.node_id or game.tree.current_id

    intent = (request.intent or "").strip()[:MAX_INTENT_CHARS]
    if not intent and request.intent_preset:
        intent = _PRESET_LABELS.get(request.intent_preset, "")
    if not intent:
        raise HTTPException(
            status_code=400,
            detail="Tell the coach what you were trying to do first - that is what it diagnoses against.",
        )

    evidence = _evidence_for(game, node_id)

    learning_events.emit(
        "intent_submitted", identity,
        preset=request.intent_preset, free_text=bool((request.intent or "").strip()),
    )

    # A theme this player already has is passed to the model as context, so a
    # recurrence is recognised as one rather than being filed twice under two
    # near-synonyms. It is context, not an instruction - the prompt says not
    # to force it.
    existing = corrections.list_for(identity)
    prior = None
    if existing:
        newest = existing[0]
        prior = {"theme": newest.theme, "occurrence_count": newest.occurrence_count}

    ok, result = await diagnosis_service.diagnose(evidence, intent, prior)
    if not ok:
        logger.warning(f"⚠️ Diagnosis unavailable ({result}) - falling back to the engine's reading")
        learning_events.emit("diagnosis_fallback", identity, reason=str(result)[:80])
        if "rejected" in str(result):
            learning_events.emit("diagnosis_rejected_unsupported", identity)
        result = fallback_diagnosis(evidence, intent)

    # What the card keeps as its evidence. A copy of the engine's own packet
    # plus where it came from, so "why do you think this" is answerable
    # without holding the review open - reviews expire after an hour idle and
    # a card outlives them.
    stored_evidence = {
        "game_id": game.id,
        "node_id": node_id,
        "source_name": game.source_name,
        "white": game.headers.get("White"),
        "black": game.headers.get("Black"),
        "ply": evidence.get("ply"),
        "san": evidence.get("san"),
        "uci": evidence.get("uci"),
        "color": evidence.get("color"),
        "phase": evidence.get("phase"),
        "fen_before": evidence.get("fen_before"),
        "fen_after": evidence.get("fen_after"),
        "eval_before": evidence.get("eval_before"),
        "eval_after": evidence.get("eval_after"),
        "best_move": evidence.get("best_move"),
        "best_san": evidence.get("best_san"),
        "pv_san": evidence.get("pv_san"),
        "cpl": evidence.get("cpl"),
        "quality": evidence.get("quality"),
        "depth": evidence.get("depth"),
        "intent": intent,
        "diagnosis_source": result.get("source"),
        "at": time.time(),
    }

    card, recurred = corrections.upsert(
        identity=identity,
        theme=result["theme"],
        player_intent=intent,
        missed_factor=result["missed_factor"],
        diagnosis=result["diagnosis"],
        confidence=result["confidence"],
        evidence=stored_evidence,
        uncertainty=result.get("uncertainty"),
    )

    learning_events.emit("diagnosis_viewed", identity, theme=card.theme, source=result.get("source"))
    learning_events.emit(
        "pattern_recurred" if recurred else "correction_created",
        identity, theme=card.theme, occurrences=card.occurrence_count,
    )

    return {
        "correction": card.to_dict(),
        # True only because two diagnoses carried the same controlled token.
        # The UI's "we have seen this before" line hangs off this and nothing
        # softer.
        "recurred": recurred,
        "diagnosis_source": result.get("source"),
        "practice_available": retest_bank.has_position(card.theme),
        # For "now try it yourself": the UI needs to know which move counts as
        # the engine's, so it can tell when the player has played it in a
        # branch. The move is already visible in the Report tab, so this is
        # not a reveal.
        "best_move": evidence.get("best_move"),
        "best_san": evidence.get("best_san"),
        "node_id": node_id,
    }


class StatusRequest(BaseModel):
    status: str


@router.post("/correction/{correction_id}/status")
def set_status(correction_id: str, request: StatusRequest, http: Request):
    """
    The player pushing back, which the doctrine treats as first-class.

    A rejected card is kept, not deleted: "we were wrong about this" and "this
    never happened" are different facts, and only one of them is true.
    """
    identity = identity_of(http)
    if request.status not in STATUSES:
        raise HTTPException(status_code=400, detail="Unknown status.")
    card = corrections.set_status(identity, correction_id, request.status)
    if card is None:
        raise HTTPException(status_code=404, detail="That correction is no longer open.")
    if request.status == "accepted":
        learning_events.emit("diagnosis_accepted", identity, theme=card.theme)
    elif request.status == "rejected":
        learning_events.emit("diagnosis_rejected", identity, theme=card.theme)
    return {"correction": card.to_dict()}


class BranchTriedRequest(BaseModel):
    correction_id: str
    uci: str


@router.post("/branch-tried")
def branch_tried(request: BranchTriedRequest, http: Request):
    """
    Record that the player played a move in the branch, and whether it was the
    engine's. The branch itself went through `/api/postmortem/.../branch`; this
    endpoint touches no chess and decides no legality.
    """
    identity = identity_of(http)
    card = corrections.get(identity, request.correction_id)
    if card is None:
        raise HTTPException(status_code=404, detail="That correction is no longer open.")
    best = (card.evidence[-1] or {}).get("best_move") if card.evidence else None
    matched = bool(best and request.uci == best)
    learning_events.emit("branch_started", identity, theme=card.theme)
    if matched:
        learning_events.emit("branch_matched_best", identity, theme=card.theme)
    return {"matched_best": matched}


# --- the re-test ------------------------------------------------------------


class PracticeStartRequest(BaseModel):
    correction_id: str


@router.post("/practice/start", dependencies=[Depends(limit_practice)])
def practice_start(request: PracticeStartRequest, http: Request):
    """
    One fresh position testing the same concept, or an honest refusal.

    No evaluation, no engine number and no answer travels with it - see
    `retest_bank.public`. The position is chosen deterministically from how
    many attempts this card already has, so practising twice is two different
    positions rather than the same one again.
    """
    identity = identity_of(http)
    card = corrections.get(identity, request.correction_id)
    if card is None:
        raise HTTPException(status_code=404, detail="That correction is no longer open.")

    entry = retest_bank.position_for(card.theme, len(card.attempts))
    if entry is None:
        # Not an error - a fact about this theme. 200 with `available: false`
        # so the UI can say why rather than showing a failure for something
        # that is working exactly as designed.
        return {
            "available": False,
            "reason": (
                "There is no verified practice position for this pattern yet. Positions are only "
                "used here when the engine confirms they have a single best answer, and this "
                "pattern does not have one yet."
            ),
            "theme": card.theme,
        }

    learning_events.emit("practice_started", identity, theme=card.theme, attempt=len(card.attempts))
    return {
        "available": True,
        "theme": card.theme,
        "position": retest_bank.public(entry),
        "attempt_index": len(card.attempts),
        # The one check the card is testing, restated. It is the taxonomy's
        # sentence, so it is the same every time this theme comes round.
        "check": THEMES[card.theme]["check"],
    }


class PracticeAttemptRequest(BaseModel):
    correction_id: str
    uci: str
    hints_used: int = 0
    response_ms: Optional[int] = None


@router.post("/practice/attempt", dependencies=[Depends(limit_practice)])
def practice_attempt(request: PracticeAttemptRequest, http: Request):
    """
    Grade one attempt against the bank's certified answer and record it.

    The move is checked for legality against the position before anything else
    happens - not because the UI would send an illegal one, but because a
    client is not a source of truth about chess and this endpoint is reachable
    without one.
    """
    identity = identity_of(http)
    card = corrections.get(identity, request.correction_id)
    if card is None:
        raise HTTPException(status_code=404, detail="That correction is no longer open.")

    entry = retest_bank.position_for(card.theme, len(card.attempts))
    if entry is None:
        raise HTTPException(status_code=400, detail="There is no practice position for this pattern.")

    board = chess.Board(entry["fen"])
    try:
        move = chess.Move.from_uci(request.uci)
    except ValueError:
        raise HTTPException(status_code=400, detail="That is not a move.")
    if move not in board.legal_moves:
        raise HTTPException(status_code=400, detail="That move is not legal in this position.")

    passed = retest_bank.is_correct(entry, request.uci)
    attempt = PracticeAttempt(
        correction_id=card.id,
        fen=entry["fen"],
        played_uci=request.uci,
        expected=[entry["best_uci"]],
        passed=passed,
        hints_used=max(0, min(3, request.hints_used)),
        response_ms=request.response_ms,
    )
    corrections.record_attempt(identity, card.id, attempt)
    learning_events.emit(
        "practice_passed" if passed else "practice_failed",
        identity, theme=card.theme, hints=attempt.hints_used,
    )

    return {
        "passed": passed,
        # Revealed only now, after the answer is in. Before this point the
        # client has never held it.
        "best_san": entry["best_san"],
        "best_uci": entry["best_uci"],
        "played_san": board.san(move),
        "correction": card.to_dict(),
    }


class HintRequest(BaseModel):
    correction_id: str


@router.post("/practice/hint", dependencies=[Depends(limit_practice)])
def practice_hint(request: HintRequest, http: Request):
    """
    One hint: what kind of move it is, never which move.

    Derived from the position rather than written per entry, so it cannot
    disagree with the answer. It is recorded on the attempt, because hint
    dependence is one of the learning metrics that makes a pass mean less.
    """
    identity = identity_of(http)
    card = corrections.get(identity, request.correction_id)
    if card is None:
        raise HTTPException(status_code=404, detail="That correction is no longer open.")
    entry = retest_bank.position_for(card.theme, len(card.attempts))
    if entry is None:
        raise HTTPException(status_code=400, detail="There is no practice position for this pattern.")

    board = chess.Board(entry["fen"])
    move = chess.Move.from_uci(entry["best_uci"])
    piece = board.piece_at(move.from_square)
    names = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
             chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}
    parts = []
    if board.gives_check(move):
        parts.append("it gives check")
    if board.is_capture(move):
        parts.append("it is a capture")
    if piece is not None:
        parts.append(f"it is a {names.get(piece.piece_type, 'piece')} move")
    learning_events.emit("hint_used", identity, theme=card.theme)
    return {"hint": "The move you are looking for: " + ", and ".join(parts) + "."}


# --- instrumentation --------------------------------------------------------


class EventRequest(BaseModel):
    name: str
    theme: Optional[str] = None


@router.post("/events", dependencies=[Depends(limit_learning_event)])
def post_event(request: EventRequest, http: Request):
    """
    The UI's half of the funnel - the steps the server cannot see, such as a
    player opening a turning point.

    Deliberately narrow: a name from a fixed list and an optional theme. There
    is no free-form property bag, because an open sink is how a player's typed
    words end up somewhere nobody meant to put them.
    """
    accepted = learning_events.emit(request.name, identity_of(http), theme=request.theme)
    if not accepted:
        raise HTTPException(status_code=400, detail="Unknown event.")
    return {"recorded": True}


@router.get("/funnel")
def funnel(http: Request):
    """
    The counts, for whoever is looking at whether the loop works.

    Process-wide totals only - no per-player breakdown, no identities, and
    nothing a player typed. It answers "did anyone get from a diagnosis to a
    passed re-test", which is the question the funnel exists for.
    """
    return {"counts": learning_events.events.counts()}

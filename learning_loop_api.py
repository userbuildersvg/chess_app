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

import asyncio
import logging
import time
from typing import Optional

import chess
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import correction_history
import db
import learning_events
from move_grade_audit import audits as move_grade_audits
import move_feedback_log
import postmortem_analysis
import retest_bank
from diagnosis_service import diagnosis_service, fallback_diagnosis
from identity import account_id_of, identity_of
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

PRACTICE_UNAVAILABLE = {
    "missing_snapshot": "This correction is missing the verified position snapshot needed for practice.",
    "no_single_best_answer": "The engine did not verify a single answer for this position, so no practice was created.",
    "no_verified_position": "The saved position or engine move could not be verified, so no practice was created.",
}


def _cards_for(identity: str) -> list[dict]:
    account_id = account_id_of(identity)
    if account_id is not None:
        return correction_history.list_for(account_id)
    return [{**c.to_dict(), "saved_to_account": False,
             "practice_available": retest_bank.has_position(c.theme),
             "practice_unavailable_reason": None if retest_bank.has_position(c.theme)
             else "no_single_best_answer"} for c in corrections.list_for(identity)]


def _card_for(identity: str, correction_id: str) -> dict | None:
    account_id = account_id_of(identity)
    if account_id is not None:
        return correction_history.get(account_id, correction_id)
    card = corrections.get(identity, correction_id)
    return None if card is None else {**card.to_dict(), "saved_to_account": False,
                                      "practice_available": retest_bank.has_position(card.theme),
                                      "practice_unavailable_reason": None}


def _practice_basis(identity: str, game, evidence: dict, theme: str) -> dict:
    """
    The practice target is the card's own evidence packet - the same FEN and
    the same engine move the card shows as "Engine preferred" - or nothing.

    It used to prefer the profile worker's finding for the same imported-game
    ply when one existed. Same position, but a separate Stockfish run, and at
    the same depth two runs do disagree: Barry's audit found a card saying
    Ng5 whose practice (and hint - "a pawn move") tested d3. One source of
    truth, so the card, the prompt, the hint and the grader cannot drift; the
    profile row is linked afterwards by `link_finding`, never consulted here.
    """
    fen, best = evidence.get("fen_before"), evidence.get("best_move")
    source = (getattr(game, "import_source", None)
              or ("play" if getattr(game, "origin", None) == "play" else "manual"))
    if not fen:
        return {"available": False, "reason": "missing_snapshot", "source": source}
    if not best:
        return {"available": False, "reason": "no_single_best_answer", "source": source}
    try:
        board, move = chess.Board(fen), chess.Move.from_uci(best)
        if not board.is_valid() or move not in board.legal_moves:
            raise ValueError
        return {"available": True, "fen": fen, "best_uci": best,
                "best_san": evidence.get("best_san") or board.san(move), "source": source}
    except (ValueError, TypeError):
        return {"available": False, "reason": "no_verified_position", "source": source}


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
    cards = _cards_for(identity)
    return {"corrections": cards, "count": len(cards)}


# --- the diagnosis ----------------------------------------------------------


class DiagnoseRequest(BaseModel):
    game_id: str
    node_id: Optional[str] = None
    intent: str = ""
    intent_preset: Optional[str] = None


async def _evidence_for(game, node_id: str) -> tuple[dict, int]:
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
        return existing, 0

    node = game.tree.nodes.get(node_id)
    if node is None or node.parent_id is None or not node.move:
        raise HTTPException(
            status_code=400,
            detail="There is no move to diagnose here - step to a move you actually played.",
        )
    parent = game.tree.nodes.get(node.parent_id)
    if parent is None:
        raise HTTPException(status_code=400, detail="That move's position is no longer available.")

    engine_started = time.monotonic()
    evidence = await asyncio.to_thread(
        postmortem_analysis.analyse_single_ply, parent.fen, node.move
    )
    engine_ms = round((time.monotonic() - engine_started) * 1000)
    if evidence is None:
        # Stockfish could not answer. Say so; do not invent a packet. The
        # brief's §21: if the engine fails, fabricate nothing.
        raise HTTPException(
            status_code=503,
            detail="The engine could not analyse that move just now. Try again in a moment.",
        )
    evidence["ply"] = game.ply_of(node_id)
    game.record(node_id, evidence)
    return evidence, engine_ms


# One diagnosis per person per decision, however many times the button is
# pressed.
#
# The browser already guards the double click (CorrectionPanel holds an
# in-flight ref), but a guard that lives only in one client is a guard that a
# reload, a second tab or a slow network can walk around - and each way round
# it starts another model chain for a card that is already being written.
#
# The key is deliberately narrow: identity, game, node AND the intent itself.
# Pressing the same button twice is the same question and shares one answer;
# saying something DIFFERENT about the same move is a different question and
# gets its own diagnosis, which is the whole premise of the loop.
#
# Nothing is cached: the entry lives only while the request is in flight and
# is dropped as soon as it settles, so this is de-duplication rather than a
# store, and no answer is ever served from it twice.
_diagnosis_in_flight: dict[tuple, "asyncio.Future"] = {}


@router.post("/diagnose", dependencies=[Depends(limit_diagnosis)])
async def diagnose(request: DiagnoseRequest, http: Request):
    """
    Intent in, correction card out.

    The order matters and is the whole point of the loop: the player has
    already said what they were trying to do before the model is asked
    anything, so the diagnosis is answering their account of the decision
    rather than narrating the centipawn drop.

    Identical submissions that arrive while one is still running share it
    (see `_diagnosis_in_flight`) instead of each starting a model chain.
    """
    key = (
        identity_of(http),
        request.game_id,
        request.node_id or "",
        request.intent_preset or "",
        (request.intent or "").strip()[:MAX_INTENT_CHARS],
    )
    running = _diagnosis_in_flight.get(key)
    if running is not None and not running.done():
        logger.info("🔁 Diagnosis already in flight for this decision - joining it")
        return await asyncio.shield(running)

    task = asyncio.ensure_future(_diagnose_once(request, http))
    _diagnosis_in_flight[key] = task
    try:
        return await task
    finally:
        # Only while it runs. Nothing is kept, so nothing can be served stale.
        if _diagnosis_in_flight.get(key) is task:
            _diagnosis_in_flight.pop(key, None)


async def _diagnose_once(request: DiagnoseRequest, http: Request):
    """The diagnosis itself. One caller: `diagnose` above."""
    identity = identity_of(http)
    game = require_game(request.game_id, http)
    node_id = request.node_id or game.tree.current_id
    request_started = time.monotonic()

    intent = (request.intent or "").strip()[:MAX_INTENT_CHARS]
    if not intent and request.intent_preset:
        intent = _PRESET_LABELS.get(request.intent_preset, "")
    if not intent:
        raise HTTPException(
            status_code=400,
            detail="Tell the coach what you were trying to do first - that is what it diagnoses against.",
        )

    learning_events.emit(
        "player_intention_submitted", identity, game_id=game.id, node_id=node_id,
        source_mode="Post-Mortem", preset=request.intent_preset,
        free_text=bool((request.intent or "").strip()),
    )
    learning_events.emit(
        "correction_generation_started", identity, game_id=game.id,
        node_id=node_id, source_mode="Post-Mortem",
    )
    needs_engine = game.analysis_of(node_id) is None
    if needs_engine:
        learning_events.emit(
            "engine_analysis_started", identity, game_id=game.id, node_id=node_id,
            source_mode="Post-Mortem", operation="correction_evidence",
        )
    try:
        evidence, engine_ms = await _evidence_for(game, node_id)
    except HTTPException as exc:
        if needs_engine:
            learning_events.emit(
                "engine_analysis_completed", identity, game_id=game.id, node_id=node_id,
                source_mode="Post-Mortem", operation="correction_evidence",
                duration_ms=round((time.monotonic() - request_started) * 1000),
                completed=False, error_category="engine_evidence",
            )
        learning_events.emit(
            "correction_flow_error", identity, game_id=game.id, node_id=node_id,
            source_mode="Post-Mortem", operation="correction_generation",
            duration_ms=round((time.monotonic() - request_started) * 1000),
            error_code=str(exc.status_code), error_category="engine_evidence",
        )
        raise
    if needs_engine:
        learning_events.emit(
            "engine_analysis_completed", identity, game_id=game.id, node_id=node_id,
            source_mode="Post-Mortem", operation="correction_evidence",
            duration_ms=engine_ms, depth=evidence.get("depth"), completed=True,
        )
    if engine_ms > 0:
        move_feedback_log.log_grade(
            mode="Post-Mortem",
            fen_before=evidence["fen_before"],
            move_uci=evidence["uci"],
            fen_after=evidence.get("fen_after"),
            san=evidence.get("san"),
            player_color=evidence.get("color"),
            quality=evidence.get("quality"),
            identity=identity,
            game_id=game.id,
        )

    # A theme this player already has is passed to the model as context, so a
    # recurrence is recognised as one rather than being filed twice under two
    # near-synonyms. It is context, not an instruction - the prompt says not
    # to force it.
    existing = _cards_for(identity)
    prior = None
    if existing:
        newest = existing[0]
        prior = {"theme": newest["theme"], "occurrence_count": newest["occurrence_count"]}

    learning_events.emit(
        "llm_request_started", identity, game_id=game.id, node_id=node_id,
        source_mode="Post-Mortem", operation="correction_diagnosis",
    )
    llm_started = time.monotonic()
    ok, result = await diagnosis_service.diagnose(evidence, intent, prior)
    llm_ms = round((time.monotonic() - llm_started) * 1000)
    diagnosis_timeout = False
    if not ok:
        diagnosis_timeout = "timeout" in str(result).lower()
        logger.warning(f"⚠️ Diagnosis unavailable ({result}) - falling back to the engine's reading")
        learning_events.emit(
            "diagnosis_fallback", identity, game_id=game.id, node_id=node_id,
            source_mode="Post-Mortem", error_category="model_unavailable",
        )
        if "rejected" in str(result):
            learning_events.emit("diagnosis_rejected_unsupported", identity)
        result = fallback_diagnosis(evidence, intent)

    learning_events.emit(
        "llm_request_completed", identity, game_id=game.id, node_id=node_id,
        source_mode="Post-Mortem", operation="correction_diagnosis",
        duration_ms=llm_ms, provider=result.get("_provider") or "gemini",
        model=result.get("_model"), fallback=not ok, timeout=diagnosis_timeout,
        completed=ok,
    )

    # What the card keeps as its evidence. A copy of the engine's own packet
    # plus where it came from, so "why do you think this" is answerable
    # without holding the review open - reviews expire after an hour idle and
    # a card outlives them.
    stored_evidence = {
        "game_id": game.id,
        "node_id": node_id,
        "source_name": game.source_name,
        # Where the game came from, when Review opened it from the imported
        # library (profile_api). None for a dropped file or a Play handoff.
        "import_source": getattr(game, "import_source", None),
        "source_username": getattr(game, "source_username", None),
        "imported_game_id": getattr(game, "imported_game_id", None),
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

    practice = _practice_basis(identity, game, evidence, result["theme"])
    account_id = account_id_of(identity)
    card = None
    save_failed = False
    if account_id is not None:
        try:
            card, recurred = correction_history.upsert(
                account_id, theme=result["theme"], player_intent=intent,
                missed_factor=result["missed_factor"], diagnosis=result["diagnosis"],
                confidence=result["confidence"], evidence=stored_evidence, practice=practice,
                uncertainty=result.get("uncertainty"),
            )
        except Exception as exc:
            # The database being away must not cost the student the card they
            # just earned. Keep it in memory for this session, say so honestly
            # ("Could not save - retry"), and let the retry re-run the save.
            if not db.is_transient(exc):
                raise
            logger.warning(f"⚠️ Correction could not be saved to the account, keeping it in memory: {exc}")
            save_failed = True
    if card is None:
        memory_card, recurred = corrections.upsert(
            identity=identity, theme=result["theme"], player_intent=intent,
            missed_factor=result["missed_factor"], diagnosis=result["diagnosis"],
            confidence=result["confidence"], evidence=stored_evidence,
            uncertainty=result.get("uncertainty"),
        )
        card = {**memory_card.to_dict(), "saved_to_account": False, "save_failed": save_failed,
                "practice_available": retest_bank.has_position(memory_card.theme),
                "practice_unavailable_reason": None if retest_bank.has_position(memory_card.theme)
                else "no_single_best_answer"}

    provider = result.get("_provider") or ("stockfish" if result.get("source") == "engine" else None)
    model = result.get("_model")
    move_grade_audits.link_correction(
        identity=identity,
        game_id=game.id,
        fen_before=evidence.get("fen_before") or "",
        move_played=evidence.get("uci") or "",
        correction_id=card["id"],
        provider=provider,
        model=model,
    )

    learning_events.emit(
        "correction_generated", identity, theme=card["theme"], game_id=game.id,
        correction_id=card["id"], node_id=node_id, source_mode="Post-Mortem",
        ply_index=evidence.get("ply"), side_to_move=evidence.get("color"),
        player_color=evidence.get("color"), depth=evidence.get("depth"),
        engine_ms=engine_ms, llm_ms=llm_ms,
        duration_ms=round((time.monotonic() - request_started) * 1000),
        provider=provider, model=model, fallback=result.get("source") == "engine",
        timeout=diagnosis_timeout,
        fresh_practice_generated=card["practice_available"], completed=True,
    )
    if recurred:
        learning_events.emit(
            "pattern_recurred", identity, theme=card["theme"], game_id=game.id,
            correction_id=card["id"], source_mode="Post-Mortem",
            occurrences=card["occurrence_count"],
        )
    finding_id = _file_imported_evidence(identity, game, card["theme"], card["id"], evidence, result)
    if account_id is not None and finding_id is not None:
        correction_history.link_finding(account_id, card["id"], finding_id)

    return {
        "correction": card,
        # True only because two diagnoses carried the same controlled token.
        # The UI's "we have seen this before" line hangs off this and nothing
        # softer.
        "recurred": recurred,
        "diagnosis_source": result.get("source"),
        "practice_available": card["practice_available"],
        "practice_unavailable_reason": card.get("practice_unavailable_reason"),
        # For "now try it yourself": the UI needs to know which move counts as
        # the engine's, so it can tell when the player has played it in a
        # branch. The move is already visible in the Report tab, so this is
        # not a reveal.
        "best_move": evidence.get("best_move"),
        "best_san": evidence.get("best_san"),
        "node_id": node_id,
    }


def _file_imported_evidence(identity, game, theme: str, correction_id: str,
                            evidence, result) -> int | None:
    """
    A correction made on an imported game is evidence on the account's
    improvement profile, tagged with the game it came from and, through that
    row, the site it came from. Only for reviews opened from the library;
    a dropped file has no row to file against. Never raises - the card was
    already made, and the person is looking at it.
    """
    imported_id = getattr(game, "imported_game_id", None)
    if not imported_id:
        return None
    try:
        import pattern_detectors
        import profile_service
        cpl = evidence.get("cpl")
        finding_id = profile_service.record_correction_evidence(
            identity, int(imported_id),
            ply=int(evidence.get("ply") or 0), theme=theme,
            severity=pattern_detectors.severity_of(cpl if isinstance(cpl, (int, float)) else 0),
            cpl=cpl if isinstance(cpl, (int, float)) else None,
            fen_before=evidence.get("fen_before") or "", move_san=evidence.get("san") or "",
            best_san=evidence.get("best_san"), phase=evidence.get("phase") or "unknown",
            confidence=result.get("confidence"),
        )
        if finding_id:
            learning_events.emit(
                "improvement_profile_evidence_added", identity, theme=theme,
                game_id=game.id, correction_id=correction_id, source_mode="Post-Mortem",
                import_source=getattr(game, "import_source", None),
                ply_index=evidence.get("ply"),
            )
        return finding_id
    except Exception as exc:  # pragma: no cover - instrumentation must not break the card
        logger.warning(f"⚠️ Could not file correction evidence against imported game: {exc}")
        return None


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
    account_id = account_id_of(identity)
    card = None
    if account_id is not None:
        card = correction_history.set_status(account_id, correction_id, request.status)
    # A card the account could not save (see diagnose) lives in memory.
    if card is None:
        memory_card = corrections.set_status(identity, correction_id, request.status)
        card = None if memory_card is None else {
            **memory_card.to_dict(), "saved_to_account": False,
            "practice_available": retest_bank.has_position(memory_card.theme),
            "practice_unavailable_reason": None,
        }
    if card is None:
        raise HTTPException(status_code=404, detail="That correction is no longer open.")
    if request.status == "accepted":
        learning_events.emit(
            "diagnosis_accepted", identity, theme=card["theme"],
            correction_id=card["id"], source_mode="Post-Mortem",
        )
    elif request.status == "rejected":
        learning_events.emit(
            "diagnosis_disagreed", identity, theme=card["theme"],
            correction_id=card["id"], source_mode="Post-Mortem",
        )
    return {"correction": card}


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
    card = _card_for(identity, request.correction_id)
    if card is None:
        raise HTTPException(status_code=404, detail="That correction is no longer open.")
    best = (card["evidence"][-1] or {}).get("best_move") if card["evidence"] else None
    matched = bool(best and request.uci == best)
    evidence = card["evidence"][-1] if card["evidence"] else {}
    learning_events.emit(
        "alternative_move_played", identity, theme=card["theme"],
        game_id=evidence.get("game_id"), correction_id=card["id"],
        node_id=evidence.get("node_id"), source_mode="Post-Mortem",
        ply_index=evidence.get("ply"), side_to_move=evidence.get("color"),
        player_color=evidence.get("color"), matched_best=matched,
    )
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
    started = time.monotonic()
    account_id = account_id_of(identity)
    if account_id is not None:
        card = correction_history.get(account_id, request.correction_id)
        if card is None:
            raise HTTPException(status_code=404, detail="That correction is no longer open.")
        practice, session_id = correction_history.start_practice(account_id, request.correction_id)
        if practice is None:
            raise HTTPException(status_code=404, detail="That correction is no longer open.")
        if not practice["available"]:
            learning_events.emit(
                "fresh_practice_opened", identity, theme=card["theme"],
                correction_id=card["id"], source_mode="Post-Mortem",
                duration_ms=round((time.monotonic() - started) * 1000),
                fresh_practice_generated=False, completed=False,
            )
            return {"available": False,
                    "reason": PRACTICE_UNAVAILABLE.get(practice["reason"], PRACTICE_UNAVAILABLE["no_verified_position"]),
                    "reason_code": practice["reason"], "theme": card["theme"]}
        learning_events.emit(
            "fresh_practice_opened", identity, theme=card["theme"],
            correction_id=card["id"], source_mode="Post-Mortem",
            duration_ms=round((time.monotonic() - started) * 1000),
            fresh_practice_generated=True, completed=True, attempt=practice["attempt_index"],
        )
        return {
            "available": True, "theme": card["theme"],
            "position": {"fen": practice["fen"],
                         "prompt": "Find the engine-verified move from this position in your game.",
                         "from_your_game": True},
            "attempt_index": practice["attempt_index"], "check": THEMES[card["theme"]]["check"],
            "practice_session_id": session_id,
        }

    card = corrections.get(identity, request.correction_id)
    if card is None:
        raise HTTPException(status_code=404, detail="That correction is no longer open.")

    entry = retest_bank.position_for(card.theme, len(card.attempts))
    if entry is None:
        # Not an error - a fact about this theme. 200 with `available: false`
        # so the UI can say why rather than showing a failure for something
        # that is working exactly as designed.
        learning_events.emit(
            "fresh_practice_opened", identity, theme=card.theme,
            correction_id=card.id, source_mode="Post-Mortem",
            duration_ms=round((time.monotonic() - started) * 1000),
            fresh_practice_generated=False, completed=False,
        )
        return {
            "available": False,
            "reason": PRACTICE_UNAVAILABLE["no_single_best_answer"],
            "reason_code": "no_single_best_answer",
            "theme": card.theme,
        }

    learning_events.emit(
        "fresh_practice_opened", identity, theme=card.theme,
        correction_id=card.id, source_mode="Post-Mortem",
        duration_ms=round((time.monotonic() - started) * 1000),
        fresh_practice_generated=True, completed=True, attempt=len(card.attempts),
    )
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
    account_id = account_id_of(identity)
    if account_id is not None:
        card = correction_history.get(account_id, request.correction_id)
        practice = correction_history.practice_for(account_id, request.correction_id)
        if card is None or practice is None:
            raise HTTPException(status_code=404, detail="That correction is no longer open.")
        if not practice["available"]:
            raise HTTPException(status_code=400, detail="There is no verified practice position for this correction.")
        board = chess.Board(practice["fen"])
        try:
            move = chess.Move.from_uci(request.uci)
        except ValueError:
            raise HTTPException(status_code=400, detail="That is not a move.")
        if move not in board.legal_moves:
            raise HTTPException(status_code=400, detail="That move is not legal in this position.")
        passed = request.uci == practice["best_uci"]
        repeated = request.uci == practice.get("played_move")
        outcome = "passed" if passed else "repeated" if repeated else "failed"
        hints = max(0, min(3, request.hints_used))
        card = correction_history.record_attempt(
            account_id, request.correction_id, practice, played_uci=request.uci,
            passed=passed, outcome=outcome, hints_used=hints, response_ms=request.response_ms,
        )
        learning_events.emit(
            "practice_move_attempted", identity, theme=card["theme"], correction_id=card["id"],
            source_mode="Post-Mortem", outcome=outcome, hint_used=hints > 0,
            duration_ms=request.response_ms, completed=True,
        )
        learning_events.emit(
            "practice_completed", identity, theme=card["theme"], correction_id=card["id"],
            source_mode="Post-Mortem", outcome=outcome, hint_used=hints > 0,
            duration_ms=request.response_ms, completed=True,
        )
        if passed:
            learning_events.emit(
                "correction_card_completed", identity, theme=card["theme"],
                correction_id=card["id"], source_mode="Post-Mortem",
                hint_used=hints > 0, completed=True,
            )
        return {"passed": passed, "repeated_mistake": repeated,
                "best_san": practice["best_san"], "best_uci": practice["best_uci"],
                "played_san": board.san(move), "correction": card}

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
        "practice_move_attempted", identity, theme=card.theme,
        correction_id=card.id, source_mode="Post-Mortem", outcome="passed" if passed else "failed",
        hint_used=attempt.hints_used > 0, duration_ms=attempt.response_ms,
        completed=True,
    )
    learning_events.emit(
        "practice_completed", identity, theme=card.theme,
        correction_id=card.id, source_mode="Post-Mortem",
        outcome="passed" if passed else "failed", hint_used=attempt.hints_used > 0,
        duration_ms=attempt.response_ms, completed=True,
    )
    if passed:
        learning_events.emit(
            "correction_card_completed", identity, theme=card.theme,
            correction_id=card.id, source_mode="Post-Mortem",
            hint_used=attempt.hints_used > 0, completed=True,
        )

    return {
        "passed": passed,
        # Revealed only now, after the answer is in. Before this point the
        # client has never held it.
        "best_san": entry["best_san"],
        "best_uci": entry["best_uci"],
        "played_san": board.san(move),
        # Shaped like every other card the guest is handed (_card_for): a
        # bare to_dict() carried no `practice_available`, so after one
        # attempt the browser's card lost its Practise button and said no
        # fresh test could be made.
        "correction": _card_for(identity, card.id),
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
    account_id = account_id_of(identity)
    if account_id is not None:
        card = correction_history.get(account_id, request.correction_id)
        practice = correction_history.practice_for(account_id, request.correction_id)
        if card is None or practice is None:
            raise HTTPException(status_code=404, detail="That correction is no longer open.")
        if not practice["available"]:
            raise HTTPException(status_code=400, detail="There is no verified practice position for this correction.")
        entry = {"fen": practice["fen"], "best_uci": practice["best_uci"]}
        theme, correction_id = card["theme"], card["id"]
    else:
        memory_card = corrections.get(identity, request.correction_id)
        if memory_card is None:
            raise HTTPException(status_code=404, detail="That correction is no longer open.")
        entry = retest_bank.position_for(memory_card.theme, len(memory_card.attempts))
        if entry is None:
            raise HTTPException(status_code=400, detail="There is no practice position for this pattern.")
        theme, correction_id = memory_card.theme, memory_card.id

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
    learning_events.emit(
        "hint_requested", identity, theme=theme,
        correction_id=correction_id, source_mode="Post-Mortem",
    )
    return {"hint": "The move you are looking for: " + ", and ".join(parts) + "."}


# --- instrumentation --------------------------------------------------------


class EventRequest(BaseModel):
    name: str
    theme: Optional[str] = None
    game_id: Optional[str] = None
    correction_id: Optional[str] = None
    node_id: Optional[str] = None
    ply_index: Optional[int] = None
    source_mode: str = "Post-Mortem"
    duration_ms: Optional[int] = None
    operation: Optional[str] = None
    outcome: Optional[str] = None
    error_code: Optional[str] = None
    error_category: Optional[str] = None
    completed: Optional[bool] = None
    hint_used: Optional[bool] = None
    # Guided Play's toggle events (guided_play.py) say what strength the
    # player was facing when they reached for coaching.
    opponent_profile: Optional[str] = None
    approx_elo: Optional[int] = None
    guided: Optional[bool] = None
    # The Play -> Review handoff's own metadata: how the game ended and from
    # which seat. Never the moves.
    result: Optional[str] = None
    termination: Optional[str] = None
    player_color: Optional[str] = None
    total_moves: Optional[int] = None


@router.post("/events", dependencies=[Depends(limit_learning_event)])
def post_event(request: EventRequest, http: Request):
    """
    The UI's half of the funnel - the steps the server cannot see, such as a
    player opening a turning point.

    Deliberately narrow: every accepted field is declared above and the sink
    independently allow-lists them. There is no free-form property bag, PGN,
    intent, filename, or chat field.
    """
    accepted = learning_events.emit(
        request.name,
        identity_of(http),
        **request.model_dump(exclude={"name"}, exclude_none=True),
    )
    if not accepted:
        raise HTTPException(status_code=400, detail="Unknown event.")
    return {"recorded": True}


@router.get("/funnel")
def funnel(http: Request, days: int = 30):
    """
    The counts, for whoever is looking at whether the loop works.

    Aggregate totals only - no per-player identifiers and nothing a player
    typed. Persistent when Postgres is configured; bounded in-memory in dev.
    """
    summary = learning_events.events.summary(days)
    # `counts` remains for older internal tooling; it is the same canonical
    # funnel data returned in the richer summary.
    return {"counts": summary["funnel"], **summary}

"""
The background scan: imported games in, findings out.

WHY THIS IS THE RISKY PART, AND WHAT MAKES IT SAFE
--------------------------------------------------
Three facts about this deployment shape every decision in this file, and all
three were measured rather than assumed:

1. **There is one Stockfish process behind one lock.** Every search in the app
   queues behind every other one, so a bulk scan that does not let go starves
   the live game. A person playing a move must never wait for somebody's
   fifteen-game import.
2. **A whole-game scan costs real time.** 0.16s per position at depth 12
   locally - about 13s for a 40-move game - and roughly three times that on a
   throttled instance. Fifteen games is around ten minutes of engine.
3. **The free instance sleeps after ~15 minutes idle.** Anything held only in
   this process dies mid-run.

So: **one game at a time, yielding between every ply, with all state in
Postgres.**

The yield is the important one. `await asyncio.sleep(PLY_PAUSE)` between plies
means a live request waits behind at most ONE position rather than behind the
whole backlog - the difference between a move that feels normal and a move that
times out. It makes the scan slower in wall-clock terms and that is the trade
being made deliberately: this work is not urgent and the game in front of
somebody is.

Point 3 is handled by `profile_service.requeue_stuck()` at boot rather than by
anything here. A row left `analysing` belongs to a process that no longer
exists.

WHY A LOOP AND NOT A QUEUE LIBRARY
----------------------------------
The same reasoning as the retention sweep in app.py. What has to happen is
"analyse the oldest pending game", the state is already durable in a database
this app already has, and there is exactly one instance. Celery, RQ or an
external broker would add an operational dependency to replace a `while True`
and a table.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re

import chess
import chess.pgn

import pattern_detectors
import postmortem_analysis
import profile_service
from postmortem_state import phase_of

logger = logging.getLogger(__name__)

# Between plies. Small enough that a scan still progresses at a sensible rate,
# long enough that the engine lock is genuinely released and a waiting live
# request gets it.
PLY_PAUSE = 0.05

# Between games, and when the queue is empty. An idle instance should be idle:
# polling an empty table every 50ms would keep a free dyno awake doing nothing.
GAME_PAUSE = 1.0
IDLE_PAUSE = 20.0

# A guard against one absurd file eating the worker. Games longer than this are
# analysed up to the limit rather than refused - the first 300 plies of a
# 400-ply game is still a real analysis.
MAX_PLIES = 300

# `[%clk 0:01:23]` and `[%clk 0:01:23.4]`, which is how every site that exports
# clock times writes them.
_CLK = re.compile(r"\[%clk\s+(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)\]")


def clock_seconds_from_comment(comment: str):
    """Seconds left on the mover's clock, from a PGN comment, or None.

    None is the normal case - most PGNs carry no clock at all - and it must
    stay distinguishable from zero, because `TIME_PRESSURE` is only ever
    claimed on a measurement that exists.
    """
    if not comment:
        return None
    m = _CLK.search(comment)
    if not m:
        return None
    hours, minutes, seconds = m.groups()
    try:
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None


def analyse_game(pgn: str, player_color: str, depth: int | None = None) -> list:
    """
    Every finding in one game, for the side the account played.

    Synchronous and engine-bound: the caller runs it on a thread. It walks the
    game once, evaluating each position exactly once and reusing the previous
    ply's evaluation as this ply's "before" - so a 40-move game costs 81
    searches rather than 160.

    **Only the account's own moves are examined.** A finding about the
    opponent's blunder is not a fact about the person, and counting it would
    make the profile a description of the fields they play in rather than of
    them.
    """
    depth = depth or postmortem_analysis.SCAN_DEPTH
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("That game could not be read as PGN.")

    mine = chess.WHITE if player_color == "white" else chess.BLACK
    board = game.board()
    before = postmortem_analysis.evaluate_position(board.fen(), depth=depth)

    findings = []
    for ply, node in enumerate(game.mainline(), start=1):
        if ply > MAX_PLIES:
            break
        move = node.move
        mover_is_me = board.turn == mine
        san = board.san(move)
        board.push(move)
        after = postmortem_analysis.evaluate_position(board.fen(), depth=depth)

        if mover_is_me:
            evidence = postmortem_analysis.build_evidence(ply, before, after, move, san)
            evidence["phase"] = phase_of(ply)
            clock = clock_seconds_from_comment(node.comment or "")
            if clock is not None:
                evidence["clock_seconds"] = clock
            found = pattern_detectors.detect(evidence)
            if found is not None:
                findings.append(found)

        before = after
    return findings


async def analyse_game_async(pgn: str, player_color: str, depth: int | None = None) -> list:
    """
    `analyse_game`, but letting go of the engine between plies.

    The scan is re-implemented here rather than wrapping the sync version in
    one `to_thread` call, because a single thread hop would hold the engine for
    the whole game - which is exactly the starvation this file exists to avoid.
    Each position is its own hop, with a pause between.
    """
    depth = depth or postmortem_analysis.SCAN_DEPTH
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("That game could not be read as PGN.")

    mine = chess.WHITE if player_color == "white" else chess.BLACK
    board = game.board()
    before = await asyncio.to_thread(postmortem_analysis.evaluate_position, board.fen(), depth)

    findings = []
    for ply, node in enumerate(game.mainline(), start=1):
        if ply > MAX_PLIES:
            break
        move = node.move
        mover_is_me = board.turn == mine
        san = board.san(move)
        board.push(move)
        after = await asyncio.to_thread(postmortem_analysis.evaluate_position, board.fen(), depth)
        # The whole point of this function. A live move now queues behind one
        # position, not behind the rest of somebody's library.
        await asyncio.sleep(PLY_PAUSE)

        if mover_is_me:
            evidence = postmortem_analysis.build_evidence(ply, before, after, move, san)
            evidence["phase"] = phase_of(ply)
            clock = clock_seconds_from_comment(node.comment or "")
            if clock is not None:
                evidence["clock_seconds"] = clock
            found = pattern_detectors.detect(evidence)
            if found is not None:
                findings.append(found)

        before = after
    return findings


async def run_once() -> bool:
    """
    Analyse one pending game if there is one. True if work was done.

    Every failure marks the row `failed` with a reason rather than leaving it
    `analysing`, because a row nobody will ever pick up again is a queue that
    silently stops - and the person waiting on it has no way to tell that from
    "still going".
    """
    claimed = await asyncio.to_thread(profile_service.claim_next_pending)
    if claimed is None:
        return False

    game_id = claimed["id"]
    logger.info(f"🧩 Analysing imported game {game_id} for {claimed['owner']}")
    try:
        findings = await analyse_game_async(claimed["pgn"], claimed["player_color"])
    except Exception as e:
        logger.warning(f"⚠️ Imported game {game_id} could not be analysed: {e}")
        await asyncio.to_thread(profile_service.mark_failed, game_id,
                                "That game could not be analysed.")
        return True

    try:
        await asyncio.to_thread(profile_service.record_findings, game_id,
                                claimed["owner"], findings)
        logger.info(f"🧩 Game {game_id}: {len(findings)} finding(s)")
    except Exception as e:
        logger.warning(f"⚠️ Could not record findings for game {game_id}: {e}")
        await asyncio.to_thread(profile_service.mark_failed, game_id,
                                "The analysis finished but could not be saved.")
    return True


async def loop() -> None:
    """
    Forever: take a game, analyse it, take the next one.

    Nothing in here is allowed to raise. The task is started once at boot and
    never restarted, so an exception escaping this loop would end bulk analysis
    for the life of the process, and the only symptom would be imports that sit
    at "pending" with no error against them.
    """
    while True:
        try:
            did_work = await run_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"⚠️ Profile worker iteration failed: {e}")
            did_work = False
        await asyncio.sleep(GAME_PAUSE if did_work else IDLE_PAUSE)


__all__ = [
    "GAME_PAUSE",
    "IDLE_PAUSE",
    "MAX_PLIES",
    "PLY_PAUSE",
    "analyse_game",
    "analyse_game_async",
    "clock_seconds_from_comment",
    "loop",
    "run_once",
]

"""
The candidate bracket: from Stockfish's ranked list to the handful of moves
a player of the chosen profile might actually consider.

This replaced `select_candidates_by_difficulty` in app.py, which slid a
3-move window along the ranked list: level 20 was the top three, level 1
the bottom three. The bottom of a ranked list is not a weak player, it is a
random one - the three moves that hang the queen, walk into mate and
abandon the king - and a tester who met it said so.

The pipeline here, all python-chess and arithmetic, no engine:

  annotate()     every ranked move gets facts: centipawn loss against the
                 best, whether it checks or captures, whether it hangs a
                 piece, whether it answers a threat to one of its own
                 pieces, whether it walks into mate, whether it is forced.
  select_pool()  the profile turns those facts into a pool of pool_size
                 moves: a cpl ceiling decides what is *eligible*, a
                 blunder tolerance decides whether the hanging/mated moves
                 stay, and a softmax AROUND the profile's typical cpl -
                 bent toward checks and captures for weak profiles, away
                 from quiet defence for the weakest - decides the weights
                 the pool is sampled by. Each pool entry carries its
                 weight; the deterministic fallback plays the heaviest.
  with_reference() / strip_reference()
                 if the engine's best move was not sampled it rides along
                 to the deep search as a hidden reference so the cpl that
                 is recorded for the chosen move is deep-accurate, then is
                 removed before anything reaches Gemini or the UI.

Deterministic given an injected `random.Random`; the tests use seeds and
count outcomes over hundreds of draws rather than asserting one.

Stage 2 (`stockfish_service.refine_candidates`) keeps every key added here
on the refined entries, so the decision record app.py writes can say what
rank the chosen move held and what it cost.
"""
import math
import random

import chess

from opponent_profiles import LOW_PROFILE_IDS, get_profile

# A forced mate expressed on the centipawn scale: mate in n is worth
# MATE_CP - n, so a faster mate scores higher and any mate outscores any
# centipawn evaluation. Losing mates mirror it below zero.
MATE_CP = 10000

_VAL = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
MINOR = 3


def cp_value(entry: dict) -> int:
    """One number for an engine entry, mates included. Unscored sorts last."""
    m = entry.get("mate_in")
    if m is not None:
        return (MATE_CP - abs(m)) if m > 0 else -(MATE_CP - abs(m))
    s = entry.get("score")
    return int(s) if s is not None else -MATE_CP


def _loose_pieces(board: chess.Board, color: bool) -> list:
    """`color`'s non-king pieces attacked by the other side and defended by nobody."""
    out = []
    for sq, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        if board.is_attacked_by(not color, sq) and not board.is_attacked_by(color, sq):
            out.append((sq, piece))
    return out


def annotate(fen: str, ranked: list) -> list:
    """
    Facts for every entry of a stage-1 ranked list, in the same order.

    Each returned dict is the input entry plus: rank, san, cpl, is_capture,
    is_promotion, gives_check, hangs_piece, answers_threat, walks_into_mate,
    forced, is_reference (always False here).
    """
    if not ranked:
        return []
    board = chess.Board(fen)
    legal_count = board.legal_moves.count()
    best_cp = cp_value(ranked[0])
    best_mated = (ranked[0].get("mate_in") or 0) < 0
    mover = board.turn
    loose_before = {sq for sq, _ in _loose_pieces(board, mover)}
    out = []
    for rank, entry in enumerate(ranked):
        move = chess.Move.from_uci(entry["move"])
        c = dict(entry)
        c["rank"] = rank
        c["cpl"] = max(0, best_cp - cp_value(entry))
        c["forced"] = legal_count == 1
        c["is_reference"] = False
        c["san"] = board.san(move)
        c["is_capture"] = board.is_capture(move)
        c["is_promotion"] = move.promotion is not None
        c["gives_check"] = board.gives_check(move)
        moved_value = _VAL.get(board.piece_at(move.from_square).piece_type, 0)

        after = board.copy(stack=False)
        after.push(move)
        loose_after = _loose_pieces(after, mover)
        loose_after_sq = {sq for sq, _ in loose_after}
        landed_loose = move.to_square in loose_after_sq
        # Hanging: a minor piece or better of ours can now be taken for free -
        # either the piece we moved, or one we left behind (a pawn left loose
        # is a detail, not a blunder, and would flag half of all openings).
        c["hangs_piece"] = (landed_loose and moved_value >= MINOR) or any(
            sq != move.to_square and _VAL.get(p.piece_type, 0) >= MINOR for sq, p in loose_after
        )
        # Answering a threat: something of ours was en prise before the move
        # and nothing of ours (that piece included, wherever it went) is
        # en prise after it.
        still_loose = loose_after_sq & (loose_before - {move.from_square})
        c["answers_threat"] = bool(loose_before) and not still_loose and not landed_loose
        c["walks_into_mate"] = (entry.get("mate_in") or 0) < 0 and not best_mated
        out.append(c)
    return out


def _captures_pawn(board: chess.Board, c: dict) -> bool:
    target = board.piece_at(chess.Move.from_uci(c["move"]).to_square)
    return target is not None and target.piece_type == chess.PAWN


def select_pool(fen: str, ranked: list, profile, rng: random.Random = None) -> tuple:
    """
    The moves Gemini may choose from, for this profile, in ranked order.

    Returns (pool, info). `pool` is a subset of annotate()'s output, at most
    profile.pool_size long and never empty when `ranked` is not; each entry
    carries its sampling "weight". `info` is {"n_legal", "n_eligible",
    "saw_mate", "weights": {uci: weight}} for the decision record and the
    sanity tool.
    """
    profile = get_profile(profile)
    rng = rng or random.Random()
    cands = annotate(fen, ranked)
    info = {"n_legal": len(cands), "n_eligible": 0, "weights": {}}
    if not cands:
        return [], info
    k = profile.pool_size
    best = cands[0]
    if best["forced"]:
        info["n_eligible"] = 1
        return cands[:1], info

    # 1. Seeing the mate. When the best move mates, every other move is
    #    thousands of centipawns worse and a softmax over cpl would never
    #    offer one - so a beginner could never miss a mate in one, which is
    #    the single most beginner thing there is. Strong profiles always see
    #    it; a weaker one sees it with probability tactical_awareness, and
    #    when it does not, the mating moves are simply not on its board and
    #    the rest are judged against the best of what remains.
    saw_mate = True
    if (best.get("mate_in") or 0) > 0 and profile.id not in ("master", "expert"):
        if rng.random() >= profile.tactical_awareness:
            rest = [c for c in cands if not (c.get("mate_in") or 0) > 0]
            if rest:
                saw_mate = False
                rebase = min(c["cpl"] for c in rest)
                cands = [dict(c, cpl=c["cpl"] - rebase) for c in rest]
    info["saw_mate"] = saw_mate

    # 2. Eligible: within the profile's cpl ceiling. A pool of one is a
    #    real answer (one clearly best move is common); only when NOTHING
    #    is within the ceiling - every move loses, or a short endgame list
    #    of bad choices - are the least bad k eligible instead, so that the
    #    pool is never empty.
    eligible = [c for c in cands if c["cpl"] <= profile.max_cpl]
    if not eligible:
        eligible = sorted(cands, key=lambda c: c["cpl"])[:k]

    # 3. Blunders: hanging a piece or walking into mate survives with
    #    probability blunder_tolerance. Walking into mate is a beginner's
    #    mistake only. Never filter below a pool's worth: restore the least
    #    bad of what was dropped.
    kept, dropped = [], []
    for c in eligible:
        if c["walks_into_mate"] and profile.id not in ("beginner", "casual"):
            dropped.append(c)
            continue
        if (c["hangs_piece"] or c["walks_into_mate"]) and rng.random() >= profile.blunder_tolerance:
            dropped.append(c)
            continue
        kept.append(c)
    need = min(k, len(eligible))
    if len(kept) < need:
        kept += sorted(dropped, key=lambda c: c["cpl"])[:need - len(kept)]
        kept.sort(key=lambda c: c["rank"])
    info["n_eligible"] = len(kept)

    # 4. Weights. Softmax around the profile's typical cpl, then three human bends:
    #    - checks, non-pawn captures and threat answers are "obvious"; weak
    #      players are drawn to them beyond their worth (x3 at awareness 0,
    #      x1 at awareness 1);
    #    - a QUIET threat answer is exactly what weak players miss;
    #    - the engine's own top move, when it is not obvious, is what the
    #      weakest players do not find.
    board = chess.Board(fen)
    weights = {}
    for c in kept:
        w = math.exp(-abs(c["cpl"] - profile.target_cpl) / profile.temperature)
        obvious = (c["gives_check"] or (c["is_capture"] and not _captures_pawn(board, c))
                   or c["answers_threat"])
        if obvious:
            w *= 1 + 2 * (1 - profile.tactical_awareness)
        if c["answers_threat"] and not c["gives_check"] and not c["is_capture"] and profile.id in LOW_PROFILE_IDS:
            w *= profile.tactical_awareness
        if c["rank"] == 0 and not obvious and profile.id in ("beginner", "casual"):
            w *= profile.tactical_awareness
        weights[c["move"]] = max(w, 1e-9)
        c["weight"] = weights[c["move"]]
    info["weights"] = weights

    # 5. A seen mate is always in the pool; the prompt tells Gemini to play it.
    pool = []
    if saw_mate and (best.get("mate_in") or 0) > 0 and best in kept:
        pool.append(best)

    # 6. Weighted sampling without replacement for the rest.
    remaining = [c for c in kept if c is not best or not pool]
    while len(pool) < k and remaining:
        total = sum(weights[c["move"]] for c in remaining)
        r = rng.random() * total
        picked = None
        for i, c in enumerate(remaining):
            r -= weights[c["move"]]
            if r <= 0:
                picked = remaining.pop(i)
                break
        if picked is None:
            picked = remaining.pop()
        pool.append(picked)
    pool.sort(key=lambda c: c["rank"])
    return pool, info


def with_reference(pool: list, annotated: list) -> list:
    """`pool` plus the engine's best move as a hidden reference, if absent."""
    if not annotated or any(c["rank"] == 0 for c in pool):
        return list(pool)
    ref = dict(annotated[0])
    ref["is_reference"] = True
    return list(pool) + [ref]


def strip_reference(cands: list) -> list:
    return [c for c in cands if not c.get("is_reference")]

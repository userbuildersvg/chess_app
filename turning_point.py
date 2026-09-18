"""
"Where did I start losing?" - answered from the scan, never from the model.

    is_question(text)                      -> bool
    select(evidence, player_color)         -> {"status": "clear"|"unclear"|"none", "turning_point", "candidates", ...}
    fallback_text(answer)                  -> a complete deterministic reply

The engine picks the move; Gemini only explains it (postmortem_api.chat hands
`select()`'s answer to the prompt as facts). Evidence rows are the scan's own
`build_evidence` dicts with `node_id` / `node_before_id` attached by the
caller, so every FEN, eval and preferred move here was measured, not recalled.

Selection: score = centipawn loss, plus a bonus when the move changed the
evaluation band (winning -> equal, equal -> worse, ...) and a smaller one when
the mover never recovered the band it held before. Book and forced moves are
not decisions and are excluded. With the user's colour known only their moves
compete; without it the biggest swing of either side is reported and the
answer says so. "Clear" means the winner both cost real material (>= 100cp)
and stands apart from the runner-up; anything closer is "unclear" and the
reply lists up to three candidates instead of pretending.
"""
from __future__ import annotations

import re
from typing import Optional

_QUESTION = re.compile(
    r"\b(?:where|when|what|which|at what point)\b.{0,40}?\b(?:"
    r"start(?:ed)?\s+(?:losing|to lose|going wrong|to go wrong)"
    r"|(?:lose|lost|throw|threw|blow|blew|squander(?:ed)?|drop(?:ped)?)\s+(?:the|my|our)?\s*(?:advantage|edge|lead|game|win)"
    r"|(?:advantage|edge|lead)\s+(?:disappear|vanish|evaporate|go|went|slip)"
    r"|(?:change|changed|decide|decided|turn|turned|lose|lost)\s+the\s+game|the game turn"
    r"|(?:go|went|going)\s+wrong"
    r"|turning[- ]point|critical moment|key decision|losing move|decisive (?:mistake|error|blunder)"
    r")"
    r"|\bwhy did (?:i|we) lose(?:\s+(?:this|the|that))?\s*(?:game)?\s*[?.!]*$"
    r"|\bturning[- ]point\b",
    re.IGNORECASE,
)


# Mover's-frame bands, in centipawns.
_BANDS = [(150, "winning"), (50, "better"), (-50, "equal"), (-150, "worse")]
CLEAR_MIN_CPL = 100        # a "turning point" that cost less than a pawn is drift
CLEAR_GAP = 0.6            # runner-up must score under 60% of the winner to call it clean
CANDIDATES = 3


def is_question(text: str) -> bool:
    return bool(_QUESTION.search(text or ""))


def _mover_cp(evaluation: Optional[dict], color: str) -> Optional[int]:
    """White-frame {score, mate_in} -> centipawns from the mover's side."""
    if not evaluation:
        return None
    mate = evaluation.get("mate_in")
    if mate == 0:  # checkmate on the board: the mover delivered it
        return 10000
    if mate is not None:
        cp = 10000 if mate > 0 else -10000
    else:
        cp = evaluation.get("score")
        if cp is None:
            return None
    return cp if color == "white" else -cp


def band(cp: Optional[int]) -> Optional[str]:
    if cp is None:
        return None
    for floor, name in _BANDS:
        if cp >= floor:
            return name
    return "losing"


def _band_rank(name: Optional[str]) -> int:
    order = ["losing", "worse", "equal", "better", "winning"]
    return order.index(name) if name in order else 2


def _candidate(e: dict, later: list[dict]) -> dict:
    color = e["color"]
    before_cp = _mover_cp(e.get("eval_before"), color)
    after_cp = _mover_cp(e.get("eval_after"), color)
    b_before, b_after = band(before_cp), band(after_cp)
    cpl = e.get("cpl") or 0
    band_drop = max(0, _band_rank(b_before) - _band_rank(b_after))
    # Never recovered: no later position of the same mover reaches the band
    # held before this move. Measured on the scan's own curve.
    recovered = any(
        _band_rank(band(_mover_cp(x.get("eval_after"), color))) >= _band_rank(b_before)
        for x in later if x["color"] == color
    )
    sustained = band_drop > 0 and not recovered
    score = cpl + 100 * band_drop + (50 if sustained else 0)
    reasons = [f"cost {cpl} centipawns against the engine's best"]
    if band_drop:
        reasons.append(f"took the position from {b_before} to {b_after}")
    if sustained:
        reasons.append("and it never got back")
    return {
        "ply": e["ply"],
        "move_number": (e["ply"] + 1) // 2,
        "san": e["san"],
        "uci": e.get("uci"),
        "color": color,
        "node_id": e.get("node_id"),
        "node_before_id": e.get("node_before_id"),
        "fen_before": e.get("fen_before"),
        "fen_after": e.get("fen_after"),
        "eval_before": e.get("eval_before"),
        "eval_after": e.get("eval_after"),
        "cpl": cpl,
        "best_san": e.get("best_san"),
        "best_move": e.get("best_move"),
        "pv_san": e.get("pv_san") or [],
        "grade": (e.get("quality") or {}).get("name"),
        "band_before": b_before,
        "band_after": b_after,
        "sustained": sustained,
        "score": score,
        "reason": ", ".join(reasons),
    }


def select(evidence: list[dict], player_color: Optional[str]) -> dict:
    graded = [
        e for e in evidence
        if (e.get("quality") or {}).get("label") not in (None, "book", "forced") and (e.get("cpl") or 0) > 0
    ]
    pool = [e for e in graded if e["color"] == player_color] if player_color in ("white", "black") else graded
    ordered = sorted(evidence, key=lambda e: e["ply"])
    cands = sorted(
        (_candidate(e, [x for x in ordered if x["ply"] > e["ply"]]) for e in pool),
        key=lambda c: (-c["score"], c["ply"]),
    )
    base = {"player_color": player_color, "analysed_moves": len(evidence)}
    if not cands:
        return {**base, "status": "none", "turning_point": None, "candidates": [],
                "caveat": "No move of yours lost ground against the engine in the analysed moves."}
    top = cands[0]
    runner = cands[1]["score"] if len(cands) > 1 else 0
    clear = top["cpl"] >= CLEAR_MIN_CPL and runner < top["score"] * CLEAR_GAP
    caveat = None if player_color else "Your colour is not recorded for this game, so this is the biggest swing by either side."
    if clear:
        return {**base, "status": "clear", "turning_point": top, "candidates": cands[:CANDIDATES],
                "confidence": "high" if top["cpl"] >= 200 else "medium", "caveat": caveat}
    return {**base, "status": "unclear", "turning_point": None, "candidates": cands[:CANDIDATES],
            "confidence": "low",
            "caveat": caveat or "No single move decided it; the evaluation slipped across several close decisions."}


def _label(c: dict) -> str:
    return f"{c['move_number']}{'.' if c['color'] == 'white' else '...'} {c['san']}"


def _eval_text(evaluation: Optional[dict]) -> str:
    if not evaluation:
        return "unknown"
    mate = evaluation.get("mate_in")
    if mate is not None:
        return "checkmate" if mate == 0 else f"mate in {abs(mate)} for {'White' if mate > 0 else 'Black'}"
    score = evaluation.get("score")
    return "unknown" if score is None else f"{score / 100:+.2f}"


def _loss_text(c: dict) -> str:
    """Centipawn loss as words; a mate allowed is not "100 pawns"."""
    mate = (c.get("eval_after") or {}).get("mate_in")
    if mate is not None or c["cpl"] >= 1000:
        return "it allowed a forced mate" if mate is not None else "it was decisive"
    return f"it cost about {c['cpl'] / 100:.1f} pawns against the engine's best"


def fallback_text(answer: dict) -> str:
    """A complete, honest reply with no model in the loop."""
    if answer["status"] == "none":
        return answer["caveat"]
    if answer["status"] == "clear":
        tp = answer["turning_point"]
        parts = [
            f"The turning point was {_label(tp)}. Before it the position was {tp['band_before']} for you "
            f"({_eval_text(tp['eval_before'])}); after it, {tp['band_after']} ({_eval_text(tp['eval_after'])}). "
            f"{_loss_text(tp).capitalize()}."
        ]
        if tp["best_san"]:
            parts.append(f"The engine preferred {tp['best_san']}.")
        if answer.get("caveat"):
            parts.append(answer["caveat"])
        return " ".join(parts)
    lines = ["I do not see one clean losing move. The game drifted through several smaller decisions:"]
    for c in answer["candidates"]:
        best = f" (engine preferred {c['best_san']})" if c["best_san"] else ""
        lines.append(f"- {_label(c)}: {_loss_text(c)}{best}")
    if answer.get("caveat"):
        lines.append(answer["caveat"])
    return "\n".join(lines)


def prompt_lines(answer: dict) -> list[str]:
    """What the coach may say about the turning point - facts only, from the scan."""
    lines = [
        "The student asked where the game turned. The ENGINE has already answered that "
        "from the whole-game scan; your job is to explain the facts below, not to pick a "
        "different move, invent an evaluation, or name a preferred move that is not listed. "
        "Quote the move, the evaluation before and after, and the engine's preferred move exactly."
    ]
    if answer["status"] == "none":
        lines.append(f"Finding: {answer['caveat']} Say exactly that, briefly.")
        return lines
    if answer["status"] == "clear":
        tp = answer["turning_point"]
        lines.append(
            f"Turning point: {_label(tp)}, graded {tp['grade'] or 'unknown'}; {_loss_text(tp)}. "
            f"Evaluation before: {_eval_text(tp['eval_before'])}; after: {_eval_text(tp['eval_after'])} "
            "(pawns from White's point of view - positive favours White, negative favours Black; say it that way round). "
            f"For the mover that is {tp['band_before']} -> {tp['band_after']}"
            + (" and the position never recovered." if tp["sustained"] else ".")
        )
        if tp["best_san"]:
            lines.append(f"Engine preferred {tp['best_san']}" + (f", continuing {' '.join(tp['pv_san'])}" if tp["pv_san"] else "") + ".")
        lines.append(f"Position before the move (FEN): {tp['fen_before']}")
        lines.append(f"Confidence: {answer.get('confidence')}. Keep it to three or four sentences, past tense, and offer to look at the position.")
    else:
        lines.append("Finding: no single clean losing move - the evaluation slipped over several decisions. "
                     "Say so first, then list these candidates and nothing else:")
        for c in answer["candidates"]:
            lines.append(f"- {_label(c)}: {_loss_text(c)}, {c['band_before']} -> {c['band_after']}"
                         + (f", engine preferred {c['best_san']}" if c["best_san"] else ""))
    if answer.get("caveat"):
        lines.append(f"Caveat to state: {answer['caveat']}")
    return lines

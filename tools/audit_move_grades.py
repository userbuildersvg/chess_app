#!/usr/bin/env python3
"""Re-grade a small stored sample at greater Stockfish depth.

The beta grade is never overwritten. The output is an audit artifact comparing
the original label/evaluation with a fresh deeper search. Run 20-50 positions
before making stronger coaching-accuracy claims.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
import move_quality  # noqa: E402
from stockfish_service import stockfish_service  # noqa: E402


def stored(limit: int, key_only: bool) -> list[dict]:
    where = "WHERE key_decision = true" if key_only else ""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, game_id, correction_id, source_flow, occurred_at, fen_before, "
            "move_played, fen_after, side_to_move, player_color, engine_best, eval_before, "
            "eval_after, eval_perspective, engine_depth, grade_assigned, key_decision, "
            "provider, model, fallback, timeout FROM move_grade_audits "
            f"{where} ORDER BY occurred_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
    keys = (
        "audit_id", "game_id", "correction_id", "source_flow", "timestamp",
        "fen_before", "move_played", "fen_after", "side_to_move", "player_color",
        "engine_best_move", "eval_before", "eval_after", "eval_perspective",
        "engine_depth", "beta_grade", "key_decision", "provider", "model",
        "fallback", "timeout",
    )
    return [dict(zip(keys, row)) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit stored Zugzwang move grades")
    parser.add_argument("--limit", type=int, default=20, choices=range(1, 51), metavar="1-50")
    parser.add_argument("--depth", type=int, default=20, help="Deeper Stockfish depth")
    parser.add_argument("--all-moves", action="store_true", help="Include non-key-decision records")
    parser.add_argument("--export-only", action="store_true", help="Export stored records without re-analysis")
    args = parser.parse_args()

    if not db.configured():
        parser.error("DATABASE_URL is not set; no persistent audit records are available")

    records = stored(args.limit, not args.all_moves)
    disagreements = 0
    for record in records:
        if args.export_only:
            continue
        deeper = move_quality.classify_move(
            record["fen_before"], record["move_played"], depth=args.depth
        )
        record["deeper_engine_depth"] = args.depth
        record["deeper_grade"] = (deeper or {}).get("label")
        record["deeper_eval_before"] = (deeper or {}).get("eval_before")
        record["deeper_eval_after"] = (deeper or {}).get("eval_after")
        record["deeper_cpl"] = (deeper or {}).get("cpl")
        beta_cpl = None
        if record["eval_before"] is not None and record["eval_after"] is not None:
            beta_cpl = max(0, record["eval_before"] - record["eval_after"])
        record["beta_cpl"] = beta_cpl
        record["cpl_delta"] = (
            None if beta_cpl is None or not deeper
            else deeper.get("cpl") - beta_cpl
        )
        record["label_agrees"] = bool(
            deeper and deeper.get("label") == record["beta_grade"]
        )
        disagreements += int(not record["label_agrees"])

    output = {
        "sample_size": len(records),
        "deeper_depth": None if args.export_only else args.depth,
        "label_disagreements": None if args.export_only else disagreements,
        "records": records,
    }
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    stockfish_service.close()
    db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

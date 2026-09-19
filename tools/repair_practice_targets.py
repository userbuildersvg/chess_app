#!/usr/bin/env python3
"""
One-off: make every saved correction's practice target its own engine move.

Why this exists: until the fix in `learning_loop_api._practice_basis`, a card
made on an imported game could take its practice target from the profile
worker's finding for the same ply - a separate Stockfish run that sometimes
disagreed with the card's own evidence. The card then said "Engine preferred:
Ng5" while practice (and its hint) tested d3. New cards cannot drift; this
repairs the ones already saved.

    set -a; . ./.env; set +a                                   # DATABASE_URL
    /tmp/chessapp/bin/python tools/repair_practice_targets.py          # dry run
    /tmp/chessapp/bin/python tools/repair_practice_targets.py --apply  # for real

Touches only `practice_expected_uci` and `practice_expected_san`, and only on
rows where practice is available and the card's `preferred_move` /
`preferred_san` are non-empty. FEN, diagnosis, profile links and the evidence
packet are left alone. No schema change. One transaction. Safe to re-run.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import chess  # noqa: E402

import data_keys  # noqa: E402
import db  # noqa: E402

APPLY = "--apply" in sys.argv[1:]

FIND = """
    SELECT e.id, e.correction_id, e.imported_game_id, e.review_id, e.ply,
           e.preferred_move, e.preferred_san, e.practice_expected_uci, e.practice_expected_san,
           c.user_id, e.practice_fen
    FROM correction_evidence e JOIN account_corrections c ON c.id = e.correction_id
    WHERE e.practice_available
      AND coalesce(preferred_move, '') <> '' AND coalesce(preferred_san, '') <> ''
      AND (practice_expected_uci IS DISTINCT FROM preferred_move
           OR practice_expected_san IS DISTINCT FROM preferred_san)
    ORDER BY e.created_at, e.id
"""

with db.connection() as conn:
    with conn.transaction():
        rows = conn.execute(FIND).fetchall()
        print(f"{len(rows)} correction evidence row(s) whose practice target differs from the card")
        ok, skipped = [], []
        for _id, corr, imported_id, review_id, ply, pref_uci, pref_san, prac_uci, prac_san, user_id, fen in rows:
            # The card's move must be a legal move in the saved practice
            # position, or the row is left alone and named: a target that
            # cannot be played is worse than the mismatch.
            try:
                legal = chess.Move.from_uci(pref_uci) in chess.Board(data_keys.unseal(user_id, fen)).legal_moves
            except Exception:  # noqa: BLE001 - unreadable FEN or bad UCI both mean "skip"
                legal = False
            (ok if legal else skipped).append(_id)
            print(f"  {corr}  game={imported_id or review_id}  ply={ply}  "
                  f"card={pref_san} ({pref_uci})  practice={prac_san} ({prac_uci})"
                  + ("" if legal else "  SKIP: card move not legal in the saved practice FEN"))
        if not APPLY:
            print(f"dry run - nothing changed ({len(ok)} would be repaired, {len(skipped)} skipped; pass --apply)")
        elif ok:
            n = conn.execute(
                "UPDATE correction_evidence SET practice_expected_uci = preferred_move,"
                " practice_expected_san = preferred_san WHERE id = ANY(%s)",
                (ok,),
            ).rowcount
            print(f"repaired {n} row(s), skipped {len(skipped)}")
        else:
            print(f"nothing to repair, skipped {len(skipped)}")

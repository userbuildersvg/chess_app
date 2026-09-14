#!/usr/bin/env python3
"""
One-off: seal the sensitive fields of rows written before APP_MASTER_KEY
existed. Safe to re-run - already-sealed values are skipped.

    set -a; . ./.env; set +a
    /tmp/chessapp/bin/python tools/encrypt_existing.py [--dry-run]

Account-owned rows only (owner 'user:<id>' / user_id); guest rows stay as
they are (data_keys.py explains why).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import data_keys  # noqa: E402
import db  # noqa: E402

dry = "--dry-run" in sys.argv
if not data_keys.enabled():
    raise SystemExit("APP_MASTER_KEY is not set - nothing to seal with.")

counts = {}


def bump(k):
    counts[k] = counts.get(k, 0) + 1


with db.connection() as conn:
    for gid, owner, pgn in conn.execute(
            "SELECT id, owner, pgn FROM imported_games WHERE owner LIKE 'user:%'").fetchall():
        if data_keys.is_sealed(pgn):
            continue
        bump("imported_games.pgn")
        if not dry:
            conn.execute("UPDATE imported_games SET pgn = %s WHERE id = %s", (data_keys.seal(owner, pgn), gid))
    for fid, owner, fen in conn.execute(
            "SELECT id, owner, fen_before FROM game_findings WHERE owner LIKE 'user:%'").fetchall():
        if data_keys.is_sealed(fen):
            continue
        bump("game_findings.fen_before")
        if not dry:
            conn.execute("UPDATE game_findings SET fen_before = %s WHERE id = %s", (data_keys.seal(owner, fen), fid))
    for cid, uid, intent, factor, diag, caveat in conn.execute(
            "SELECT id, user_id, player_intent, missed_factor, diagnosis, caveat FROM account_corrections").fetchall():
        if data_keys.is_sealed(intent):
            continue
        bump("account_corrections")
        if not dry:
            conn.execute(
                "UPDATE account_corrections SET player_intent = %s, missed_factor = %s, diagnosis = %s, caveat = %s"
                " WHERE id = %s",
                (data_keys.seal(uid, intent), data_keys.seal(uid, factor), data_keys.seal(uid, diag),
                 data_keys.seal(uid, caveat), cid))
    for eid, uid, evidence, fen in conn.execute(
            "SELECT e.id, c.user_id, e.evidence, e.practice_fen FROM correction_evidence e"
            " JOIN account_corrections c ON c.id = e.correction_id").fetchall():
        ev = json.loads(evidence) if isinstance(evidence, str) else evidence
        if isinstance(ev, dict) and set(ev) == {"enc"}:
            continue
        bump("correction_evidence")
        if not dry:
            text = json.dumps(ev, separators=(",", ":"))
            conn.execute(
                "UPDATE correction_evidence SET evidence = %s::jsonb, practice_fen = %s WHERE id = %s",
                (json.dumps({"enc": data_keys.seal(uid, text)}), data_keys.seal(uid, fen), eid))

print(("would seal" if dry else "sealed") + ": " + (", ".join(f"{k} {v}" for k, v in counts.items()) or "nothing"))

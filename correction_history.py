"""Durable account correction cards. Guest corrections stay in learning_loop."""

from __future__ import annotations

import json
import secrets
import time

import data_keys
import db
from learning_loop import STATUSES, THEMES, theme_label


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _sealed_json(user_id, value: dict) -> str:
    """The evidence packet as jsonb: `{"enc": "<sealed>"}` under a data key,
    the packet itself otherwise."""
    text = json.dumps(value, separators=(",", ":"))
    sealed = data_keys.seal(user_id, text)
    return json.dumps({"enc": sealed}, separators=(",", ":")) if sealed != text else text


def _open(user_id, value):
    """Reverse of `_sealed_json`. An unreadable packet is an empty one."""
    value = _json(value)
    if isinstance(value, dict) and set(value) == {"enc"}:
        text = data_keys.unseal(user_id, value["enc"])
        return json.loads(text) if text else {}
    return value


def get(user_id, correction_id: str, connect=None) -> dict | None:
    opener = connect or db.connection
    with opener() as conn:
        row = conn.execute(
            "SELECT id, theme, player_intent, missed_factor, diagnosis, correction_rule,"
            " confidence, caveat, status, occurrence_count, created_at, last_seen_at"
            " FROM account_corrections WHERE id = %s AND user_id = %s",
            (correction_id, user_id),
        ).fetchone()
        if row is None:
            return None
        evidence_rows = conn.execute(
            "SELECT id, evidence, practice_available, practice_unavailable_reason,"
            " practice_fen, practice_expected_uci, practice_started_at,"
            " practice_completed_at, practice_outcome"
            " FROM correction_evidence WHERE correction_id = %s ORDER BY created_at, id",
            (correction_id,),
        ).fetchall()
        attempts = conn.execute(
            "SELECT a.id, a.played_uci, a.passed, a.outcome, a.hints_used, a.response_ms,"
            " a.created_at, e.practice_fen, e.practice_expected_uci"
            " FROM correction_practice_attempts a"
            " JOIN correction_evidence e ON e.id = a.correction_evidence_id"
            " WHERE e.correction_id = %s ORDER BY a.created_at, a.id",
            (correction_id,),
        ).fetchall()

    evidence = [_json(_open(user_id, r[1])) for r in evidence_rows]
    attempt_payload = [{
        "id": r[0], "correction_id": correction_id, "played_uci": r[1],
        "passed": bool(r[2]), "outcome": r[3], "hints_used": int(r[4]),
        "response_ms": r[5], "created_at": r[6], "fen": data_keys.unseal(user_id, r[7]),
        "expected": [r[8]] if r[8] else [],
    } for r in attempts]
    passed = sum(1 for a in attempt_payload if a["passed"])
    latest = evidence_rows[-1] if evidence_rows else None
    return {
        "id": row[0], "theme": row[1], "theme_label": theme_label(row[1]),
        "player_intent": data_keys.unseal(user_id, row[2]), "missed_factor": data_keys.unseal(user_id, row[3]),
        "diagnosis": data_keys.unseal(user_id, row[4]),
        "correction_rule": row[5], "confidence": row[6], "uncertainty": data_keys.unseal(user_id, row[7]),
        "status": row[8], "occurrence_count": int(row[9]), "created_at": row[10],
        "last_seen_at": row[11], "evidence": evidence, "attempts": attempt_payload,
        "practice_summary": {
            "attempted": len(attempt_payload), "passed": passed,
            "rate": round(passed / len(attempt_payload), 2) if attempt_payload else None,
            "hints_used": sum(a["hints_used"] for a in attempt_payload),
            "last_passed": attempt_payload[-1]["passed"] if attempt_payload else None,
        },
        "practice_available": bool(latest[2]) if latest else False,
        "practice_unavailable_reason": latest[3] if latest else "missing_snapshot",
        "saved_to_account": True,
    }


def list_for(user_id, connect=None) -> list[dict]:
    opener = connect or db.connection
    with opener() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM account_corrections WHERE user_id = %s"
            " AND EXISTS (SELECT 1 FROM correction_evidence e WHERE e.correction_id = account_corrections.id)"
            " ORDER BY last_seen_at DESC", (user_id,),
        ).fetchall()]
    return [card for cid in ids if (card := get(user_id, cid, connect=connect))]


def upsert(user_id, *, theme: str, player_intent: str, missed_factor: str,
           diagnosis: str, confidence: float, uncertainty: str | None,
           evidence: dict, practice: dict, connect=None) -> tuple[dict, bool]:
    now = time.time()
    opener = connect or db.connection
    with opener() as conn:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO account_corrections"
                " (id,user_id,theme,player_intent,missed_factor,diagnosis,correction_rule,"
                " confidence,caveat,status,occurrence_count,created_at,last_seen_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',1,%s,%s)"
                " ON CONFLICT (user_id,theme) DO UPDATE SET player_intent=excluded.player_intent,"
                " missed_factor=excluded.missed_factor,diagnosis=excluded.diagnosis,"
                " confidence=excluded.confidence,caveat=excluded.caveat,"
                " occurrence_count=account_corrections.occurrence_count+1,last_seen_at=excluded.last_seen_at"
                " RETURNING id,occurrence_count",
                (_id("corr"), user_id, theme, data_keys.seal(user_id, player_intent),
                 data_keys.seal(user_id, missed_factor), data_keys.seal(user_id, diagnosis),
                 THEMES[theme]["check"], confidence, data_keys.seal(user_id, uncertainty), now, now),
            ).fetchone()
            correction_id, occurrences = row[0], int(row[1])
            recurred = occurrences > 1
            conn.execute(
                "INSERT INTO correction_evidence"
                " (correction_id,imported_game_id,profile_finding_id,review_id,node_id,ply,played_move,preferred_move,"
                " preferred_san,source,source_username,evidence,practice_available,"
                " practice_unavailable_reason,practice_fen,practice_expected_uci,"
                " practice_expected_san,created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)",
                (correction_id, evidence.get("imported_game_id"), practice.get("finding_id"), evidence.get("game_id"),
                 evidence.get("node_id"), evidence.get("ply"), evidence.get("uci"),
                 evidence.get("best_move"), evidence.get("best_san"), practice["source"],
                 evidence.get("source_username"), _sealed_json(user_id, evidence),
                 practice["available"], practice.get("reason"), data_keys.seal(user_id, practice.get("fen")),
                 practice.get("best_uci"), practice.get("best_san"), now),
            )
    return get(user_id, correction_id, connect=connect), recurred


def set_status(user_id, correction_id: str, status: str, connect=None) -> dict | None:
    if status not in STATUSES:
        raise ValueError(status)
    opener = connect or db.connection
    with opener() as conn:
        changed = conn.execute(
            "UPDATE account_corrections SET status=%s,last_seen_at=%s"
            " WHERE id=%s AND user_id=%s", (status, time.time(), correction_id, user_id),
        ).rowcount
    return get(user_id, correction_id, connect=connect) if changed else None


def practice_for(user_id, correction_id: str, connect=None) -> dict | None:
    opener = connect or db.connection
    with opener() as conn:
        row = conn.execute(
            "SELECT e.id,e.practice_available,e.practice_unavailable_reason,e.practice_fen,"
            " e.practice_expected_uci,e.practice_expected_san,e.source,e.imported_game_id,e.played_move,"
            " (SELECT count(*) FROM correction_practice_attempts a WHERE a.correction_evidence_id=e.id)"
            " FROM correction_evidence e JOIN account_corrections c ON c.id=e.correction_id"
            " WHERE c.id=%s AND c.user_id=%s ORDER BY e.created_at DESC,e.id DESC LIMIT 1",
            (correction_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return {"evidence_id": int(row[0]), "available": bool(row[1]), "reason": row[2],
            "fen": data_keys.unseal(user_id, row[3]), "best_uci": row[4], "best_san": row[5], "source": row[6],
            "imported_game_id": row[7], "played_move": row[8], "attempt_index": int(row[9])}


def start_practice(user_id, correction_id: str, connect=None) -> tuple[dict | None, str | None]:
    practice = practice_for(user_id, correction_id, connect=connect)
    if not practice or not practice["available"]:
        return practice, None
    session_id = _id("prac")
    opener = connect or db.connection
    with opener() as conn:
        conn.execute(
            "UPDATE correction_evidence e SET practice_session_id=%s,practice_started_at=%s"
            " FROM account_corrections c WHERE e.id=%s AND e.correction_id=c.id AND c.user_id=%s",
            (session_id, time.time(), practice["evidence_id"], user_id),
        )
    return practice, session_id


def record_attempt(user_id, correction_id: str, practice: dict, *, played_uci: str,
                   passed: bool, outcome: str, hints_used: int, response_ms: int | None,
                   connect=None) -> dict | None:
    opener = connect or db.connection
    now = time.time()
    with opener() as conn:
        with conn.transaction():
            owned = conn.execute(
                "SELECT 1 FROM correction_evidence e JOIN account_corrections c ON c.id=e.correction_id"
                " WHERE e.id=%s AND c.id=%s AND c.user_id=%s",
                (practice["evidence_id"], correction_id, user_id),
            ).fetchone()
            if not owned:
                return None
            conn.execute(
                "INSERT INTO correction_practice_attempts"
                " (id,correction_evidence_id,played_uci,passed,outcome,hints_used,response_ms,created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (_id("att"), practice["evidence_id"], played_uci, passed, outcome,
                 hints_used, response_ms, now),
            )
            conn.execute(
                "UPDATE correction_evidence SET practice_completed_at=%s,practice_outcome=%s WHERE id=%s",
                (now, outcome, practice["evidence_id"]),
            )
    return get(user_id, correction_id, connect=connect)


def link_finding(user_id, correction_id: str, finding_id: int, connect=None) -> None:
    opener = connect or db.connection
    with opener() as conn:
        conn.execute(
            "UPDATE correction_evidence e SET profile_finding_id=%s FROM account_corrections c"
            " WHERE e.correction_id=c.id AND c.id=%s AND c.user_id=%s"
            " AND e.profile_finding_id IS NULL"
            " AND e.id=(SELECT id FROM correction_evidence WHERE correction_id=%s ORDER BY created_at DESC,id DESC LIMIT 1)",
            (finding_id, correction_id, user_id, correction_id),
        )


def coverage(conn) -> dict:
    row = conn.execute(
        "SELECT count(*),count(*) FILTER (WHERE practice_available),"
        " count(*) FILTER (WHERE NOT practice_available),"
        " count(*) FILTER (WHERE practice_started_at IS NOT NULL),"
        " count(*) FILTER (WHERE practice_completed_at IS NOT NULL) FROM correction_evidence"
    ).fetchone()
    total, available, unavailable, started, completed = map(int, row)
    return {"corrections_saved_to_account": total, "practice_available": available,
            "practice_unavailable": unavailable,
            "correction_to_practice_coverage_pct": round(available * 100 / total, 1) if total else None,
            "practice_started": started, "practice_completed": completed}

"""
Read-only admin overview: `GET /api/admin/overview`.

Admin = a signed-in account whose email is in the `ADMIN_EMAILS` allowlist
(comma-separated, compared case-insensitively against `users.email_ci`).
Enforced here, on the server - the `/admin` page in the browser draws what
this route answers and authorises nothing. The beta gate covers `/api/admin`
like every other `/api` route.

Everything is a count or a duration read from existing tables. Nothing in the
response is a PGN, a FEN, a chat line, a correction sentence, a token or a
key; recent accounts carry a shortened id, the email (the reader is the
founder, who already holds it), a creation time and game counts. Where the
product does not record a thing yet, the field says `"not_tracked"` rather
than inventing a zero.
"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
import learning_events
from auth_service import accounts_enabled, auth_service
from identity import account_id_of, identity_of

router = APIRouter(prefix="/api/admin", tags=["admin"])

NOT_TRACKED = "not_tracked"
DAY = 86400.0


def admin_emails() -> frozenset:
    return frozenset(
        e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
    )


def is_admin(account_id) -> bool:
    """On the ADMIN_EMAILS allowlist, or granted by an invite code (admin_invites.py)."""
    if account_id is None:
        return False
    user = auth_service.get_user(account_id)
    if not user:
        return False
    if user.get("is_admin"):
        return True
    email = user.get("email") or ""
    return bool(admin_emails()) and email.strip().lower() in admin_emails()


def _refuse(status: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "ok": False, "error": error, "message": message, "detail": message,
    })


def require_admin(request: Request):
    """The admin's account id, or the JSONResponse that refuses the caller."""
    account_id = account_id_of(identity_of(request)) if accounts_enabled() else None
    if account_id is None:
        return None, _refuse(401, "account_required", "Sign in to access this admin page.")
    if not is_admin(account_id):
        return None, _refuse(403, "admin_required",
                             "You do not have permission to access this admin page.")
    return account_id, None


# --- queries ---------------------------------------------------------------

def _one(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _int(v):
    return int(v) if v is not None else 0


def _accounts(conn, now):
    total = _int(_one(conn, "SELECT count(*) FROM users"))
    today = _int(_one(conn, "SELECT count(*) FROM users WHERE created_at >= %s", (now - DAY,)))
    week = _int(_one(conn, "SELECT count(*) FROM users WHERE created_at >= %s", (now - 7 * DAY,)))
    with_imports = _int(_one(conn, "SELECT count(DISTINCT owner) FROM imported_games"))
    with_analysed = _int(_one(
        conn, "SELECT count(DISTINCT owner) FROM imported_games WHERE state = 'done'"))
    rows = conn.execute(
        "SELECT u.id, u.email, u.created_at, u.last_login_at,"
        "       (SELECT count(*) FROM imported_games g WHERE g.owner = 'user:' || u.id),"
        "       (SELECT count(*) FROM imported_games g WHERE g.owner = 'user:' || u.id AND g.state = 'done'),"
        "       (SELECT count(*) FROM imported_games g WHERE g.owner = 'user:' || u.id AND g.reviewed_at IS NOT NULL)"
        " FROM users u ORDER BY u.created_at DESC LIMIT 10"
    ).fetchall()
    recent = [{
        "id": f"u{r[0]}", "email": r[1], "created_at": r[2], "last_login_at": r[3],
        "imported": _int(r[4]), "analysed": _int(r[5]), "reviewed": _int(r[6]),
    } for r in rows]
    return {"total": total, "created_today": today, "created_7d": week,
            "with_imported_games": with_imports, "with_analysed_games": with_analysed,
            "recent": recent}


def _imports(conn):
    rows = conn.execute(
        "SELECT source, state, count(*) FROM imported_games GROUP BY source, state").fetchall()
    by_source: dict = {}
    for source, state, n in rows:
        s = by_source.setdefault(source or "manual", {"games": 0, "analysed": 0, "failed": 0})
        s["games"] += _int(n)
        if state == "done":
            s["analysed"] += _int(n)
        elif state == "failed":
            s["failed"] += _int(n)
    # Duplicates are refused at import time and counted only in the events
    # stream (`duplicate_count` on external_import_game_imported).
    dup = _one(conn, "SELECT sum((properties->>'duplicate_count')::int) FROM product_events"
                     " WHERE event_name = 'external_import_game_imported'")
    search_failed = _int(_one(
        conn, "SELECT count(*) FROM product_events WHERE event_name = 'external_import_search_failed'"))
    return {"chesscom": by_source.get("chesscom", {"games": 0, "analysed": 0, "failed": 0}),
            "lichess": by_source.get("lichess", {"games": 0, "analysed": 0, "failed": 0}),
            "manual": by_source.get("manual", {"games": 0, "analysed": 0, "failed": 0}),
            "external_search_failures": search_failed,
            "duplicates_refused": _int(dup) if dup is not None else 0}


def _event_counts(conn) -> dict:
    return {name: _int(n) for name, n in conn.execute(
        "SELECT event_name, count(*) FROM product_events GROUP BY event_name").fetchall()}


class _EventStats:
    """Every per-event aggregate the overview needs, fetched in THREE
    grouped queries instead of one query per metric.

    From a laptop in another region each round trip to Neon is ~150ms, and
    the overview used to make about forty-five of them - seven seconds of
    sequential waiting on a page that is meant to be glanced at. Grouping
    by event name (and operation / category / flag) gets the same numbers
    in a handful of trips, and keeps the whole page well inside one
    statement-timeout budget.
    """

    def __init__(self, conn):
        # (event, operation) -> (n, avg_ms, p95_ms) over rows with duration_ms
        self.latency = {}
        for ev, op, n, avg, p95 in conn.execute(
                "SELECT event_name, coalesce(properties->>'operation', ''), count(*),"
                "       avg((properties->>'duration_ms')::float),"
                "       percentile_cont(0.95) WITHIN GROUP (ORDER BY (properties->>'duration_ms')::float)"
                " FROM product_events WHERE properties ? 'duration_ms'"
                " GROUP BY 1, 2").fetchall():
            self.latency[(ev, op)] = (_int(n), avg, p95)
        # (event, flag) -> count of rows where properties->>flag = 'true'
        self.flags = {}
        for ev, fb, to, n in conn.execute(
                "SELECT event_name, properties->>'fallback' = 'true', properties->>'timeout' = 'true', count(*)"
                " FROM product_events GROUP BY 1, 2, 3").fetchall():
            if fb:
                self.flags[(ev, "fallback")] = self.flags.get((ev, "fallback"), 0) + _int(n)
            if to:
                self.flags[(ev, "timeout")] = self.flags.get((ev, "timeout"), 0) + _int(n)
        # event -> {category: count}
        self.categories = {}
        for ev, cat, n in conn.execute(
                "SELECT event_name, properties->>'error_category', count(*) FROM product_events"
                " WHERE properties ? 'error_category' GROUP BY 1, 2 ORDER BY 3 DESC").fetchall():
            self.categories.setdefault(ev, {})[cat or "unknown"] = _int(n)

    def latency_of(self, event: str, operation: str | None = None) -> dict:
        if operation is None:
            rows = [v for (ev, _op), v in self.latency.items() if ev == event]
        else:
            rows = [v for (ev, op), v in self.latency.items() if ev == event and op == operation]
        n = sum(r[0] for r in rows)
        if not n:
            return {"samples": 0, "avg_ms": None, "p95_ms": None}
        avg = sum(r[1] * r[0] for r in rows) / n
        # p95 across operations is the largest per-operation p95: a bound,
        # not an exact quantile, and honest about being one.
        return {"samples": n, "avg_ms": round(avg), "p95_ms": round(max(r[2] for r in rows))}

    def flag(self, event: str, key: str) -> int:
        return self.flags.get((event, key), 0)

    def by_category(self, event: str) -> dict:
        return dict(self.categories.get(event, {}))


def _recent_failures(conn) -> list:
    rows = conn.execute(
        "SELECT event_name, occurred_at, source_mode, properties->>'operation',"
        "       properties->>'error_category', properties->>'error_code'"
        " FROM product_events"
        " WHERE event_name IN ('correction_flow_error', 'diagnosis_fallback',"
        "       'external_import_search_failed', 'play_game_review_handoff_failed')"
        "    OR (event_name = 'llm_request_completed' AND properties->>'fallback' = 'true')"
        " ORDER BY occurred_at DESC LIMIT 15").fetchall()
    return [{"event": r[0], "at": r[1], "mode": r[2], "operation": r[3],
             "category": r[4], "code": r[5]} for r in rows]


# --- v0.1 sections ----------------------------------------------------------

def _merge_counts(*dicts: dict) -> dict:
    out: dict = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _group(conn, sql, params=()) -> dict:
    return {(k if k is not None else "unknown"): _int(n) for k, n in conn.execute(sql, params).fetchall()}


def _funnel(conn, ev: dict, accounts: dict) -> list:
    games = _int(_one(conn, "SELECT count(*) FROM imported_games"))
    return [
        {"step": "accounts_created", "count": accounts["total"]},
        {"step": "users_with_imported_games", "count": accounts["with_imported_games"]},
        {"step": "games_imported", "count": games},
        {"step": "reviews_started", "count": ev.get("game_analysis_started", 0)},
        {"step": "reviews_completed", "count": ev.get("game_analysis_completed", 0)},
        {"step": "correction_cards_generated", "count": ev.get("correction_generated", 0)},
        {"step": "intent_submissions",
         "count": ev.get("player_intention_submitted", 0) + ev.get("intent_submitted", 0)},
        {"step": "fresh_practice_started",
         "count": ev.get("fresh_practice_opened", 0) + ev.get("practice_started", 0)},
        {"step": "fresh_practice_completed", "count": ev.get("practice_completed", 0)},
    ]


FAILURE_EVENTS = ("correction_flow_error", "diagnosis_fallback", "external_import_search_failed",
                  "play_game_review_handoff_failed")


def _recent_accounts(conn) -> list:
    """The 15 newest accounts with per-account counts.

    Event rows carry `learning_events.player_key(identity)`, an HMAC pseudonym
    of the identity string. It is recomputed here per account to join the
    two, and is never part of the answer.
    """
    users = conn.execute(
        "SELECT id, email, created_at, last_login_at FROM users ORDER BY created_at DESC LIMIT 15"
    ).fetchall()
    if not users:
        return []
    owners = [f"user:{u[0]}" for u in users]
    by_source = {}
    for owner, source, n in conn.execute(
            "SELECT owner, source, count(*) FROM imported_games WHERE owner = ANY(%s)"
            " GROUP BY owner, source", (owners,)).fetchall():
        by_source.setdefault(owner, {})[source or "manual"] = _int(n)
    keys = {learning_events.player_key(o): o for o in owners}
    counts = {}
    for actor, name, n in conn.execute(
            "SELECT actor_key, event_name, count(*) FROM product_events WHERE actor_key = ANY(%s)"
            " GROUP BY actor_key, event_name", (list(keys),)).fetchall():
        counts.setdefault(keys[actor], {})[name] = _int(n)
    out = []
    for uid, email, created, last_login in users:
        owner = f"user:{uid}"
        src = by_source.get(owner, {})
        ev = counts.get(owner, {})
        out.append({
            "id": f"u{uid}", "email": email, "created_at": created, "last_active_at": last_login,
            "chesscom_games": src.get("chesscom", 0), "lichess_games": src.get("lichess", 0),
            "manual_games": src.get("manual", 0),
            "reviews_completed": ev.get("game_analysis_completed", 0),
            "corrections_generated": ev.get("correction_generated", 0),
            "practice_started": ev.get("fresh_practice_opened", 0) + ev.get("practice_started", 0),
            "practice_completed": ev.get("practice_completed", 0),
            "failures": sum(ev.get(e, 0) for e in FAILURE_EVENTS),
        })
    return out


def _import_health(conn, imports: dict, stats: "_EventStats") -> dict:
    searches = _group(conn, "SELECT properties->>'import_source', count(*) FROM product_events"
                            " WHERE event_name = 'external_import_search_started' GROUP BY 1")
    failures = _group(conn, "SELECT properties->>'import_source', count(*) FROM product_events"
                            " WHERE event_name = 'external_import_search_failed' GROUP BY 1")
    categories = stats.by_category("external_import_search_failed")
    avg_returned = _one(conn, "SELECT avg((properties->>'games_returned')::float) FROM product_events"
                              " WHERE event_name = 'external_import_search_completed'")
    recent = [{"at": r[0], "event": r[1], "source": r[2], "category": r[3], "games": r[4]}
              for r in conn.execute(
                  "SELECT occurred_at, event_name, properties->>'import_source',"
                  "       properties->>'error_category', (properties->>'games_imported')::int"
                  " FROM product_events WHERE event_name IN ('external_import_search_completed',"
                  " 'external_import_search_failed', 'external_import_game_imported')"
                  " ORDER BY occurred_at DESC LIMIT 10").fetchall()]
    return {
        "searches": {"chesscom": searches.get("chesscom", 0), "lichess": searches.get("lichess", 0)},
        "imported_games": {"chesscom": imports["chesscom"]["games"], "lichess": imports["lichess"]["games"],
                           "manual": imports["manual"]["games"]},
        "analysis_failures": {"chesscom": imports["chesscom"]["failed"], "lichess": imports["lichess"]["failed"],
                              "manual": imports["manual"]["failed"]},
        "search_failures": {"chesscom": failures.get("chesscom", 0), "lichess": failures.get("lichess", 0)},
        "duplicates_refused": imports["duplicates_refused"],
        "username_not_found": categories.get("username_not_found", 0),
        "provider_unavailable": categories.get("provider_unavailable", 0),
        "rate_limited": categories.get("rate_limited", 0),
        "no_games": categories.get("no_games", 0),
        # The fetcher has no timeout or parse category of its own: a timeout
        # surfaces as provider_unavailable and an unparseable game is skipped.
        "provider_timeout": NOT_TRACKED,
        "parse_failed": NOT_TRACKED,
        "error_categories": categories,
        "avg_games_returned": round(avg_returned, 1) if avg_returned is not None else None,
        "recent_events": recent,
    }


def _provider_health(conn, ev: dict, latency: dict, stats: "_EventStats") -> dict:
    llm = ev.get("llm_request_completed", 0)
    ai_moves = ev.get("ai_move_explanation_generated", 0) + ev.get("ai_move_generation_completed", 0)
    fallback_moves = _int(_one(
        conn, "SELECT count(*) FROM product_events WHERE event_name = 'ai_move_explanation_generated'"
              " AND coalesce(properties->>'source', 'gemini') <> 'gemini'"))
    fallback_moves += stats.flag("ai_move_generation_completed", "fallback")
    return {
        "gemini_calls": llm + ai_moves,
        "llm_requests": llm,
        "ai_move_requests": ai_moves,
        "gemini_timeouts": stats.flag("llm_request_completed", "timeout")
        + stats.flag("correction_generated", "timeout"),
        "fallback_moves": fallback_moves,
        "fallback_explanations": stats.flag("llm_request_completed", "fallback")
        + ev.get("diagnosis_fallback", 0),
        "ai_move_latency": latency["ai_move"],
        "diagnosis_latency": latency["diagnosis"],
        "review_chat_latency": latency["review_chat"],
        "by_provider": _group(conn, "SELECT properties->>'provider', count(*) FROM product_events"
                                    " WHERE event_name IN ('llm_request_completed', 'ai_move_generation_completed')"
                                    " GROUP BY 1 ORDER BY 2 DESC"),
        "by_model": _group(conn, "SELECT properties->>'model', count(*) FROM product_events"
                                 " WHERE event_name IN ('llm_request_completed', 'correction_generated')"
                                 " AND properties ? 'model' GROUP BY 1 ORDER BY 2 DESC"),
        "error_categories": _merge_counts(stats.by_category("correction_flow_error"),
                                          stats.by_category("diagnosis_fallback")),
    }


def _review_health(conn, ev: dict, reviews: dict, latency: dict) -> dict:
    grades = _group(conn, "SELECT grade_assigned, count(*) FROM move_grade_audits GROUP BY 1")
    return {
        "started": reviews["started"], "completed": reviews["completed"], "failed": reviews["failures"],
        "imported_game_reviews": reviews["imported_games_reviewed"],
        "play_reviews": ev.get("play_game_analysis_completed", 0),
        "manual_pgn_reviews": ev.get("pgn_imported", 0),
        "avg_scan_ms": latency["review_scan"]["avg_ms"],
        "avg_judged_moves": reviews["avg_judged_moves"],
        "graded_moves": sum(grades.values()),
        "book_moves": grades.get("book", 0),
        "forced_moves": grades.get("forced", 0),
        "unjudged_moves": grades.get("unknown", 0),
        "grade_missing_moves_clicks": NOT_TRACKED,
    }


def _profile_evidence(conn) -> dict:
    origins = _group(conn, "SELECT origin, count(*) FROM game_findings GROUP BY 1")
    sources = _group(conn, "SELECT coalesce(g.source, 'manual'), count(*) FROM game_findings f"
                           " JOIN imported_games g ON g.id = f.game_id GROUP BY 1")
    themes = [{"theme": t, "count": n} for t, n in _group(
        conn, "SELECT theme, count(*) FROM game_findings GROUP BY 1 ORDER BY 2 DESC LIMIT 8").items()]
    return {
        "label": "Observed evidence",
        "total_rows": sum(origins.values()),
        "detector_findings": origins.get("detector", 0),
        "correction_evidence": origins.get("correction", 0),
        "by_source": {"chesscom": sources.get("chesscom", 0), "lichess": sources.get("lichess", 0),
                      "manual": sources.get("manual", 0), "play": NOT_TRACKED},
        "top_themes": themes,
    }


def build_overview() -> dict:
    now = time.time()
    if not db.configured():
        return {"generated_at": now, "database": False, "accounts": NOT_TRACKED,
                "imports": NOT_TRACKED, "reviews": NOT_TRACKED, "corrections": NOT_TRACKED,
                "failures": NOT_TRACKED, "latency": NOT_TRACKED, "recent_failures": [],
                "funnel": NOT_TRACKED, "recent_accounts": [], "import_health": NOT_TRACKED,
                "provider_health": NOT_TRACKED, "review_health": NOT_TRACKED,
                "profile_evidence": NOT_TRACKED}
    with db.connection() as conn:
        ev = _event_counts(conn)
        stats = _EventStats(conn)
        accounts = _accounts(conn, now)
        imports = _imports(conn)
        avg_judged = _one(conn, "SELECT avg((properties->>'analysed_moves')::float)"
                                " FROM product_events WHERE event_name = 'game_analysis_completed'")
        reviews = {
            "started": ev.get("game_analysis_started", 0),
            "completed": ev.get("game_analysis_completed", 0),
            "pgn_dropped": ev.get("pgn_imported", 0),
            "from_play": ev.get("play_game_analysis_completed", 0),
            "imported_games_reviewed": _int(_one(
                conn, "SELECT count(*) FROM imported_games WHERE reviewed_at IS NOT NULL")),
            "imported_games_analysed": ev.get("imported_game_analysis_completed", 0),
            "avg_judged_moves": round(avg_judged, 1) if avg_judged is not None else None,
            "failures": _int(_one(conn, "SELECT count(*) FROM product_events"
                                    " WHERE event_name = 'correction_flow_error'"
                                    " AND properties->>'operation' = 'game_analysis'")),
        }
        corrections = {
            "cards_generated": ev.get("correction_generated", 0),
            "intent_submissions": ev.get("player_intention_submitted", 0) + ev.get("intent_submitted", 0),
            "fresh_practice_started": ev.get("fresh_practice_opened", 0) + ev.get("practice_started", 0),
            "fresh_practice_completed": ev.get("practice_completed", 0),
            "cards_completed": ev.get("correction_card_completed", 0),
            "diagnosis_accepted": ev.get("diagnosis_accepted", 0),
            "diagnosis_disagreed": ev.get("diagnosis_disagreed", 0),
            "evidence_rows": _int(_one(conn, "SELECT count(*) FROM game_findings")),
        }
        failures = {
            "gemini_fallbacks": stats.flag("llm_request_completed", "fallback")
            + stats.flag("ai_move_generation_completed", "fallback"),
            "gemini_timeouts": stats.flag("llm_request_completed", "timeout"),
            "diagnosis_fallbacks": ev.get("diagnosis_fallback", 0),
            "provider_api_failures": ev.get("external_import_search_failed", 0),
            "flow_errors_by_category": stats.by_category("correction_flow_error"),
            "import_analysis_failed": imports["chesscom"]["failed"] + imports["lichess"]["failed"]
            + imports["manual"]["failed"],
            "pgn_parse_failures": NOT_TRACKED,
            "engine_failures": stats.by_category("correction_flow_error").get("engine_analysis", 0),
            "backend_5xx": NOT_TRACKED,
        }
        latency = {
            "ai_move": stats.latency_of("ai_move_explanation_generated"),
            "diagnosis": stats.latency_of("correction_generated"),
            "review_scan": stats.latency_of("game_analysis_completed"),
            "review_chat": stats.latency_of("llm_request_completed", "review_chat"),
        }
        recent_failures = _recent_failures(conn)
        funnel = _funnel(conn, ev, accounts)
        recent_accounts = _recent_accounts(conn)
        import_health = _import_health(conn, imports, stats)
        provider_health = _provider_health(conn, ev, latency, stats)
        review_health = _review_health(conn, ev, reviews, latency)
        profile_evidence = _profile_evidence(conn)
    return {"generated_at": now, "database": True, "accounts": accounts, "imports": imports,
            "reviews": reviews, "corrections": corrections, "failures": failures,
            "latency": latency, "recent_failures": recent_failures,
            "funnel": funnel, "recent_accounts": recent_accounts, "import_health": import_health,
            "provider_health": provider_health, "review_health": review_health,
            "profile_evidence": profile_evidence}


@router.get("/overview")
def overview(request: Request):
    _, refusal = require_admin(request)
    if refusal is not None:
        return refusal
    return {"ok": True, "overview": build_overview()}

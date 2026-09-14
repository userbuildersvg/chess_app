"""
The read-only admin overview (admin_api.py), through the API.

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_admin_$$ \\
        /tmp/chessapp/bin/python test_admin_api.py

Needs DATABASE_URL and a disposable schema (dropped on the way out).

What is proved: a guest gets 401 account_required, a signed-in non-admin gets
403 admin_required, an ADMIN_EMAILS account gets the overview; the overview
carries counts and never a PGN, FEN, key or token; an empty schema answers
with zeros and nulls rather than a 500; and no route under /api/admin writes.
"""
import json
import os

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ["ACCOUNTS_ENABLED"] = "true"
os.environ.setdefault("SESSION_COOKIE_SECRET", "test-signing-key-not-a-real-secret")
os.environ["ADMIN_EMAILS"] = " Founder@Example.com , second@example.com"
if os.environ.get("DATABASE_SCHEMA", "public") == "public":
    raise SystemExit("Refusing to run against the public schema - set DATABASE_SCHEMA to a disposable name.")

from fastapi.testclient import TestClient

import app
import admin_api
import correction_history
import db
import db_writer
import learning_events
import move_grade_audit
import profile_service
import rate_limit as rate_limit_module

PASSED = FAILED = 0


def check(label, cond, detail=None):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f" - {detail}" if detail else ""))


def section(name):
    print(f"\n--- {name} ---")


def clear_limits():
    rate_limit_module.limit_login.limiter.reset()
    rate_limit_module.limit_signup.limiter.reset()


db.migrate()

PGN = ('[Event "Live Chess"]\n[White "alice"]\n[Black "bob"]\n[Result "1-0"]\n\n'
       '1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0\n')
FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

section("empty schema")
ov = admin_api.build_overview()
check("empty schema builds without crashing", ov["database"] is True)
check("zero accounts", ov["accounts"]["total"] == 0 and ov["accounts"]["recent"] == [])
check("latency with no samples is null, not zero",
      ov["latency"]["ai_move"] == {"samples": 0, "avg_ms": None, "p95_ms": None}, ov["latency"])
check("untracked metrics say so",
      ov["failures"]["pgn_parse_failures"] == "not_tracked" and ov["failures"]["backend_5xx"] == "not_tracked")
check("avg judged moves is null with no reviews", ov["reviews"]["avg_judged_moves"] is None)

section("allowlist")
check("emails are trimmed and lower-cased", admin_api.admin_emails() == {"founder@example.com", "second@example.com"})
check("no account is never admin", admin_api.is_admin(None) is False)

section("guest")
with TestClient(app.app) as guest:
    r = guest.get("/api/admin/overview")
    check("guest -> 401", r.status_code == 401, r.status_code)
    check("...account_required shape", r.json().get("ok") is False and r.json().get("error") == "account_required", r.text)

section("normal account")
clear_limits()
with TestClient(app.app) as c:
    r = c.post("/api/auth/signup", json={"username": "bob", "password": "bob-password-1", "email": "bob@example.com"})
    check("bob exists", r.status_code == 200, r.text)
    r = c.get("/api/admin/overview")
    check("bob -> 403", r.status_code == 403, r.status_code)
    check("...admin_required shape", r.json().get("error") == "admin_required" and "permission" in r.json().get("message", ""), r.text)
    check("bob's account profile says admin: false", c.get("/api/account").json()["admin"] is False)

with db.connection() as conn:
    bob_id = conn.execute("SELECT id FROM users WHERE username = 'bob'").fetchone()[0]
owner = f"user:{bob_id}"
durable_card, _ = correction_history.upsert(
    bob_id, theme="KING_SAFETY", player_intent="Keep the king safe",
    missed_factor="The centre stayed open", diagnosis="Castling was safer.", confidence=0.7,
    uncertainty=None, evidence={"game_id": "review-1", "node_id": "node-1", "ply": 3,
                                "uci": "f1b5", "best_move": "d2d4", "best_san": "d4"},
    practice={"available": True, "source": "manual", "fen": FEN,
              "best_uci": "d2d4", "best_san": "d4"},
)
practice, _ = correction_history.start_practice(bob_id, durable_card["id"])
correction_history.record_attempt(
    bob_id, durable_card["id"], practice, played_uci="d2d4", passed=True,
    outcome="passed", hints_used=0, response_ms=500,
)
gid = profile_service.add_game(owner, PGN, {"white": "alice", "black": "bob", "result": "1-0"}, "white", 6,
                               source="chesscom", external_id="cc-1")
profile_service.record_findings(gid, owner, [{
    "ply": 3, "theme": "KING_SAFETY", "severity": "major", "cpl": 120, "fen_before": FEN,
    "move_san": "Bb5", "best_san": "d4", "phase": "opening",
}])
with db.connection() as conn:
    conn.execute("UPDATE imported_games SET state = 'done', reviewed_at = 1 WHERE id = %s", (gid,))
for name, props in [
    ("game_analysis_started", {}),
    ("game_analysis_completed", {"duration_ms": 1500, "analysed_moves": 30, "completed": True}),
    ("correction_generated", {"duration_ms": 4000, "theme": "KING_SAFETY"}),
    ("ai_move_explanation_generated", {"duration_ms": 900, "source": "gemini"}),
    ("ai_move_explanation_generated", {"duration_ms": 300, "source": "stockfish_fallback"}),
    ("llm_request_completed", {"duration_ms": 8000, "fallback": True, "timeout": True, "operation": "correction_diagnosis"}),
    ("diagnosis_fallback", {"error_category": "model_unavailable"}),
    ("correction_flow_error", {"operation": "game_analysis", "error_category": "engine_analysis", "error_code": "RuntimeError"}),
    ("external_import_search_failed", {"import_source": "lichess", "error_category": "rate_limited"}),
    ("external_import_game_imported", {"import_source": "chesscom", "games_imported": 1, "duplicate_count": 2}),
    ("external_import_search_started", {"import_source": "chesscom"}),
    ("external_import_search_completed", {"import_source": "chesscom", "games_returned": 12}),
    ("external_import_search_started", {"import_source": "lichess"}),
    ("llm_request_completed", {"duration_ms": 700, "operation": "review_chat", "provider": "gemini", "model": "gemini-3.5-flash", "completed": True}),
    ("player_intention_submitted", {}),
    ("fresh_practice_opened", {}),
    ("practice_completed", {}),
]:
    check(f"event {name} accepted", learning_events.emit(name, owner, **props))
for grade in ("book", "forced", "good", None):
    move_grade_audit.audits.record(
        identity=owner, source_flow="review", fen_before=FEN, move_played="e4", fen_after=None,
        side_to_move="white", player_color="white", engine_best="e4", eval_before=0.2, eval_after=0.2,
        eval_perspective="mover", engine_depth=12, grade_assigned=grade)
db_writer.flush()

section("empty-schema shape of the v0.1 groups")
for key in ("funnel", "recent_accounts", "import_health", "provider_health", "review_health", "profile_evidence"):
    check(f"overview has {key}", key in ov)
check("funnel is an ordered list of steps with zero counts before any data",
      [f["step"] for f in ov["funnel"]][:2] == ["accounts_created", "users_with_imported_games"]
      and all(f["count"] == 0 for f in ov["funnel"]), ov["funnel"])
check("import health untracked categories say so",
      ov["import_health"]["provider_timeout"] == "not_tracked" and ov["import_health"]["parse_failed"] == "not_tracked"
      and ov["import_health"]["avg_games_returned"] is None, ov["import_health"])
check("review health: grade-missing clicks not tracked, no grades yet",
      ov["review_health"]["grade_missing_moves_clicks"] == "not_tracked" and ov["review_health"]["graded_moves"] == 0)
check("profile evidence empty and conservatively worded",
      ov["profile_evidence"]["total_rows"] == 0 and ov["profile_evidence"]["label"] == "Observed evidence"
      and ov["profile_evidence"]["by_source"]["play"] == "not_tracked" and ov["profile_evidence"]["top_themes"] == [])
check("provider health with nothing recorded is zeros and null latency",
      ov["provider_health"]["gemini_calls"] == 0 and ov["provider_health"]["ai_move_latency"]["avg_ms"] is None)

section("admin account")
clear_limits()
with TestClient(app.app) as c:
    r = c.post("/api/auth/signup", json={"username": "founder", "password": "founder-password-1", "email": "FOUNDER@example.com"})
    check("founder exists", r.status_code == 200, r.text)
    check("founder's account profile says admin: true", c.get("/api/account").json()["admin"] is True)
    r = c.get("/api/admin/overview")
    check("founder -> 200", r.status_code == 200, r.text[:300])
    body = r.json()
    ov = body["overview"]
    text = json.dumps(body)

    section("counts")
    check("two accounts, both recent", ov["accounts"]["total"] == 2 and ov["accounts"]["created_today"] == 2 and len(ov["accounts"]["recent"]) == 2)
    check("recent accounts newest first with short id + email + counts",
          ov["accounts"]["recent"][0]["email"] == "FOUNDER@example.com"
          and ov["accounts"]["recent"][1] == {**ov["accounts"]["recent"][1], "id": f"u{bob_id}", "imported": 1, "analysed": 1, "reviewed": 1},
          ov["accounts"]["recent"])
    check("accounts with imports / analysed", ov["accounts"]["with_imported_games"] == 1 and ov["accounts"]["with_analysed_games"] == 1)
    check("imports by source", ov["imports"]["chesscom"] == {"games": 1, "analysed": 1, "failed": 0} and ov["imports"]["lichess"]["games"] == 0, ov["imports"])
    check("duplicates refused and search failures from events",
          ov["imports"]["duplicates_refused"] == 2 and ov["imports"]["external_search_failures"] == 1, ov["imports"])
    check("reviews", ov["reviews"]["started"] == 1 and ov["reviews"]["completed"] == 1
          and ov["reviews"]["avg_judged_moves"] == 30.0 and ov["reviews"]["imported_games_reviewed"] == 1
          and ov["reviews"]["failures"] == 1, ov["reviews"])
    check("corrections", ov["corrections"]["cards_generated"] == 1 and ov["corrections"]["intent_submissions"] == 1
          and ov["corrections"]["fresh_practice_started"] == 1 and ov["corrections"]["fresh_practice_completed"] == 1
          and ov["corrections"]["evidence_rows"] == 1
          and ov["corrections"]["corrections_saved_to_account"] == 1
          and ov["corrections"]["practice_available"] == 1
          and ov["corrections"]["practice_unavailable"] == 0
          and ov["corrections"]["correction_to_practice_coverage_pct"] == 100.0
          and ov["corrections"]["practice_started"] == 1
          and ov["corrections"]["practice_completed"] == 1, ov["corrections"])
    check("failures", ov["failures"]["gemini_fallbacks"] == 1 and ov["failures"]["gemini_timeouts"] == 1
          and ov["failures"]["diagnosis_fallbacks"] == 1 and ov["failures"]["provider_api_failures"] == 1
          and ov["failures"]["engine_failures"] == 1
          and ov["failures"]["flow_errors_by_category"] == {"engine_analysis": 1}, ov["failures"])
    check("latency avg/p95", ov["latency"]["ai_move"]["samples"] == 2 and ov["latency"]["ai_move"]["avg_ms"] == 600
          and ov["latency"]["ai_move"]["p95_ms"] == 870 and ov["latency"]["diagnosis"]["avg_ms"] == 4000
          and ov["latency"]["review_scan"]["p95_ms"] == 1500, ov["latency"])
    check("recent failures listed, newest first, categories only",
          len(ov["recent_failures"]) == 4 and all(set(f) == {"event", "at", "mode", "operation", "category", "code"} for f in ov["recent_failures"]),
          ov["recent_failures"])

    section("v0.1 groups")
    funnel = {f["step"]: f["count"] for f in ov["funnel"]}
    check("funnel counts", funnel == {"accounts_created": 2, "users_with_imported_games": 1, "games_imported": 1,
                                      "reviews_started": 1, "reviews_completed": 1, "correction_cards_generated": 1,
                                      "intent_submissions": 1, "fresh_practice_started": 1, "fresh_practice_completed": 1}, funnel)
    bob = next(u for u in ov["recent_accounts"] if u["id"] == f"u{bob_id}")
    check("recent accounts: bob's per-account counts joined through the event pseudonym",
          bob["chesscom_games"] == 1 and bob["lichess_games"] == 0 and bob["manual_games"] == 0
          and bob["reviews_completed"] == 1 and bob["corrections_generated"] == 1
          and bob["practice_started"] == 1 and bob["practice_completed"] == 1 and bob["failures"] == 3, bob)
    check("recent accounts: founder has no activity", next(u for u in ov["recent_accounts"] if u["email"] == "FOUNDER@example.com")["failures"] == 0)
    ih = ov["import_health"]
    check("import health: searches per source", ih["searches"] == {"chesscom": 1, "lichess": 1}, ih)
    check("import health: imported per source", ih["imported_games"] == {"chesscom": 1, "lichess": 0, "manual": 0})
    check("import health: failures and categories",
          ih["search_failures"] == {"chesscom": 0, "lichess": 1} and ih["rate_limited"] == 1
          and ih["username_not_found"] == 0 and ih["error_categories"] == {"rate_limited": 1}, ih)
    check("import health: avg games returned and duplicates", ih["avg_games_returned"] == 12.0 and ih["duplicates_refused"] == 2)
    check("import health: recent events are source/category only",
          len(ih["recent_events"]) == 3 and all(set(e) == {"at", "event", "source", "category", "games"} for e in ih["recent_events"]), ih["recent_events"])
    ph = ov["provider_health"]
    check("provider health: calls, timeouts, fallbacks",
          ph["llm_requests"] == 2 and ph["ai_move_requests"] == 2 and ph["gemini_calls"] == 4
          and ph["gemini_timeouts"] == 1 and ph["fallback_moves"] == 1 and ph["fallback_explanations"] == 2, ph)
    check("provider health: breakdowns", ph["by_provider"] == {"gemini": 1, "unknown": 1}
          and ph["by_model"] == {"gemini-3.5-flash": 1} and ph["error_categories"] == {"engine_analysis": 1, "model_unavailable": 1}, ph)
    check("provider health: latency", ph["ai_move_latency"]["avg_ms"] == 600 and ph["diagnosis_latency"]["avg_ms"] == 4000)
    rh = ov["review_health"]
    check("review health", rh["started"] == 1 and rh["completed"] == 1 and rh["failed"] == 1
          and rh["imported_game_reviews"] == 1 and rh["play_reviews"] == 0 and rh["manual_pgn_reviews"] == 0
          and rh["avg_scan_ms"] == 1500 and rh["avg_judged_moves"] == 30.0, rh)
    check("review health: book/forced/unjudged from the grade audits",
          rh["graded_moves"] == 4 and rh["book_moves"] == 1 and rh["forced_moves"] == 1 and rh["unjudged_moves"] == 1, rh)
    pe = ov["profile_evidence"]
    check("profile evidence: detector vs correction, by source, themes",
          pe["total_rows"] == 1 and pe["detector_findings"] == 1 and pe["correction_evidence"] == 0
          and pe["by_source"] == {"chesscom": 1, "lichess": 0, "manual": 0, "play": "not_tracked"}
          and pe["top_themes"] == [{"theme": "KING_SAFETY", "count": 1}], pe)

    section("privacy")
    check("no PGN in the overview", "Nf3" not in text and "[Event" not in text)
    check("no FEN in the overview", FEN not in text and "KQkq" not in text)
    check("no hashes, tokens or secrets", all(k not in text.lower() for k in ("password", "token", "hash", "secret", "api_key", "session")))
    check("no raw identity strings", f"user:{bob_id}" not in text)
    check("no stack traces", "Traceback" not in text and "File \"" not in text)
    check("no actor keys", not any(len(w) in (20, 64) and all(ch in "0123456789abcdef" for ch in w) for w in text.replace('"', ' ').split()))

    section("read-only")
    for method in ("post", "put", "delete", "patch"):
        check(f"{method.upper()} /api/admin/overview is not a route", getattr(c, method)("/api/admin/overview").status_code == 405)
    check("only one admin route exists", [r.path for r in app.app.routes if r.path.startswith("/api/admin")] == ["/api/admin/overview"])

section("allowlist empty")
os.environ["ADMIN_EMAILS"] = ""
clear_limits()
with TestClient(app.app) as c:
    c.post("/api/auth/login", json={"username": "founder", "password": "founder-password-1"})
    check("with no allowlist even the founder is refused", c.get("/api/admin/overview").status_code == 403)

print(f"\n{PASSED} passed, {FAILED} failed")
db.drop_schema()
try:
    app.stockfish_service.close()
except Exception:
    pass
raise SystemExit(1 if FAILED else 0)

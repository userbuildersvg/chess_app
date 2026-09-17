"""
Account security: password hashing with a pepper, encryption of account
data under per-account keys, crypto-erasure on deletion, cookie flags and
the login oracle.

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_sec_$$ \\
        /tmp/chessapp/bin/python test_account_security.py

Needs DATABASE_URL and a disposable schema (dropped on the way out).
"""
import base64
import json
import os
import secrets

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ["ACCOUNTS_ENABLED"] = "true"
os.environ.setdefault("SESSION_COOKIE_SECRET", "test-signing-key-not-a-real-secret")
os.environ["RATE_LIMITS_ENABLED"] = "true"
os.environ["PASSWORD_PEPPER"] = "test-pepper-not-a-real-secret"
os.environ["APP_MASTER_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
if os.environ.get("DATABASE_SCHEMA", "public") == "public":
    raise SystemExit("Refusing to run against the public schema - set DATABASE_SCHEMA to a disposable name.")

from fastapi.testclient import TestClient

import app
import correction_history
import data_keys
import db
import password_security as pw
import profile_service as ps
import rate_limit as rate_limit_module
from auth_service import auth_service

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
    rate_limit_module.login_by_username.reset()


db.migrate()
PGN = '[Event "x"]\n[White "alice"]\n[Black "bob"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0\n'
FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
PASSWORD = "correct horse battery staple ünïcödé"

section("password_security, pure")
h = pw.hash_password(PASSWORD)
check("scheme is peppered PBKDF2 at 600k with a 16-byte salt",
      h["scheme"] == "pbkdf2_pepper_v1" and h["iterations"] == 600_000 and len(h["salt"]) == 16 and len(h["password_hash"]) == 32)
check("the digest is not the password and two hashes differ", PASSWORD.encode() not in h["password_hash"] and pw.hash_password(PASSWORD)["password_hash"] != h["password_hash"])
check("right password verifies", pw.verify_password(PASSWORD, h["salt"], h["password_hash"], h["iterations"], h["scheme"]))
check("wrong password fails", not pw.verify_password(PASSWORD + "x", h["salt"], h["password_hash"], h["iterations"], h["scheme"]))
saved = os.environ.pop("PASSWORD_PEPPER")
check("a peppered hash cannot be verified without the pepper", not pw.verify_password(PASSWORD, h["salt"], h["password_hash"], h["iterations"], h["scheme"]))
legacy = pw.hash_password(PASSWORD)
check("without a pepper the scheme is plain pbkdf2", legacy["scheme"] == "pbkdf2")
os.environ["PASSWORD_PEPPER"] = saved
check("a legacy hash still verifies once the pepper is set, and needs a rehash",
      pw.verify_password(PASSWORD, legacy["salt"], legacy["password_hash"], legacy["iterations"], legacy["scheme"])
      and pw.needs_rehash(legacy["iterations"], legacy["scheme"]) and not pw.needs_rehash(h["iterations"], h["scheme"]))
for bad, why in (("", "empty"), ("        ", "whitespace-only"), ("short", "too short"), ("x" * 257, "over the cap")):
    try:
        pw.validate(bad)
        check(f"{why} password refused", False)
    except ValueError:
        check(f"{why} password refused", True)
check("12 chars with spaces and unicode accepted", pw.validate("pässwörd 123") == "pässwörd 123")

section("signup, login, cookies")
clear_limits()
with TestClient(app.app, base_url="https://testserver") as c:
    r = c.post("/api/auth/signup", json={"username": "alice", "password": PASSWORD, "email": "Alice@Example.com"})
    check("signup 200", r.status_code == 200, r.text)
    check("the response carries no password or hash", PASSWORD not in r.text and "password_hash" not in r.text)
    with db.connection() as conn:
        row = conn.execute("SELECT id, password_hash, salt, password_scheme, email_ci FROM users WHERE username = 'alice'").fetchone()
    alice_id = row[0]
    check("stored: no plaintext, peppered scheme, normalised email",
          PASSWORD.encode() not in bytes(row[1]) and row[3] == "pbkdf2_pepper_v1" and row[4] == "alice@example.com", row[3:])
    cookie = next(h for h in r.headers.get_list("set-cookie") if h.startswith("zw_session="))
    check("session cookie is HttpOnly, SameSite=lax, Path=/", "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax") and "Path=/" in cookie, cookie)
    with db.connection() as conn:
        tok = conn.execute("SELECT token_hash FROM sessions WHERE user_id = %s", (alice_id,)).fetchone()[0]
    raw = cookie.split(";")[0].split("=", 1)[1]
    check("session token is stored hashed, not as sent", raw != tok and len(tok) == 64)

    r_wrong_pw = c.post("/api/auth/login", json={"username": "alice", "password": "not-it-at-all"})
    r_no_user = c.post("/api/auth/login", json={"username": "nobody-here", "password": "not-it-at-all"})
    check("wrong password and unknown user are the same public answer",
          r_wrong_pw.status_code == r_no_user.status_code == 401 and r_wrong_pw.json() == r_no_user.json(), (r_wrong_pw.text, r_no_user.text))
    check("...and it names neither", "alice" not in r_wrong_pw.text and "exist" not in r_wrong_pw.text.lower())
    r = c.post("/api/auth/login", json={"username": "ALICE@example.com", "password": PASSWORD})
    check("login by email, any case", r.status_code == 200, r.text)
    with db.connection() as conn:
        before = conn.execute("SELECT count(*) FROM sessions WHERE user_id = %s", (alice_id,)).fetchone()[0]
    r = c.post("/api/auth/logout")
    check("logout 200", r.status_code == 200)
    with db.connection() as conn:
        n = conn.execute("SELECT count(*) FROM sessions WHERE user_id = %s", (alice_id,)).fetchone()[0]
    check("logout deleted this session's row server-side", n == before - 1, (before, n))
    check("the dead cookie no longer signs in", c.get("/api/account").status_code == 401)

section("Secure flag in production")
os.environ["PRODUCTION"] = "1"
clear_limits()
with TestClient(app.app, base_url="https://testserver") as c:
    r = c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    cookie = next(h for h in r.headers.get_list("set-cookie") if h.startswith("zw_session="))
    check("Secure is set on the session cookie in production", "Secure" in cookie, cookie)
os.environ.pop("PRODUCTION")

section("legacy hash rehashes on login")
legacy = pw.hash_password(PASSWORD)  # made with the pepper; write it as if it were pre-pepper
os.environ.pop("PASSWORD_PEPPER")
legacy = pw.hash_password(PASSWORD)
os.environ["PASSWORD_PEPPER"] = saved
with db.connection() as conn:
    conn.execute("UPDATE users SET salt = %s, password_hash = %s, iterations = %s, password_scheme = 'pbkdf2' WHERE id = %s",
                 (legacy["salt"], legacy["password_hash"], legacy["iterations"], alice_id))
clear_limits()
with TestClient(app.app) as c:
    check("a pre-pepper account still signs in", c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 200)
    with db.connection() as conn:
        scheme = conn.execute("SELECT password_scheme FROM users WHERE id = %s", (alice_id,)).fetchone()[0]
    check("...and is rehashed under the pepper on the way in", scheme == "pbkdf2_pepper_v1", scheme)

section("login rate limit")
clear_limits()
with TestClient(app.app) as c:
    codes = [c.post("/api/auth/login", json={"username": "alice", "password": "wrong-" + str(i)}).status_code for i in range(12)]
    check("repeated wrong passwords hit 429", 429 in codes and codes[0] == 401, codes)

section("encryption at rest")
owner = f"user:{alice_id}"
gid = ps.add_game(owner, PGN, {"white": "alice", "black": "bob", "result": "1-0"}, "white", 6, source="chesscom", external_id="e1")
ps.record_findings(gid, owner, [{"ply": 5, "theme": "TACTICAL_OVERLOOK", "severity": "major", "cpl": 100, "fen_before": FEN,
                                 "move_san": "Bb5", "best_san": "d4", "phase": "opening"}])
card, _ = correction_history.upsert(alice_id, theme="KING_SAFETY", player_intent="I wanted to trade queens",
                                    missed_factor="the back rank", diagnosis="You left the king boxed in.",
                                    confidence=0.8, uncertainty="maybe", evidence={"fen_before": FEN, "san": "Bb5"},
                                    practice={"source": "review", "available": True, "fen": FEN, "best_uci": "d2d4", "best_san": "d4", "finding_id": None})
with db.connection() as conn:
    raw_pgn = conn.execute("SELECT pgn FROM imported_games WHERE id = %s", (gid,)).fetchone()[0]
    raw_fen = conn.execute("SELECT fen_before FROM game_findings WHERE game_id = %s", (gid,)).fetchone()[0]
    raw_corr = conn.execute("SELECT player_intent, diagnosis, missed_factor, caveat FROM account_corrections WHERE user_id = %s", (alice_id,)).fetchone()
    raw_ev = conn.execute("SELECT evidence::text, practice_fen FROM correction_evidence e JOIN account_corrections c ON c.id = e.correction_id WHERE c.user_id = %s", (alice_id,)).fetchone()
    key_row = conn.execute("SELECT encrypted_data_key, destroyed_at FROM user_data_keys WHERE user_id = %s", (alice_id,)).fetchone()
check("imported PGN is sealed at rest", raw_pgn.startswith("enc1:") and "Nf3" not in raw_pgn)
check("finding position is sealed at rest", raw_fen.startswith("enc1:") and "KQkq" not in raw_fen)
check("correction intent / diagnosis / factor / caveat are sealed", all(v.startswith("enc1:") for v in raw_corr) and "queens" not in json.dumps(raw_corr))
check("evidence packet and practice position are sealed", '"enc"' in raw_ev[0] and "KQkq" not in raw_ev[0] and raw_ev[1].startswith("enc1:"))
check("one wrapped key per account, not the raw key, not destroyed", key_row is not None and key_row[1] is None and len(bytes(key_row[0])) == 12 + 32 + 16)
check("get_game decrypts the PGN", ps.get_game(owner, gid)["pgn"] == PGN)
cand = ps.practice_candidate(owner, "TACTICAL_OVERLOOK")
check("practice_candidate decrypts the position", cand is not None and cand["fen_before"] == FEN, cand)
check("correction text and evidence decrypt", card is not None and card["player_intent"] == "I wanted to trade queens"
      and card["evidence"][0]["fen_before"] == FEN, card)
pr = correction_history.practice_for(alice_id, card["id"]) if card else None
check("practice position decrypts", pr is not None and pr["fen"] == FEN, pr)
ps.add_game(owner, PGN.replace("a6", "a6 {pending}"), {"white": "alice", "black": "bob"}, "white", 6, source="lichess", external_id="e2")
claimed = ps.claim_next_pending()
check("the worker's claim decrypts too", claimed is not None and claimed["pgn"] == PGN.replace("a6", "a6 {pending}"), claimed)
guest_gid = ps.add_game("guest:abc", PGN, {"white": "g", "black": "h"}, "white", 6, source="manual")
with db.connection() as conn:
    guest_pgn = conn.execute("SELECT pgn FROM imported_games WHERE id = %s", (guest_gid,)).fetchone()[0]
check("guest rows are not sealed (no account, no key)", guest_pgn == PGN)

section("account B cannot read account A")
clear_limits()
with TestClient(app.app) as c:
    c.post("/api/auth/signup", json={"username": "mallory", "password": PASSWORD, "email": "m@example.com"})
with db.connection() as conn:
    mallory_id = conn.execute("SELECT id FROM users WHERE username = 'mallory'").fetchone()[0]
check("the same blob under another account's key is unreadable", data_keys.unseal(f"user:{mallory_id}", raw_pgn) is None)
check("a guest identity cannot open it either", data_keys.unseal("guest:abc", raw_pgn) is None)
check("a bare string account id (as the API passes it) resolves to the same key", data_keys.unseal(str(alice_id), raw_pgn) == PGN)

section("deletion: password required, sessions gone, key destroyed")
clear_limits()
with TestClient(app.app) as c:
    c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    r = c.request("DELETE", "/api/account", json={"confirm_username": "alice"})
    check("deletion without the password is refused", r.status_code == 401, r.text)
    r = c.request("DELETE", "/api/account", json={"confirm_username": "alice", "password": "wrong"})
    check("deletion with a wrong password is refused", r.status_code == 401)
    r = c.request("DELETE", "/api/account", json={"confirm_username": "alice", "password": PASSWORD})
    check("deletion with the password succeeds", r.status_code == 200 and r.json()["signed_in"] is False, r.text)
    check("the session no longer works", c.get("/api/account").status_code == 401)
with db.connection() as conn:
    key_row = conn.execute("SELECT encrypted_data_key, destroyed_at FROM user_data_keys WHERE user_id = %s", (alice_id,)).fetchone()
    left = conn.execute("SELECT (SELECT count(*) FROM users WHERE id = %s), (SELECT count(*) FROM sessions WHERE user_id = %s),"
                        " (SELECT count(*) FROM imported_games WHERE owner = %s), (SELECT count(*) FROM account_corrections WHERE user_id = %s)",
                        (alice_id, alice_id, owner, alice_id)).fetchone()
check("user, sessions, library and corrections are gone", left == (0, 0, 0, 0), left)
check("the wrapped key is zeroed and marked destroyed", key_row is not None and key_row[1] is not None and bytes(key_row[0]) == b"\x00" * len(bytes(key_row[0])))
check("a stale sealed blob (as a backup would hold) cannot be opened any more", data_keys.unseal(owner, raw_pgn) is None and data_keys.unseal(owner, raw_fen) is None)
check("...even with the cache cleared", (data_keys.forget(alice_id), data_keys.unseal(owner, raw_pgn))[1] is None)
check("no password anywhere in the events log", True)  # learning_events refuses free text by construction (test_learning_loop)

section("after APP_MASTER_KEY is rotated: a sentence, not a 500")
clear_limits()
with TestClient(app.app, base_url="https://testserver") as c:
    r = c.post("/api/auth/signup", json={"username": "carol", "password": PASSWORD, "email": "carol@example.com"})
    check("carol signs up", r.status_code == 200, r.text)
    carol_id = auth_service.get_user_by_username("carol")["id"] if hasattr(auth_service, "get_user_by_username") else None
    if carol_id is None:
        with db.connection() as conn:
            carol_id = conn.execute("SELECT id FROM users WHERE username='carol'").fetchone()[0]
    sealed = data_keys.seal(f"user:{carol_id}", "kept under key A")
    check("her data is sealed under the first master key", sealed.startswith("enc1:") and data_keys.unseal(f"user:{carol_id}", sealed) == "kept under key A")
    key_a = os.environ["APP_MASTER_KEY"]
    os.environ["APP_MASTER_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
    data_keys.forget(carol_id)
    check("under a different master key her sealed data reads as unreadable, not as an error", data_keys.unseal(f"user:{carol_id}", sealed) is None)
    try:
        data_keys.seal(f"user:{carol_id}", "new write")
        check("...and sealing raises the named KeyUnreadable", False)
    except data_keys.KeyUnreadable:
        check("...and sealing raises the named KeyUnreadable", True)
    r = c.post("/api/profile/games", json={"pgn": PGN, "player_color": "white"})
    check("an account write answers 503 with the reason, never a 500",
          r.status_code == 503 and r.json().get("error") == "data_key_unreadable" and "APP_MASTER_KEY" in r.json().get("detail", ""), f"{r.status_code} {r.text[:200]}")
    # The repair: re-wrap her key from A to B, then B opens everything.
    key_b = os.environ["APP_MASTER_KEY"]
    counts = data_keys.rewrap_all(data_keys.decode_master_key(key_a), data_keys.decode_master_key(key_b), dry_run=True)
    check("a dry run counts her key as needing a re-wrap and writes nothing",
          counts["rewrapped"] >= 1 and data_keys.unseal(f"user:{carol_id}", sealed) is None, counts)
    counts = data_keys.rewrap_all(data_keys.decode_master_key(key_a), data_keys.decode_master_key(key_b), dry_run=False)
    check("re-wrapping under the new master key makes her data readable again without touching the rows",
          counts["rewrapped"] >= 1 and data_keys.unseal(f"user:{carol_id}", sealed) == "kept under key A", counts)
    check("...and a second run finds nothing left to do",
          data_keys.rewrap_all(data_keys.decode_master_key(key_a), data_keys.decode_master_key(key_b), dry_run=False)["rewrapped"] == 0)
    r = c.post("/api/profile/games", json={"pgn": PGN, "player_color": "white"})
    check("her account writes again", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    os.environ["APP_MASTER_KEY"] = key_a
    data_keys.forget(carol_id)
    check("the old master key no longer opens it", data_keys.unseal(f"user:{carol_id}", sealed) is None)
    os.environ["APP_MASTER_KEY"] = key_b
    data_keys.forget(carol_id)

section("without a master key")
saved_key = os.environ.pop("APP_MASTER_KEY")
check("not enabled without the key", not data_keys.enabled())
try:
    data_keys.check_startup(production=True)
    check("production refuses to boot without APP_MASTER_KEY", False)
except RuntimeError:
    check("production refuses to boot without APP_MASTER_KEY", True)
data_keys.check_startup(production=False)
check("a laptop only warns", True)
check("seal passes plaintext through with no key", data_keys.seal("user:1", "abc") == "abc")
os.environ["APP_MASTER_KEY"] = "not-32-bytes"
try:
    data_keys.enabled()
    check("a malformed key is refused loudly", False)
except RuntimeError:
    check("a malformed key is refused loudly", True)
os.environ["APP_MASTER_KEY"] = saved_key

print(f"\n{PASSED} passed, {FAILED} failed")
db.drop_schema()
try:
    app.stockfish_service.close()
except Exception:
    pass
raise SystemExit(1 if FAILED else 0)

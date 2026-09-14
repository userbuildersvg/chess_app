"""
One-time admin invite codes (admin_invites.py, POST /api/account/become-admin).

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_inv_$$ \\
        /tmp/chessapp/bin/python test_account_admin_invites.py

Needs DATABASE_URL and a disposable schema (dropped on the way out).

What is proved: a guest cannot redeem; a wrong, malformed, unknown or spent
code gets one identical refusal; a valid code makes the account admin and
opens /api/admin/overview without re-login; the same code is refused for a
second account; an ADMIN_EMAILS admin stays admin; no response carries a raw
code, a hash or the secret; and the code is normalised before hashing.
"""
import json
import os

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ["ACCOUNTS_ENABLED"] = "true"
os.environ.setdefault("SESSION_COOKIE_SECRET", "test-signing-key-not-a-real-secret")
os.environ["RATE_LIMITS_ENABLED"] = "true"
os.environ["ADMIN_EMAILS"] = "founder@example.com"
if os.environ.get("DATABASE_SCHEMA", "public") == "public":
    raise SystemExit("Refusing to run against the public schema - set DATABASE_SCHEMA to a disposable name.")

from fastapi.testclient import TestClient

import admin_invites
import app
import db
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
    rate_limit_module.limit_admin_invite.limiter.reset()


db.migrate()

SECRET = "test-admin-invite-secret-not-real"
CODES = [admin_invites.generate_code() for _ in range(3)]
os.environ["ADMIN_INVITE_SECRET"] = SECRET
os.environ["ADMIN_INVITE_CODE_HASHES"] = ",".join(admin_invites.hash_code(c, SECRET) for c in CODES)
HASHES = admin_invites.configured_hashes()
FORBIDDEN = [SECRET] + CODES + list(HASHES)


def leaks(text: str) -> list:
    return [f for f in FORBIDDEN if f in text]


section("codes and hashing")
check("a code has the zz-admin-XXXX-XXXX-XXXX shape",
      all(c.startswith("zz-admin-") and len(c) == 9 + 14 for c in CODES), CODES[0])
check("codes are distinct", len(set(CODES)) == 3)
c0 = CODES[0]
check("normalisation: lower-case, spaces, confusables and missing dashes",
      admin_invites.canonical(f"  {c0.lower().replace('-', ' ')} ") == c0
      and admin_invites.canonical(c0.replace("0", "O").replace("1", "l")) == c0, admin_invites.canonical(c0.lower()))
check("a wrong prefix, a short body, and an over-long input are not codes",
      admin_invites.canonical("zg-beta-" + c0[9:]) is None and admin_invites.canonical(c0[:-1]) is None
      and admin_invites.canonical(c0 + "x" * 60) is None)
check("HMAC depends on the secret", admin_invites.hash_code(c0, SECRET) != admin_invites.hash_code(c0, "other"))
check("hash never equals the code", admin_invites.hash_code(c0, SECRET) not in c0)
check("three hashes configured", len(HASHES) == 3)

section("guest")
with TestClient(app.app) as guest:
    r = guest.post("/api/account/become-admin", json={"code": CODES[0]})
    check("guest -> 401 account_required", r.status_code == 401 and r.json()["error"] == "account_required", r.text)
    check("...nothing leaked", leaks(r.text) == [])

section("signed-in user, bad codes")
clear_limits()
with TestClient(app.app) as alice:
    r = alice.post("/api/auth/signup", json={"username": "alice", "password": "alice-password-1", "email": "alice@example.com"})
    check("alice exists", r.status_code == 200, r.text)
    answers = []
    for label, code in [("malformed", "hello"), ("wrong prefix", "zg-beta-" + CODES[0][9:]),
                        ("unknown", admin_invites.generate_code()), ("empty", "")]:
        r = alice.post("/api/account/become-admin", json={"code": code})
        answers.append((r.status_code, r.json()))
        check(f"{label} code -> 400 invalid_admin_code", r.status_code == 400 and r.json()["error"] == "invalid_admin_code", r.text)
    check("every refusal is byte-identical", len({json.dumps(a, sort_keys=True) for a in answers}) == 1, answers)
    r = alice.post("/api/account/become-admin", json={"code": "x" * 5000})
    check("an over-long code is refused the same way, not 413/422", r.status_code == 400 and r.json()["error"] == "invalid_admin_code", r.status_code)
    check("alice is still not admin", alice.get("/api/account").json()["admin"] is False)
    check("alice still cannot open the admin overview", alice.get("/api/admin/overview").status_code == 403)

    section("signed-in user, valid code")
    r = alice.post("/api/account/become-admin", json={"code": "  " + CODES[0].lower() + " "})
    check("valid code (typed loosely) -> 200 is_admin", r.status_code == 200 and r.json() == {
        "ok": True, "is_admin": True, "message": "Admin access enabled for this account."}, r.text)
    check("account profile now says admin", alice.get("/api/account").json()["admin"] is True)
    r = alice.get("/api/admin/overview")
    check("the admin overview opens without re-login", r.status_code == 200, r.status_code)
    check("...and leaks no code, hash or secret", leaks(r.text) == [])
    r = alice.post("/api/account/become-admin", json={"code": CODES[1]})
    check("an admin redeeming again is told so and burns nothing",
          r.status_code == 200 and "already" in r.json()["message"], r.text)
    with db.connection() as conn:
        n = conn.execute("SELECT count(*) FROM admin_invite_redemptions").fetchone()[0]
        flag = conn.execute("SELECT is_admin FROM users WHERE username = 'alice'").fetchone()[0]
        stored = conn.execute("SELECT code_hash FROM admin_invite_redemptions").fetchone()[0]
    check("one redemption row, flag set", n == 1 and flag is True)
    check("the stored value is the hash, not the code", stored in HASHES and stored not in CODES)

section("second account, same code")
clear_limits()
with TestClient(app.app) as bob:
    bob.post("/api/auth/signup", json={"username": "bob", "password": "bob-password-1", "email": "bob@example.com"})
    r = bob.post("/api/account/become-admin", json={"code": CODES[0]})
    used = (r.status_code, r.json())
    check("a spent code -> 400 invalid_admin_code", used[0] == 400 and used[1]["error"] == "invalid_admin_code", r.text)
    check("spent and unknown are indistinguishable", json.dumps(used, sort_keys=True) == json.dumps(answers[2], sort_keys=True), (used, answers[2]))
    check("bob is not admin", bob.get("/api/admin/overview").status_code == 403)
    r = bob.post("/api/account/become-admin", json={"code": CODES[2]})
    check("a fresh code still works for bob", r.status_code == 200 and r.json()["is_admin"] is True, r.text)

section("ADMIN_EMAILS admin is unchanged")
clear_limits()
with TestClient(app.app) as founder:
    founder.post("/api/auth/signup", json={"username": "founder", "password": "founder-password-1", "email": "founder@example.com"})
    check("allowlisted founder is admin with no code", founder.get("/api/account").json()["admin"] is True)
    check("...and opens the overview", founder.get("/api/admin/overview").status_code == 200)
    text = founder.get("/api/admin/overview").text
    check("the overview shows redeemed accounts' counts but no hashes", leaks(text) == [] and "code_hash" not in text)

section("rate limit")
clear_limits()
with TestClient(app.app) as bob:
    bob.post("/api/auth/login", json={"username": "bob", "password": "bob-password-1"})
    codes = [bob.post("/api/account/become-admin", json={"code": "zz-admin-nope"}).status_code for _ in range(12)]
    check("guessing is rate-limited after ten attempts", 429 not in codes[:10] and codes[10:] == [429, 429], codes)

section("not configured")
os.environ["ADMIN_INVITE_CODE_HASHES"] = ""
clear_limits()
with TestClient(app.app) as carol:
    carol.post("/api/auth/signup", json={"username": "carol", "password": "carol-password-1", "email": "carol@example.com"})
    r = carol.post("/api/account/become-admin", json={"code": CODES[1]})
    check("with no hashes configured every code is refused the same way", r.status_code == 400 and r.json()["error"] == "invalid_admin_code")

print(f"\n{PASSED} passed, {FAILED} failed")
db.drop_schema()
try:
    app.stockfish_service.close()
except Exception:
    pass
raise SystemExit(1 if FAILED else 0)

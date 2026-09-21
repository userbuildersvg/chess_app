"""
The closed beta gate: authorization, redemption, and the ways round it.

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_beta_$$ \\
        /tmp/chessapp/bin/python test_beta_access.py

Needs Stockfish and DATABASE_URL. Run it against a disposable schema, never
`public` - it mints codes and creates accounts, and this file drops the schema
on the way out (CLAUDE.md section 6).

WHAT THIS FILE IS FOR
---------------------
The beta gate protects account/private surfaces while Shipaton exposes the
existing guest Play/Learn/Review loop. Its failure mode is silent: a route that
is accidentally open serves perfectly good responses to people who should never
have reached it, and nothing in a log or a browser says so. So the claims below
are asserted rather than reasoned about.

Nine of them, and why each is here:

1. **A guarded route refuses an unauthorized caller** - and refuses it the same
   way whether the request comes from a page, a bare fetch, or curl, because
   the check is in middleware and not in the client.
2. **The gate is deny-by-default.** Every /api route except a named handful is
   closed, so a route added next month is closed on the day it is written.
   This is asserted by ENUMERATING the app's real routes, not by listing the
   ones somebody remembered.
3. **Nothing the client sends is an input.** No header, body field, cookie or
   query parameter grants access.
4. **A code is spent exactly once**, including under concurrency, including
   when two threads race the same code.
5. **Disabled, expired and used-up codes fail** - with the same message as a
   code that never existed, so the endpoint is not an oracle.
6. **Access survives sign-out and follows the account**, which is what "the
   code is never required again" has to mean.
7. **Rate limiting refuses a guesser** on both buckets.
8. **The code is never stored**, and a database dump is not a list of working
   invitations.
9. **It fails closed** - unset configuration means refused, never allowed.
"""

import os
import threading
import time

os.environ.setdefault("DISABLE_LANGFLOW", "true")
# Before importing app, for the reason test_accounts_postgres.py gives: the
# account resolver is wired into the identity middleware at import time.
os.environ["ACCOUNTS_ENABLED"] = "true"
os.environ["BETA_ACCESS_REQUIRED"] = "true"
os.environ.setdefault("SESSION_COOKIE_SECRET", "test-signing-key-not-a-real-secret")
os.environ.setdefault("BETA_CODE_PEPPER", "test-beta-pepper-not-a-real-secret")

if os.environ.get("DATABASE_SCHEMA", "public") == "public":
    raise SystemExit(
        "Refusing to run against the public schema - this suite mints beta codes "
        "and creates accounts. Set DATABASE_SCHEMA to a disposable name, e.g. "
        "DATABASE_SCHEMA=zwtest_beta_$$"
    )

from fastapi.testclient import TestClient

import app
import auth_api
import beta_api
import beta_gate
import beta_service
import db
import rate_limit as rate_limit_module

PASSED = 0
FAILED = 0


def check(label, condition, detail=None):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f" - {detail}" if detail else ""))


def section(name):
    print(f"\n--- {name} ---")


def clear_limits():
    """Forget recorded attempts.

    This suite redeems far more often than a person ever would - a real tester
    types one code once - so without this the buckets fill partway through and
    every later redemption fails with a 429 that reads exactly like a broken
    code. The limits themselves are proven deliberately in section 7 rather
    than assumed here.
    """
    rate_limit_module.limit_beta_redeem.limiter.reset()
    rate_limit_module.redeem_by_identity.reset()
    rate_limit_module.limit_login.limiter.reset()
    rate_limit_module.limit_signup.limiter.reset()


def fresh_client():
    """A client with its own cookie jar - i.e. a different visitor."""
    return TestClient(app.app)


def mint(**kwargs):
    """One code, minted the way the admin tool mints them."""
    return beta_service.create_codes(1, **kwargs)[0]


db.migrate()


# ===========================================================================
# 1. A guarded route refuses an unauthorized caller
#
# The first claim, and the one everything else rests on. Note that the client
# used here is a bare HTTP client with a cookie jar: there is no React, no
# page, and no JavaScript anywhere in this file. That is the point - if the
# gate held only in the browser, every assertion in this section would pass
# from a page and fail from here.
# ===========================================================================

section("a guarded route refuses an unauthorized caller")

anon = fresh_client()

_probe_routes = [
    ("GET", "/api/profile"),
    ("GET", "/api/account"),
    ("GET", "/api/admin/overview"),
    ("GET", "/api/learning-loop/funnel"),
]
for method, path in _probe_routes:
    r = anon.request(method, path, json={})
    check(f"{method} {path} is refused", r.status_code == 403, r.status_code)
    if r.status_code == 403:
        check(f"{path} says why in a way a client can branch on",
              r.json().get("beta_required") is True, r.json())

# 403, not 401. A signed-in account with no invitation is not a caller who
# should be sent to a sign-in form, and the frontend's account layer treats
# 401 as a dead session.
check("the refusal is 403 and never 401",
      anon.get("/api/profile").status_code == 403)

# The endpoint must not run. A 403 that has already reset the board, spent
# Gemini quota or taken the engine lock is not a refusal, it is a refusal
# notice attached to a completed request.
check("the public demo can reset its guest game",
      anon.post("/api/reset").status_code == 200)


# ===========================================================================
# 2. Deny by default, enumerated
#
# The single most valuable assertion in this file. It walks the routes the
# application actually declares - not a list somebody typed - and insists that
# every one of them under /api is either closed or on the named exception list.
#
# A route added later with no thought given to the beta is therefore closed,
# and a route deliberately opened has to be added to `beta_gate.OPEN_PATHS`,
# which is a small, readable, reviewable edit somebody has to look straight at.
# ===========================================================================

section("deny by default")

_api_routes = sorted({
    r.path for r in app.app.routes
    if getattr(r, "path", "").startswith("/api/")
})
check("the app really has a lot of API routes to protect", len(_api_routes) > 40, len(_api_routes))

_unexpectedly_open = [p for p in _api_routes if beta_gate.is_open(p)]
_expected_open = sorted(beta_gate.OPEN_PATHS | {
    p for p in _api_routes if any(p.startswith(x) for x in beta_gate.OPEN_PREFIXES)
})
check("every open API route is one that was deliberately opened",
      sorted(_unexpectedly_open) == sorted(p for p in _expected_open if p in _api_routes),
      sorted(set(_unexpectedly_open) - set(_expected_open)))

# Named individually as well, because the enumeration above would also pass if
# somebody opened everything.
check("/api/health is open (Render's check carries no cookie)", beta_gate.is_open("/api/health"))
check("/api/auth/login is open (a returning tester's grant is on their account)",
      beta_gate.is_open("/api/auth/login"))
check("/api/auth/signup is closed (redeeming comes before account creation)",
      not beta_gate.is_open("/api/auth/signup"))
check("/api/beta/redeem is open (it is the door)", beta_gate.is_open("/api/beta/redeem"))
check("/api/billing/status is open for the caller-scoped guest Free explanation", beta_gate.is_open("/api/billing/status"))
check("/api/move is open for the public guest demo", beta_gate.is_open("/api/move"))
check("/api/sandbox/session is open for the public guest demo", beta_gate.is_open("/api/sandbox/session"))
check("/api/postmortem/import is open for the public guest demo", beta_gate.is_open("/api/postmortem/import"))
check("/api/learning-loop/themes is open for the public correction loop", beta_gate.is_open("/api/learning-loop/themes"))
check("/api/learning-loop/funnel remains private operational data", not beta_gate.is_open("/api/learning-loop/funnel"))
check("/api/account/settings is NOT open", not beta_gate.is_open("/api/account/settings"))
check("the SPA's own HTML is not gated", beta_gate.is_open("/"), "the landing page has to load")

# A path that merely starts with an open one must not inherit it. "/api/health"
# being open must not open "/api/healthz" or "/api/health/../move".
check("an open path does not open its neighbours",
      not beta_gate.is_open("/api/healthz") and not beta_gate.is_open("/api/auth/loginx"))

locked_signup = fresh_client()
_locked_signup_response = locked_signup.post(
    "/api/auth/signup",
    json={"username": "noinvitation", "password": "correct-horse-battery"},
)
check("an uninvited caller cannot create an account through the API",
      _locked_signup_response.status_code == 403, _locked_signup_response.status_code)
with db.connection() as _c:
    _uninvited_user = _c.execute(
        "SELECT id FROM users WHERE username_ci = %s", ("noinvitation",)
    ).fetchone()
check("the refused signup created no account", _uninvited_user is None, _uninvited_user)


# ===========================================================================
# 3. Nothing the client sends is an input
#
# Every plausible bypass someone would try from DevTools, asserted rather than
# argued. None of these can work, because the decision reads a server-signed
# cookie and a database row - but "cannot work" is what a test is for.
# ===========================================================================

section("nothing the client sends grants access")

_forged = [
    ("a header", {"headers": {"X-Beta-Access": "true", "Authorization": "Bearer anything"}}),
    ("a body field", {"json": {"beta_access": True, "identity": "user:1", "has_access": True}}),
    ("a query parameter", {"params": {"beta": "true", "beta_access": "1"}}),
]
for label, kwargs in _forged:
    r = fresh_client().post("/api/account/password", **kwargs)
    check(f"{label} does not grant access", r.status_code == 403, r.status_code)

# A guest cookie the server never minted. `verify_guest_cookie` rejects it
# before the gate is reached, so the caller is a brand-new guest with no grant
# - the same answer, by a different route, and both of them "no".
forged_cookie = fresh_client()
forged_cookie.cookies.set("zw_guest", "guest:deadbeefdeadbeefdeadbeefdeadbeef.0" * 1)
check("an unsigned guest cookie does not grant access",
      forged_cookie.get("/api/profile").status_code == 403)

# An invented session token.
forged_session = fresh_client()
forged_session.cookies.set("zw_session", "a" * 64)
check("an invented session token does not grant access",
      forged_session.get("/api/profile").status_code == 403)


# ===========================================================================
# 4. Redemption: one code, one redeemer, once
# ===========================================================================

section("redemption")

clear_limits()

code = mint(created_by="test")
check("a minted code has the advertised shape",
      code.startswith("ZG-BETA-") and len(code) == len("ZG-BETA-XXXX-XXXX"), code)
check("it uses only the unambiguous alphabet",
      all(ch in beta_service.ALPHABET for ch in code.replace("-", "")[len("ZGBETA"):]), code)

tester = fresh_client()
check("the tester is locked out before redeeming",
      tester.get("/api/profile").status_code == 403)
check("but can ask for the beta status", tester.get("/api/beta/status").status_code == 200)
check("and the status says they have no access",
      tester.get("/api/beta/status").json()["has_access"] is False)

r = tester.post("/api/beta/redeem", json={"code": code})
check("redeeming a good code succeeds", r.status_code == 200, r.text)
check("and the gate opens on the very next request",
      tester.get("/api/profile").status_code != 403,
      "the cache must be invalidated on redemption, not merely expire")
check("the status endpoint agrees", tester.get("/api/beta/status").json()["has_access"] is True)

# Case, dashes and whitespace are the operator's problem, not the tester's.
clear_limits()
sloppy_code = mint()
sloppy = fresh_client()
messy = "  " + sloppy_code.replace("-", "").lower() + "  "
check("a code typed without dashes, lowercase, with spaces still works",
      sloppy.post("/api/beta/redeem", json={"code": messy}).status_code == 200)

# One-time. The same code offered by a different visitor is refused.
clear_limits()
second = fresh_client()
r2 = second.post("/api/beta/redeem", json={"code": code})
check("a spent code is refused for a second visitor", r2.status_code == 400, r2.status_code)
check("and that visitor is still locked out", second.get("/api/profile").status_code == 403)

# Idempotent for the same visitor: a double-clicked Continue must not spend a
# second code or produce an error.
clear_limits()
again = tester.post("/api/beta/redeem", json={"code": mint()})
check("re-redeeming from a browser that already has access succeeds harmlessly",
      again.status_code == 200 and again.json()["already_had_access"] is True, again.text)

# ...and did not consume the code it was offered. This is the assertion that
# catches an idempotency check placed after the counter increment.
_usage_before = beta_service.usage()["redemptions"]
clear_limits()
spare = mint()
tester.post("/api/beta/redeem", json={"code": spare})
check("and spent nothing", beta_service.usage()["redemptions"] == _usage_before,
      "an already-granted identity must not consume a code")
still_good = fresh_client()
clear_limits()
check("so the offered code is still redeemable by somebody else",
      still_good.post("/api/beta/redeem", json={"code": spare}).status_code == 200)


# ===========================================================================
# 5. Bad codes, all refused, all identically
# ===========================================================================

section("codes that must fail")

clear_limits()

disabled_code = mint()
beta_service.set_enabled(disabled_code, False)
r_disabled = fresh_client().post("/api/beta/redeem", json={"code": disabled_code})
check("a disabled code fails", r_disabled.status_code == 400, r_disabled.status_code)

clear_limits()
expired_code = mint(expires_at=time.time() - 60)
r_expired = fresh_client().post("/api/beta/redeem", json={"code": expired_code})
check("an expired code fails", r_expired.status_code == 400, r_expired.status_code)

clear_limits()
r_unknown = fresh_client().post("/api/beta/redeem", json={"code": "ZG-BETA-ZZZZ-ZZZZ"})
check("a code that never existed fails", r_unknown.status_code == 400, r_unknown.status_code)

clear_limits()
r_garbage = fresh_client().post("/api/beta/redeem", json={"code": "not a code at all"})
check("a malformed code fails", r_garbage.status_code == 400, r_garbage.status_code)

# The one that matters: they are indistinguishable. Any difference between
# them tells a guesser which of their attempts hit a real row, which is the
# only thing a guesser is missing.
_answers = {r.status_code: r.json()["detail"]
            for r in (r_disabled, r_expired, r_unknown, r_garbage)}
check("disabled, expired, unknown and malformed answer identically",
      len({(r.status_code, r.json()["detail"])
           for r in (r_disabled, r_expired, r_unknown, r_garbage)}) == 1,
      _answers)

# max_uses is obeyed, in both directions.
clear_limits()
shared = mint(max_uses=2)
u1, u2, u3 = fresh_client(), fresh_client(), fresh_client()
check("a max_uses=2 code admits the first", u1.post("/api/beta/redeem", json={"code": shared}).status_code == 200)
clear_limits()
check("and the second", u2.post("/api/beta/redeem", json={"code": shared}).status_code == 200)
clear_limits()
check("and refuses the third", u3.post("/api/beta/redeem", json={"code": shared}).status_code == 400)

# Revocation: taking access away from somebody who already redeemed.
clear_limits()
doomed_code = mint()
doomed = fresh_client()
doomed.post("/api/beta/redeem", json={"code": doomed_code})
check("the redeemer has access", doomed.get("/api/profile").status_code != 403)
_doomed_identity = doomed.get("/api/auth/me")  # establishes the cookie
_doomed_guest = doomed.cookies.get("zw_guest").rsplit(".", 1)[0]
beta_service.revoke_identity(_doomed_guest)
check("revoking their grant locks them out again",
      doomed.get("/api/profile").status_code == 403)
check("and the code is NOT returned to the pool",
      fresh_client().post("/api/beta/redeem", json={"code": doomed_code}).status_code == 400,
      "a leaked invitation must not become redeemable again by revoking its holder")


# ===========================================================================
# 6. Concurrency: two threads, one code, one winner
#
# The failure this guards against is two testers redeeming the same code in
# the same instant and both being admitted - which would make max_uses a
# suggestion. `SELECT ... FOR UPDATE` plus the `uses < max_uses` guard in the
# UPDATE's own WHERE clause is what makes exactly one of them win.
# ===========================================================================

section("concurrency")

clear_limits()
contested = mint()
results = []
barrier = threading.Barrier(8)


def race():
    client = fresh_client()
    barrier.wait()
    try:
        results.append(client.post("/api/beta/redeem", json={"code": contested}).status_code)
    except Exception as e:  # a 429 raised as an exception would skew the count
        results.append(type(e).__name__)


threads = [threading.Thread(target=race) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("exactly one of eight simultaneous redemptions succeeded",
      results.count(200) == 1, results)
with db.connection() as _c:
    _uses = _c.execute(
        "SELECT uses FROM beta_codes WHERE code_hash = %s",
        (beta_service.hash_code(beta_service.canonical(contested)),),
    ).fetchone()[0]
check("and the counter says so", _uses == 1, _uses)


# ===========================================================================
# 7. Rate limiting
# ===========================================================================

section("rate limiting")

clear_limits()
guesser = fresh_client()
_codes_seen = []
for i in range(15):
    _codes_seen.append(
        guesser.post("/api/beta/redeem", json={"code": "ZG-BETA-2222-333%d" % (i % 10)}).status_code
    )
check("a guesser is cut off by the per-IP bucket", 429 in _codes_seen, _codes_seen)
check("and was cut off well before fifteen attempts",
      _codes_seen.index(429) <= 10, _codes_seen)

# The per-identity bucket, exercised directly: the per-IP one does nothing
# against a distributed caller, and an identity is the one thing a distributed
# attempt still has to hold, because the grant is written onto it.
rate_limit_module.redeem_by_identity.reset()
_identity_refused = False
for i in range(25):
    try:
        rate_limit_module.redeem_by_identity.check("guest:aaaa")
    except Exception:
        _identity_refused = True
        break
check("the per-identity bucket refuses a flood too", _identity_refused)
check("and a different identity is unaffected",
      rate_limit_module.redeem_by_identity.check("guest:bbbb") is None,
      "one tester's typos must not lock out the office they share an IP with")
clear_limits()


# ===========================================================================
# 8. The code is never stored
# ===========================================================================

section("storage")

_stored_code = mint()
_canonical = beta_service.canonical(_stored_code)
with db.connection() as _c:
    _row = _c.execute(
        "SELECT code_hash, code_label FROM beta_codes WHERE code_hash = %s",
        (beta_service.hash_code(_canonical),),
    ).fetchone()
check("the code is stored as a hash", _row is not None and len(_row[0]) == 64, _row)
check("the plaintext appears nowhere in the row", _stored_code not in str(_row), _row)
check("the label identifies without redeeming",
      _row[1].startswith("ZG-BETA-") and _row[1].endswith("????"), _row[1])

# A dump of every row must not contain a working invitation.
with db.connection() as _c:
    _dump = str(_c.execute("SELECT * FROM beta_codes").fetchall())
check("a dump of the whole table holds no usable code",
      _stored_code not in _dump and _canonical not in _dump)

# The hash is keyed. Plain SHA-256 of a 40-bit code would be sweepable offline
# in minutes; the pepper is what makes a leaked table worthless.
import hashlib
check("the stored hash is not a bare SHA-256 of the code",
      _row[0] != hashlib.sha256(_canonical.encode()).hexdigest(),
      "the pepper is what makes a leaked table useless")

# Codes are unpredictable. Not a proof of entropy - just a guard against
# somebody swapping `secrets` for `random` with a fixed seed, which has
# happened to better projects than this one.
_batch = beta_service.create_codes(40)
check("forty codes are forty different codes", len(set(_batch)) == 40)


# ===========================================================================
# 9. Access follows the account, not the browser
#
# "The code is never required again" is a product promise, and this is what
# has to be true for it: the grant moves onto the account at signup, survives
# sign-out, and arrives on a browser that has never seen a code.
# ===========================================================================

section("access follows the account")

clear_limits()
signup_code = mint()
person = fresh_client()
person.post("/api/beta/redeem", json={"code": signup_code})
check("redeemed as a guest, they pass the private-surface gate", person.get("/api/profile").status_code != 403)

_signup = person.post("/api/auth/signup",
                      json={"username": "betatester", "password": "correct-horse-battery"})
check("they can create an account", _signup.status_code == 200, _signup.text)
check("and still have access afterwards", person.get("/api/profile").status_code == 200)

# Read from the database rather than inferred from the response: the grant
# moving onto the account is the whole mechanism, and a 200 from /api/profile
# would still be served if the guest grant had merely survived.
with db.connection() as _c:
    _flag = _c.execute(
        "SELECT beta_access FROM users WHERE username_ci = %s", ("betatester",)
    ).fetchone()
check("users.beta_access is set on the account", _flag is not None and _flag[0] is True, _flag)

# Signing out hands the browser a brand-new guest identity, which has never
# redeemed anything - so it is locked out, correctly, and signing back in
# restores access. That is the behaviour, not a bug: access belongs to the
# account.
person.post("/api/auth/logout")
check("after signing out, the fresh guest identity has no access",
      person.get("/api/profile").status_code == 403,
      "the grant belongs to the account, and the new guest is not it")

clear_limits()
_login = person.post("/api/auth/login",
                     json={"username": "betatester", "password": "correct-horse-battery"})
check("signing back in works", _login.status_code == 200, _login.text)
check("and restores access without a code", person.get("/api/profile").status_code == 200)

# A browser that has never seen a code at all.
clear_limits()
new_device = fresh_client()
check("a brand-new browser is locked out of private surfaces", new_device.get("/api/profile").status_code == 403)
new_device.post("/api/auth/login",
                json={"username": "betatester", "password": "correct-horse-battery"})
check("but signing in on it grants access, no code required",
      new_device.get("/api/profile").status_code == 200)

# Google sign-in has one extra edge: its callback signs in an existing subject
# and creates an account for a new one. The callback has to remain open for a
# returning tester on a fresh browser, while the account-creation half must
# obey the same redeem-first rule as password signup.
_real_google_configured = auth_api.google_oauth.configured
_real_google_exchange = auth_api.google_oauth.exchange_code
auth_api.google_oauth.configured = lambda: True
auth_api.google_oauth.exchange_code = lambda code, redirect_uri: {
    "subject": "beta-google-subject",
    "email": "beta-google@example.com",
    "email_verified": True,
    "name": "Beta Google",
}

try:
    google_locked = fresh_client()
    google_locked.get("/api/auth/google/start", follow_redirects=False)
    _google_state = google_locked.cookies.get(auth_api.OAUTH_STATE_COOKIE)
    _google_locked_callback = google_locked.get(
        f"/api/auth/google/callback?code=abc&state={_google_state}",
        follow_redirects=False,
    )
    check("an uninvited Google subject is returned to the access page",
          _google_locked_callback.status_code == 302, _google_locked_callback.status_code)
    check("and the callback created no account",
          auth_api.auth_service.find_by_federated("google", "beta-google-subject") is None)

    google_invited = fresh_client()
    google_invited.post("/api/beta/redeem", json={"code": mint()})
    google_invited.get("/api/auth/google/start", follow_redirects=False)
    _google_state = google_invited.cookies.get(auth_api.OAUTH_STATE_COOKIE)
    google_invited.get(
        f"/api/auth/google/callback?code=abc&state={_google_state}",
        follow_redirects=False,
    )
    _google_user = auth_api.auth_service.find_by_federated("google", "beta-google-subject")
    check("an invited guest can create a Google account",
          _google_user is not None and google_invited.get("/api/profile").status_code == 200,
          _google_user)

    google_returning = fresh_client()
    google_returning.get("/api/auth/google/start", follow_redirects=False)
    _google_state = google_returning.cookies.get(auth_api.OAUTH_STATE_COOKIE)
    google_returning.get(
        f"/api/auth/google/callback?code=abc&state={_google_state}",
        follow_redirects=False,
    )
    check("the same Google account can sign in on a fresh unredeemed browser",
          google_returning.get("/api/profile").status_code == 200)
finally:
    auth_api.google_oauth.configured = _real_google_configured
    auth_api.google_oauth.exchange_code = _real_google_exchange

# An account that never redeemed anything is refused, even signed in. This is
# the assertion that would fail if beta_access defaulted to true, or if being
# signed in were mistaken for being invited.
clear_limits()
outsider_code = mint()
outsider = fresh_client()
outsider.post("/api/beta/redeem", json={"code": outsider_code})
outsider.post("/api/auth/signup", json={"username": "outsider", "password": "correct-horse-battery"})
with db.connection() as _c:
    _c.execute("UPDATE users SET beta_access = false WHERE username_ci = %s", ("outsider",))
beta_service.invalidate()
check("a signed-in account with no invitation is refused",
      outsider.get("/api/profile").status_code == 403)
check("and is told so as a beta problem, not an auth problem",
      outsider.get("/api/profile").json().get("beta_required") is True)


# ===========================================================================
# 9b. A chosen key, for the operator's own use
#
# `create_custom_code` stores a code somebody picked instead of one that was
# drawn. It is a second way into the same table, so the thing worth asserting
# is that it is not a second, weaker code path: same canonicalisation, same
# keyed hash, same refusals, and a multi-use key that genuinely admits the same
# person on more than one device.
# ===========================================================================

section("a chosen key")

clear_limits()

# `U` is not in the generation alphabet - Crockford leaves it out so a random
# draw cannot spell something unfortunate - but a person choosing their own key
# may use it. This is the exact case the owner key needed.
check("U is never generated", "U" not in beta_service.ALPHABET)
check("but it is accepted when typed", "U" in beta_service.INPUT_ALPHABET)
check("so a chosen code containing U canonicalises",
      beta_service.canonical("zg-beta-qu1z-4fun") == "ZG-BETA-QU1Z-4FUN",
      beta_service.canonical("zg-beta-qu1z-4fun"))

owner_key = beta_service.create_custom_code(
    "ZG-BETA-TEST-KEYU", created_by="suite", max_uses=3, notes="chosen key")
check("a chosen code is stored in canonical form", owner_key == "ZG-BETA-TEST-KEYU")

# Multi-use, because a key that admits its owner once is not an owner key.
first, second, third, fourth = (fresh_client() for _ in range(4))
check("the chosen key admits the first device",
      first.post("/api/beta/redeem", json={"code": "zg beta test keyu"}).status_code == 200)
clear_limits()
check("and the second", second.post("/api/beta/redeem",
                                    json={"code": owner_key}).status_code == 200)
clear_limits()
check("and the third", third.post("/api/beta/redeem",
                                  json={"code": owner_key}).status_code == 200)
clear_limits()
check("and stops at max_uses like any other code",
      fourth.post("/api/beta/redeem", json={"code": owner_key}).status_code == 400)
check("the devices that got in can actually use the app",
      first.get("/api/profile").status_code != 403
      and third.get("/api/profile").status_code != 403)

# It is not stored in the clear either. A chosen key is still a credential, and
# it is the one credential whose loss costs the most.
with db.connection() as _c:
    _row = _c.execute(
        "SELECT code_hash FROM beta_codes WHERE code_hash = %s",
        (beta_service.hash_code(owner_key),)).fetchone()
    _dump = str(_c.execute("SELECT * FROM beta_codes").fetchall())
check("the chosen key is stored as a keyed hash too", _row is not None)
check("and its plaintext is nowhere in the table", "TESTKEYU" not in _dump.replace("-", ""))

# One character off is refused, with the same message as everything else.
clear_limits()
_near = fresh_client().post("/api/beta/redeem", json={"code": "ZG-BETA-TEST-KEYV"})
check("a one-character variant is refused", _near.status_code == 400, _near.status_code)
check("with the same message as any other bad code",
      _near.json()["detail"] == beta_service.INVALID_CODE_MESSAGE)

# Storing the same chosen code twice is an error, not a silent no-op that
# would leave an operator believing they had changed max_uses.
try:
    beta_service.create_custom_code(owner_key)
    check("a duplicate chosen code is refused", False, "it was accepted")
except beta_service.BetaError as e:
    check("a duplicate chosen code is refused and says so", "already exists" in e.message)

# And a malformed one cannot be stored at all.
for bad in ("ZG-BETA-ABC", "not a code", "ZG-BETA-AAAA-AAA", "ZG-BETA-AAAA-AAAAA", ""):
    try:
        beta_service.create_custom_code(bad)
        check(f"a malformed chosen code ({bad}) is refused", False, "it was accepted")
    except beta_service.BetaError:
        check(f"a malformed chosen code ({bad}) is refused", True)
clear_limits()


# ===========================================================================
# 10. Fail closed
#
# The default matters more than the code. A gate that opens when a variable is
# unset is a gate that a fresh container, a lost environment or a forgotten
# test opens silently - and every one of those looks like nothing at all from
# the inside.
# ===========================================================================

section("fail closed")

_saved = os.environ.pop("BETA_ACCESS_REQUIRED", None)
check("with BETA_ACCESS_REQUIRED unset, the gate is ON", beta_service.beta_required())
os.environ["BETA_ACCESS_REQUIRED"] = "yes-please"
check("and any value other than 'false' leaves it on", beta_service.beta_required())
os.environ["BETA_ACCESS_REQUIRED"] = "false"
check("only an explicit false opens the app", not beta_service.beta_required())
check("and then a stranger can play",
      fresh_client().get("/api/status").status_code == 200)
os.environ["BETA_ACCESS_REQUIRED"] = _saved or "true"
beta_service.invalidate()
check("turning it back on locks them out again",
      fresh_client().get("/api/profile").status_code == 403)

# An unset pepper must raise rather than fall back to a per-process key, which
# would mint codes that never validate anywhere else.
_pepper = os.environ.pop("BETA_CODE_PEPPER")
_secret = os.environ.pop("SESSION_COOKIE_SECRET", None)
beta_service._pepper_cache = None
try:
    beta_service.hash_code("ZG-BETA-AAAA-AAAA")
    check("hashing without a pepper raises", False, "it returned a hash")
except beta_service.BetaError:
    check("hashing without a pepper raises rather than inventing a key", True)
finally:
    os.environ["BETA_CODE_PEPPER"] = _pepper
    if _secret is not None:
        os.environ["SESSION_COOKIE_SECRET"] = _secret
    beta_service._pepper_cache = None

# The status endpoint tells an unauthorized caller nothing but their own state.
_status_body = fresh_client().get("/api/beta/status").text
for leak in ("code", "hash", "uses", "max_uses", "ZG-BETA"):
    check(f"the status endpoint leaks no '{leak}'", leak not in _status_body, _status_body)

# /api/health says whether the door is shut, which is how an operator confirms
# from outside that a deploy did not ship with it open.
_health = fresh_client().get("/api/health").json()
check("health reports the gate", _health.get("beta_required") is True, _health)


# ===========================================================================

app.stockfish_service.close()
db.drop_schema()
db.close_pool()

print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

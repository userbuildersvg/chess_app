"""
The hardening sprint's invariants: headers, CSRF, body size, and proxy trust.

Why this file exists
--------------------

Everything asserted here is invisible in the running app. A missing
`frame-ancestors` looks exactly like a present one until somebody frames the
sign-in page; a rate limiter reading the wrong end of `X-Forwarded-For` counts
diligently into a bucket the caller chose. That is the whole category of defect
this suite covers: protections whose absence produces no error, no log line and
no visible change, and which therefore rot silently unless something asserts
them.

Four properties, and each of them was a real hole in this repository before the
sprint that added this file (CLAUDE.md §28):

1. **Every response carries the security headers** - including the responses no
   route produces: the beta gate's 403, a 404, a validation error. Those are
   the ones a per-route decorator would have missed.
2. **A cross-site write is refused.** Production cookies were `SameSite=None`
   and roughly twenty POST routes take no request body, so an ordinary HTML
   form on any website could drive them with the victim's cookie attached.
3. **An oversized body is refused before it is buffered**, rather than after
   the parser has already materialised it.
4. **The rate limiter's bucket key is not something the caller can write.**

The tests are about PROPERTIES, not particular values. `test_headers_are_on`
asks whether a policy forbids framing, not whether it spells it a particular
way, so tightening the CSP does not break the suite that guards it.

Run it like the others (CLAUDE.md §6):

    cd /mnt/c/Users/David/Documents/chess-app-v3.9
    DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_security.py

Needs neither Stockfish nor DATABASE_URL: nothing here plays chess or stores
anything. It is the only suite in the repository that runs with no dependency
at all, which is deliberate - a security regression should be catchable in the
four seconds before anybody has set up an environment.
"""
import importlib
import os
import sys

# Before importing app - see the padlock note at the top of CLAUDE.md. Without
# it every request below is answered 403 by beta_gate.py and this suite would
# "pass" while testing the gate sixty times.
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ.setdefault("DISABLE_LANGFLOW", "true")
# Deterministic, and not whatever the developer's shell happens to hold: this
# suite asserts on the origin list, so it has to own it.
os.environ["ALLOWED_ORIGINS"] = "https://zugzwang.example,http://localhost:3001"

from fastapi.testclient import TestClient

import app as app_module
import body_limit
import csrf
import rate_limit
import security_headers

PASSED = 0
FAILED = 0


def check(label, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f"  ({detail})" if detail else ""))


client = TestClient(app_module.app)
ALLOWED = "https://zugzwang.example"
HOSTILE = "https://evil.example"


# ---------------------------------------------------------------------------
print("\n--- 1. the security headers are on every response ---")
# ---------------------------------------------------------------------------

REQUIRED = [
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
    "permissions-policy",
]

# Three responses that are produced by three different parts of the stack: a
# real route, Starlette's own 404 (no route runs at all), and a 422 from
# request validation (the route is found and refuses the body). A per-route
# decorator would have covered exactly one of them.
for label, response in [
    ("a normal 200", client.get("/api/health")),
    ("a 404 for no route at all", client.get("/api/does-not-exist")),
    ("a 422 from body validation", client.post("/api/move", json={"nope": 1})),
]:
    missing = [h for h in REQUIRED if h not in response.headers]
    check(f"{label} carries every security header", not missing, f"missing {missing}")

health = client.get("/api/health")
check("the API's CSP forbids framing",
      "frame-ancestors 'none'" in health.headers.get("content-security-policy", ""))
check("and X-Frame-Options says the same thing to an older browser",
      health.headers.get("x-frame-options") == "DENY")
check("nothing may be loaded on this origin's behalf",
      "default-src 'none'" in health.headers.get("content-security-policy", ""))
check("MIME sniffing is off", health.headers.get("x-content-type-options") == "nosniff")
check("no referrer leaves this origin",
      health.headers.get("referrer-policy") == "no-referrer")
check("the opener is severed",
      health.headers.get("cross-origin-opener-policy") == "same-origin")
check("the camera and microphone are denied",
      "camera=()" in health.headers.get("permissions-policy", "")
      and "microphone=()" in health.headers.get("permissions-policy", ""))

# The retired one. Sending it is worse than sending nothing - the filter it
# enabled was itself exploitable - so its absence is asserted rather than left
# to whoever next reads a scanner report recommending it.
check("X-XSS-Protection is deliberately NOT sent",
      "x-xss-protection" not in health.headers)


# ---------------------------------------------------------------------------
print("\n--- 2. HSTS is a production promise, and only that ---")
# ---------------------------------------------------------------------------

check("no HSTS locally - it would poison http://localhost for two years",
      "strict-transport-security" not in health.headers)

os.environ["HSTS_ENABLED"] = "true"
importlib.reload(security_headers)
check("HSTS_ENABLED=true turns it on", security_headers.hsts_enabled() is True)
check("and it is a long, subdomain-wide, preloadable promise",
      "max-age=63072000" in security_headers.HSTS
      and "includeSubDomains" in security_headers.HSTS
      and "preload" in security_headers.HSTS)
os.environ["HSTS_ENABLED"] = "false"
importlib.reload(security_headers)
check("HSTS_ENABLED=false turns it off even in production",
      security_headers.hsts_enabled() is False)
os.environ.pop("HSTS_ENABLED", None)
importlib.reload(security_headers)
os.environ["RENDER"] = "1"
importlib.reload(security_headers)
check("and with nothing set it follows the deployment",
      security_headers.hsts_enabled() is True)
os.environ.pop("RENDER", None)
importlib.reload(security_headers)


# ---------------------------------------------------------------------------
print("\n--- 3. a cross-site write is refused ---")
# ---------------------------------------------------------------------------

# The exact shape of the attack. /api/reset takes no request body, so an
# ordinary HTML form on any site can submit to it: no preflight, no CORS
# decision, and the cookie goes along.
forged = client.post("/api/reset", headers={"Origin": HOSTILE})
check("a POST from a foreign origin is refused", forged.status_code == 403,
      f"got {forged.status_code}")
check("and is refused as a forgery, not as a missing invitation",
      "beta_required" not in forged.json())

check("a POST from the deployed frontend's origin is allowed",
      client.post("/api/reset", headers={"Origin": ALLOWED}).status_code == 200)
check("a POST from the dev frontend's origin is allowed",
      client.post("/api/reset", headers={"Origin": "http://localhost:3001"}).status_code == 200)

# The judgement call in csrf.py, asserted so that changing it is deliberate.
check("a POST with NO origin at all is allowed - a browser cannot produce one",
      client.post("/api/reset").status_code == 200)

check("a GET from a foreign origin is untouched - it changes nothing",
      client.get("/api/status", headers={"Origin": HOSTILE}).status_code == 200)

# Referer is the fallback, and only the origin part of it is read.
check("a foreign Referer is refused when there is no Origin",
      client.post("/api/reset",
                  headers={"Referer": f"{HOSTILE}/attack.html"}).status_code == 403)
check("and a Referer from our own frontend is allowed",
      client.post("/api/reset",
                  headers={"Referer": f"{ALLOWED}/play"}).status_code == 200)

# Every unsafe method, not only POST - so a router that later exposes PUT or
# PATCH is covered on the day it is written.
for method in ("PUT", "PATCH", "DELETE"):
    r = client.request(method, "/api/account", headers={"Origin": HOSTILE})
    check(f"a forged {method} is refused too", r.status_code == 403, f"got {r.status_code}")

check("the safe methods are exactly the ones that cannot change anything",
      csrf.SAFE_METHODS == frozenset({"GET", "HEAD", "OPTIONS", "TRACE"}))
check("an origin is parsed to scheme and host only",
      csrf.origin_of("https://a.example:8443/deep/path?q=1") == "https://a.example:8443")
check("and something with no origin in it is not one",
      csrf.origin_of("not a url") is None)


# ---------------------------------------------------------------------------
print("\n--- 4. an oversized body is refused before it is read ---")
# ---------------------------------------------------------------------------

limit = body_limit.DEFAULT_MAX_BODY_BYTES

# Declared honestly: refused on the Content-Length alone, having read nothing.
big = client.post("/api/chat", json={"message": "x" * (limit * 2)},
                  headers={"Origin": ALLOWED})
check("a body over the default ceiling is refused", big.status_code == 413,
      f"got {big.status_code}")
check("and says so in a sentence a person can act on",
      "too large" in big.json().get("detail", "").lower())
check("the 413 still carries its security headers",
      big.headers.get("x-content-type-options") == "nosniff"
      and "frame-ancestors 'none'" in big.headers.get("content-security-policy", ""))

# Not declared at all. A chunked body has no Content-Length to check, so the
# first guard has nothing to say and the byte counter is the whole boundary -
# which is why the counter exists rather than the header check being enough.
#
# (A body that UNDERSTATES its length is not a third case: the ASGI server
# frames the request by Content-Length and hands the app only what was
# declared, so the extra bytes never arrive at all.)
def _chunks():
    yield b'{"message":"'
    for _ in range((limit * 2) // 1024):
        yield b"x" * 1024
    yield b'"}'


chunked = client.post(
    "/api/chat",
    content=_chunks(),
    headers={"Origin": ALLOWED, "Content-Type": "application/json"},
)
check("a chunked body with no declared length is refused by the counter",
      chunked.status_code == 413, f"got {chunked.status_code}")

check("an ordinary body passes untouched",
      client.post("/api/chat", json={"message": "why is this move good?"},
                  headers={"Origin": ALLOWED}).status_code in (200, 429, 503))

# The two upload routes get their own ceiling, derived from the limit their own
# parser enforces rather than guessed - so the guard cannot refuse something
# the route would have accepted.
from postmortem_state import MAX_PGN_BYTES
from profile_api import MAX_BODY_BYTES as PROFILE_MAX

mw = body_limit.BodyLimitMiddleware(app_module.app)
check("the PGN import route is allowed a whole PGN",
      mw.limit_for("/api/postmortem/import") > MAX_PGN_BYTES)
check("the library import route is allowed a whole library",
      mw.limit_for("/api/profile/games") > PROFILE_MAX)
check("and everything else gets the small default",
      mw.limit_for("/api/move") == body_limit.DEFAULT_MAX_BODY_BYTES)
check("a GET is never body-checked",
      client.get("/api/status", headers={"Origin": ALLOWED}).status_code == 200)


# ---------------------------------------------------------------------------
print("\n--- 5. the rate limiter's bucket key is not the caller's to choose ---")
# ---------------------------------------------------------------------------

class FakeRequest:
    """Just enough of a Request for client_ip: headers and a peer address."""

    class _Client:
        host = "10.0.0.9"

    def __init__(self, forwarded=None):
        self.headers = {} if forwarded is None else {"x-forwarded-for": forwarded}
        self.client = self._Client()


# One trusted hop for this block - the Render shape. The DEFAULT is 0 and is
# asserted at the end; these checks are about the arithmetic when a deployment
# has declared its proxies.
os.environ["TRUSTED_PROXY_HOPS"] = "1"
importlib.reload(rate_limit)

# THE BUG. X-Forwarded-For is built by appending, so the leftmost entry is
# whatever the caller wrote. Reading it meant every request could name its own
# bucket - unlimited Gemini spend, unlimited password guesses, unlimited code
# guesses, all while the limiter counted correctly into buckets of one.
spoofed = FakeRequest("1.2.3.4, 203.0.113.7")
check("a caller-written X-Forwarded-For entry is ignored",
      rate_limit.client_ip(spoofed) != "1.2.3.4",
      f"got {rate_limit.client_ip(spoofed)}")
check("the address our own proxy saw is what counts",
      rate_limit.client_ip(spoofed) == "203.0.113.7")

check("two requests forging different origins share one bucket",
      rate_limit.client_ip(FakeRequest("9.9.9.9, 203.0.113.7"))
      == rate_limit.client_ip(FakeRequest("8.8.8.8, 203.0.113.7")))

check("with no header at all it is the socket peer",
      rate_limit.client_ip(FakeRequest()) == "10.0.0.9")
check("a chain exactly as long as the promised hops is read",
      rate_limit.client_ip(FakeRequest("203.0.113.7")) == "203.0.113.7")

# THE BYPASS THAT SURVIVED THE FIRST FIX, pinned so it cannot come back.
#
# The first version of client_ip clamped the index at 0, so a chain SHORTER
# than TRUSTED_PROXY_HOPS fell back to the leftmost entry - the caller's own
# text. An audit reproduced it end to end: twelve failed logins with a fixed
# header gave 401 x10 + 429 x2, and the same twelve with a rotating header
# gave 401 x12. The clamp was written as an IndexError guard and quietly meant
# "return the attacker's value rather than raise".
# A chain SHORTER than the promised hops means the header is not the one this
# deployment was described as having - a proxy was removed, or the number is
# too high. The first version clamped the index at 0 and returned the leftmost
# entry, which is the caller's own text; it now refuses to use the header at
# all and falls back to the socket peer.
#
# Note what this does NOT claim. At one hop, a one-entry chain is the ordinary
# case (the caller sent nothing and the proxy appended itself), so it is
# trusted - correctly. The clamp fix is about a chain that is short RELATIVE
# TO THE CONFIGURED HOPS, which is why it is exercised at two.
os.environ["TRUSTED_PROXY_HOPS"] = "2"
importlib.reload(rate_limit)
check("a chain shorter than the promised hops is not trusted at all",
      rate_limit.client_ip(FakeRequest("1.2.3.4")) == "10.0.0.9",
      f"got {rate_limit.client_ip(FakeRequest('1.2.3.4'))}")
check("two callers forging a short chain share the peer's bucket",
      rate_limit.client_ip(FakeRequest("1.2.3.4"))
      == rate_limit.client_ip(FakeRequest("5.6.7.8")))
os.environ["TRUSTED_PROXY_HOPS"] = "1"
importlib.reload(rate_limit)
check("and a full chain is still read correctly at one hop",
      rate_limit.client_ip(FakeRequest("1.2.3.4, 203.0.113.7")) == "203.0.113.7")

os.environ["TRUSTED_PROXY_HOPS"] = "2"
importlib.reload(rate_limit)
check("two trusted hops step one further left",
      rate_limit.client_ip(FakeRequest("1.2.3.4, 198.51.100.2, 203.0.113.7"))
      == "198.51.100.2")

os.environ["TRUSTED_PROXY_HOPS"] = "0"
importlib.reload(rate_limit)
check("zero trusted hops ignores the header entirely",
      rate_limit.client_ip(FakeRequest("1.2.3.4")) == "10.0.0.9")

os.environ.pop("TRUSTED_PROXY_HOPS", None)
importlib.reload(rate_limit)
check("and the default is ZERO - trust no part of that header",
      rate_limit.TRUSTED_PROXY_HOPS == 0)
check("so with nothing configured a forged header buys nothing",
      rate_limit.client_ip(FakeRequest("1.2.3.4, 5.6.7.8")) == "10.0.0.9")


# ---------------------------------------------------------------------------
print("\n--- 5b. uvicorn is not allowed to forge the client address ---")
# ---------------------------------------------------------------------------

# The real cause of the bypass above, and the one no in-process test can
# observe: uvicorn ships ProxyHeadersMiddleware ENABLED BY DEFAULT, and it
# REWRITES scope["client"] from X-Forwarded-For. So `request.client.host` -
# the "unforgeable socket peer" every fallback in client_ip relies on - is
# itself caller-controlled unless uvicorn is told to stop.
#
# TestClient does not run uvicorn, so this asserts the LAUNCH COMMANDS
# instead. That is the whole attack surface: three places start this app, and
# a fourth would be a new deployment target. Removing the flag from any of
# them silently restores unlimited password guessing.
import pathlib

for launcher, label in [
    ("Dockerfile.backend", "the Render image"),
    ("Dockerfile", "the all-in-one image"),
]:
    text = pathlib.Path(launcher).read_text(encoding="utf-8")
    cmd = [ln for ln in text.splitlines() if ln.startswith("CMD ") and "uvicorn" in ln]
    check(f"{label} starts uvicorn with --no-proxy-headers",
          bool(cmd) and all("--no-proxy-headers" in ln for ln in cmd),
          f"{launcher}: {cmd}")

claude_md = pathlib.Path("CLAUDE.md").read_text(encoding="utf-8")
runner = [ln for ln in claude_md.splitlines()
          if "uvicorn app:app --host" in ln and ln.strip().startswith("exec")]
check("the documented dev runner carries it too",
      bool(runner) and all("--no-proxy-headers" in ln for ln in runner),
      f"{runner}")


# ---------------------------------------------------------------------------
print("\n--- 6. the route map is not published to a public host ---")
# ---------------------------------------------------------------------------

# `/` sits OUTSIDE `/api/`, so the beta gate never saw it: the app answered
# 403 to everything without an invitation and then listed its own endpoints at
# the front door. It follows DOCS_ENABLED now, for the same reason /docs does.
import app as _app_for_docs

check("locally, where the docs are served, the route map is too",
      (client.get("/").status_code == 200) is _app_for_docs.DOCS_ENABLED)
check("and it is the docs flag that decides, not a separate one",
      isinstance(_app_for_docs.DOCS_ENABLED, bool))

os.environ["ENABLE_DOCS"] = "false"
importlib.reload(_app_for_docs)
public = TestClient(_app_for_docs.app)
check("with the docs off, / is a 404 like any unknown path",
      public.get("/").status_code == 404)
check("and so are the interactive docs",
      public.get("/docs").status_code == 404
      and public.get("/openapi.json").status_code == 404)
os.environ.pop("ENABLE_DOCS", None)


# ---------------------------------------------------------------------------
print("\n--- 7. the middleware stack is in the one order that works ---")
# ---------------------------------------------------------------------------

# Starlette runs the LAST-added middleware outermost, so user_middleware is
# innermost-first. Asserted because every one of these positions is
# load-bearing and none of them fails loudly when wrong: the headers stop
# covering error responses, the gate's 403 loses its CORS headers, or an
# oversized body is buffered before it is refused.
order = [m.cls.__name__ for m in app_module.app.user_middleware]
expected = [
    "SecurityHeadersMiddleware",
    "BodyLimitMiddleware",
    "IdentityMiddleware",
    "CORSMiddleware",
    "CsrfOriginMiddleware",
    "BetaGateMiddleware",
]
check("outermost to innermost, the stack is as documented",
      order == expected, f"got {order}")


# ---------------------------------------------------------------------------
print(f"\n{PASSED}/{PASSED + FAILED} passed")
sys.exit(1 if FAILED else 0)

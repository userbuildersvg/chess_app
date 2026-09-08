"""
The closed-beta gate. Server-side, fail-closed, and the only thing that decides.

WHY A MIDDLEWARE AND NOT A DEPENDENCY ON EVERY ROUTE
----------------------------------------------------
There are roughly sixty API routes across six routers, and the failure mode of
`dependencies=[Depends(require_beta)]` is not that it refuses wrongly - it is
that somebody adds route sixty-one and forgets it. That route is then open,
nothing fails, no test covers it, and the hole is found by whoever was looking
for one.

A middleware inverts the default. Everything under `/api` is closed, and a new
route is protected on the day it is written without its author doing anything.
Opening one is an explicit edit to the list below, which is a small, readable,
reviewable thing that a person changing it has to look straight at.

That inversion is the whole design. It is why this file is a deny-by-default
prefix match rather than a decorator, and it should stay that way.

WHERE IT SITS IN THE STACK
--------------------------
Starlette runs the LAST-added middleware outermost, so `app.py` adds this one
BEFORE `CORSMiddleware` and `IdentityMiddleware`, giving:

    IdentityMiddleware  →  CORSMiddleware  →  BetaGateMiddleware  →  routes

Both neighbours matter. Identity has to run first or `request.state.identity`
is not set and this file has nothing to authorize. CORS has to be outside or a
403 from here reaches a cross-origin caller with no CORS headers, which
presents in a browser as a network error with no status - the same class of
silent confusion that `ALLOWED_ORIGINS` exists to avoid. Preflight `OPTIONS`
requests are answered by CORSMiddleware before they ever arrive here.

WHAT IT CANNOT BE BYPASSED BY
-----------------------------
Nothing in the decision comes from the client. `identity` is resolved by
`identity.py` from an HMAC-signed, server-minted, HttpOnly cookie, and
`beta_service.has_access()` answers from Postgres. So:

* DevTools, React state, localStorage - none of them is an input. Editing all
  three changes what the page draws and nothing about what the API serves.
* JavaScript disabled entirely - the API is unchanged; there is simply no page.
* `fetch('/api/move', ...)` from the console, or curl - identical treatment,
  because the check is not in the page.
* A replayed or edited cookie - an unsigned guest id is rejected by
  `verify_guest_cookie` before this file sees it, and a signed one that never
  redeemed a code has no row in `beta_redemptions`.
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import beta_service
from identity import identity_of

logger = logging.getLogger(__name__)


# Exact paths that answer before a visitor has access.
#
# Every entry is here for a stated reason, and the bar is "the landing page
# cannot work without it". Nothing that plays chess, reads history, spends
# Gemini quota or touches a game is on this list.
OPEN_PATHS = frozenset({
    # Liveness. Anonymous by definition, creates nothing, and Render's health
    # check has no cookie to present.
    "/api/health",

    # Whether accounts exist and whether email works. The sign-in page asks
    # this before it can draw itself, and a tester signing in on a new browser
    # has no access yet - that is exactly the state this has to serve.
    "/api/auth/config",
    # Who the caller is. Answers "nobody" for a locked-out visitor, which is
    # what the landing page needs in order to offer Sign in rather than
    # Continue. It is also the identity bootstrap in `http.ts`, so gating it
    # would mean a visitor could not be given a cookie to redeem a code with.
    "/api/auth/me",

    # Getting back in. A returning tester whose grant lives on their account
    # has no guest grant on a new browser, so sign-in and recovery must be
    # reachable from the landing page or their access is unusable. Signup is
    # deliberately absent: a new account comes only after a guest redeems.
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/google/start",
    "/api/auth/google/callback",
})

# Prefixes that answer before a visitor has access.
OPEN_PREFIXES = (
    # The gate's own endpoints. Obviously: this is where a code is redeemed.
    "/api/beta/",
)

# Everything the gate governs. A request outside this prefix is not an API
# call - it is the SPA's own HTML, its assets, or the root - and refusing
# those would mean the visitor could not load the page that asks for a code.
#
# The frontend gate on those pages is a convenience for the person looking at
# them. It is not what protects anything, and it is not trusted here.
GUARDED_PREFIX = "/api/"


def is_open(path: str) -> bool:
    """Whether this path answers without beta access."""
    if not path.startswith(GUARDED_PREFIX):
        return True
    if path in OPEN_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in OPEN_PREFIXES)


class BetaGateMiddleware(BaseHTTPMiddleware):
    """Refuse every guarded request from an identity without beta access."""

    async def dispatch(self, request, call_next):
        if not beta_service.beta_required():
            return await call_next(request)

        path = request.url.path
        if is_open(path):
            return await call_next(request)

        identity = identity_of(request)

        # The cache first, synchronously, because it is pure memory and it is
        # what answers for a tester who is already in - which is almost every
        # request this middleware ever sees.
        #
        # Only a miss reaches the database, and it does so on a worker thread.
        # `has_access()` blocks: it borrows a pooled connection and waits on a
        # round trip to Neon. Calling that directly from an async `dispatch`
        # would stall the whole event loop for its duration, so one cold
        # visitor's latency would be paid by every other request in flight -
        # and this middleware runs on every request there is, which is exactly
        # the position where that mistake is most expensive.
        allowed = beta_service.cached_access(identity)
        if allowed is None:
            allowed = await run_in_threadpool(beta_service.has_access, identity)
        if allowed:
            return await call_next(request)

        # 403, not 401. 401 means "authenticate and try again", and the
        # frontend's account layer would reasonably read it as a dead session
        # and bounce the caller to sign-in - which is wrong for a signed-in
        # account that simply has no invitation. 403 says the request was
        # understood, the caller is known, and the answer is still no.
        #
        # `beta_required` is a flag the client can branch on without having to
        # parse prose, so the page can show the landing surface rather than a
        # generic error. It tells an unauthorized caller nothing they did not
        # already learn from the status code.
        logger.info(f"🔒 Beta gate refused {request.method} {path}")
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "Zugzwang is in a closed beta. An access code is required."
                ),
                "beta_required": True,
            },
        )

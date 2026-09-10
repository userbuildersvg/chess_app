"""
Cross-site request forgery: the second lock, behind `SameSite`.

THE HOLE THIS CLOSES
--------------------
Until now the only thing standing between an attacker's page and a state
change on this API was the cookie's `SameSite` attribute, and in production
that attribute was `None` - which is to say, nothing. `SameSite=None` exists
precisely to tell the browser "send this cookie on cross-site requests", and
the browser obliges.

That mattered because of a shape this API uses a lot: a POST route with **no
request body**. `/api/reset`, `/api/ai-move`, `/api/auth/logout`, every
`/api/ai-vs-ai/*` control, and each of the post-mortem navigation routes take
no Pydantic model, so FastAPI never inspects the Content-Type. A plain HTML
form on any website can therefore submit to them:

    <form action="https://…/api/ai-move" method="POST"><input type="submit">

A form POST is a "simple request" - no preflight, no CORS decision, no
JavaScript needed - so `CORSMiddleware` never sees it and the response being
unreadable to the attacker is irrelevant. The *effect* has already happened:
somebody's game reset, or a Gemini call billed to our key.

`identity.py` now defaults production to `SameSite=Lax`, which stops this at
the browser. This module is the second lock, because the first one is a single
environment variable away from being undone (`COOKIE_SAMESITE=none`, set by
somebody debugging a cookie that is not arriving) and because a `SameSite`
regression is completely silent.

WHY AN ORIGIN CHECK AND NOT A CSRF TOKEN
----------------------------------------
A synchroniser token is the textbook answer and it is the wrong one here. It
needs somewhere to keep the token per session, an endpoint to hand it out, a
change to every write in `chessService.ts`, and a rotation story - all to
establish a fact the browser is already telling us for free. Every browser
sends `Origin` on a cross-origin POST, and has done for years; on a form POST
it is sent precisely because the request is unsafe. OWASP lists verifying the
origin as a legitimate primary defence, not merely a supplement.

The rule is therefore: **an unsafe method whose `Origin` we do not recognise
is refused.**

WHY A MISSING `Origin` IS ALLOWED
---------------------------------
This is the one judgement call in the file, so it is worth stating rather than
discovering. A request with no `Origin` at all is permitted.

That is not a bypass, because *a browser cannot produce one*. A page cannot
suppress the header; `fetch` cannot override it; a form cannot omit it. The
callers that legitimately send no `Origin` are the ones that are not a browser
and therefore cannot be CSRF'd in the first place: `curl`, the seventeen
TestClient suites, Render's health check, an uptime monitor.

Refusing them would break every test suite in the repository to defend against
an attacker who does not exist, since an attacker who can set arbitrary
headers from outside a browser does not need the victim's cookie - they can
simply not have one, and get an anonymous session.

`Referer` is consulted only when `Origin` is absent, for the same reason
Django does it: it is the older, lossier signal, and it is checked for the
origin part alone.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# The methods that cannot change anything, per RFC 9110. GET and HEAD are the
# ones that matter; OPTIONS is here because CORS preflight must pass through
# untouched, and TRACE because refusing it would be theatre - it is not
# enabled.
#
# This list is only correct while those methods are genuinely read-only. The
# audit that produced this file checked: `GET /api/reset` was the one route
# that violated it and was made a POST before this existed (CLAUDE.md §22,
# blocker 3). A future GET that mutates re-opens the hole silently, which is
# why it is written down here rather than assumed.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

REFUSAL = (
    "That request did not come from Zugzwang, so it was refused."
)


def origin_of(url: str):
    """`scheme://host[:port]` for a URL, or None if it has no origin."""
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    """Refuse an unsafe request that names an origin we do not serve.

    `allowed_origins` is the same list `CORSMiddleware` is given, passed in
    rather than re-read from the environment so the two can never disagree
    about what this deployment's frontend is. A drift between them would mean
    an origin CORS accepts and this refuses, which presents as writes failing
    for one specific deployment and nowhere else.
    """

    def __init__(self, app, allowed_origins):
        super().__init__(app)
        self._allowed = frozenset(allowed_origins)

    def _permitted(self, request, origin: str) -> bool:
        if origin in self._allowed:
            return True

        # The service's own origin. A browser pointed straight at this API -
        # the interactive docs locally, a hand-driven request against Render -
        # sends its own host as the Origin, which is same-origin by definition
        # and never a forgery.
        #
        # BOTH SCHEMES, and that is not laziness. uvicorn runs with
        # `--no-proxy-headers` (see Dockerfile.backend for why: with it on,
        # `request.client.host` becomes attacker-controlled and every rate
        # limit dies). A consequence is that uvicorn no longer rewrites the
        # scheme from X-Forwarded-Proto either, so behind Render's TLS
        # terminator `request.url` says `http` while the browser correctly
        # says `https`. Comparing the full origin would then reject a genuine
        # same-origin request, and it would do it only in production.
        #
        # Accepting either scheme for OUR OWN HOST costs nothing: an attacker
        # cannot make their page's origin have our host, which is the whole
        # thing being checked. The host is the security-relevant half; the
        # scheme here is an artifact of where TLS is terminated.
        own = urlsplit(str(request.url))
        if not own.netloc:
            return False
        return origin in (f"http://{own.netloc}", f"https://{own.netloc}")

    async def dispatch(self, request, call_next):
        if request.method in SAFE_METHODS:
            return await call_next(request)

        origin = request.headers.get("origin")
        if origin is None:
            referer = request.headers.get("referer")
            origin = origin_of(referer) if referer else None
        if origin is None:
            # Not a browser. See the module docstring - this is a decision,
            # not an oversight.
            return await call_next(request)

        if self._permitted(request, origin):
            return await call_next(request)

        # The origin IS logged, unlike the beta gate's refusals: this one is
        # never a legitimate user making a mistake, so there is no privacy
        # cost, and knowing which site is posting at us is the entire value of
        # noticing at all.
        logger.warning(
            f"🛡️ CSRF: refused {request.method} {request.url.path} from origin {origin}"
        )
        return JSONResponse(status_code=403, content={"detail": REFUSAL})

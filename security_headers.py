"""
The response headers that make a browser defend this application for us.

WHY THIS IS A MIDDLEWARE AND NOT A DECORATOR
--------------------------------------------
Same argument `beta_gate.py` makes, for the same reason. There are ~60 routes;
a header applied per route is a header somebody forgets on route sixty-one,
and the omission is invisible - nothing fails, no test notices, and the hole
is found by whoever was looking for one. Applied here, every response carries
them: routes, validation errors, the gate's own 403, and the responses
Starlette generates before any of our code runs.

It is added LAST in `app.py`, which makes it OUTERMOST. That is deliberate: a
CORS rejection, a 403 from the beta gate and a 500 from an unhandled exception
are exactly the responses most likely to be missing headers, and they are the
ones that never reach an inner middleware's `call_next`.

WHAT EACH HEADER IS DOING, AND WHY IT IS SAFE HERE
--------------------------------------------------
This service answers JSON to a single-page app on another host. It renders no
HTML, embeds nothing, and is never framed - which is what makes a policy this
severe possible without breaking anything.

* `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`
  An API's own CSP governs what a browser will do with an API *response* if it
  is ever navigated to directly rather than fetched. There is no legitimate
  case where a browser should load a script, a style or an image on behalf of
  this origin, so everything is denied. This is NOT the application's CSP -
  that one belongs on the HTML document and lives in `chess-frontend/
  vercel.json`, because the document is served by Vercel and never passes
  through this process.

* `frame-ancestors 'none'` plus `X-Frame-Options: DENY`
  Both, not one. `frame-ancestors` is the modern control and supersedes the
  header; `X-Frame-Options` is what an older browser that ignores CSP still
  obeys. They are consistent, so there is no ambiguity to resolve.

* `X-Content-Type-Options: nosniff`
  Stops a browser second-guessing our `Content-Type`. The concrete attack it
  closes: an endpoint that echoes attacker-influenced text inside a JSON body
  can be navigated to directly, and a sniffing browser that decides the body
  looks like HTML will execute it on this origin.

* `Referrer-Policy: no-referrer`
  This service issues one cross-origin redirect (Google sign-in). Nothing
  downstream needs to know which of our URLs a request came from, and several
  of our paths carry ids that are credentials - a post-mortem game id, a
  password reset token in a link somebody clicked. `no-referrer` rather than
  `strict-origin-when-cross-origin` because the API has no analytics to feed
  and no reason to leak the path.

* `Cross-Origin-Opener-Policy: same-origin`
  Severs `window.opener` between this origin and anything that opened it, so a
  page that popped an API URL cannot reach into it.

* `Cross-Origin-Resource-Policy: same-site`
  NOT `same-origin`. The frontend on Vercel reaches this service through a
  server-side rewrite, so in normal use these responses are same-origin - but
  a direct browser call from the deployed frontend to Render is cross-origin
  and legitimate, and `same-origin` would break it silently. `same-site` also
  does not apply to the CORS-approved fetches the app actually makes; it is
  here for the no-CORS embeddings (an <img>, a <script> tag) that nothing
  legitimate ever does to a JSON API.

* `Permissions-Policy`
  Every powerful feature switched off. An API has no camera, and the value of
  saying so is that it also applies to anything ever framed by an API
  response, which is nothing - so this is cheap and cannot regress.

* `Strict-Transport-Security` - PRODUCTION ONLY, and see below.

DELIBERATELY OMITTED
--------------------
* `X-XSS-Protection`. Retired. The filter it enabled was itself exploitable
  and every current browser ignores the header; Chrome removed the auditor
  outright. Sending `0` would be the only defensible value and it changes
  nothing, so this sends nothing.
* `Content-Security-Policy-Report-Only` / `report-uri`. There is no endpoint
  to receive reports and no one watching for them. A report destination that
  nobody reads is a header that looks like monitoring and is not.
* `Clear-Site-Data` on logout. It would drop the frontend's caches and
  service-worker state as well as the cookie, and logout here is meant to end
  a session, not evict the app. Revisit if a real logout-on-shared-device
  requirement appears.
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware

from identity import is_production

# The API's own policy. Severe because this origin serves JSON and nothing
# else - see the module docstring.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

# Two years, subdomains included, and preload-eligible. Matches what Vercel
# already sends for the frontend origin, so the two halves of the deployment
# state the same thing rather than one of them being the weak link.
HSTS = "max-age=63072000; includeSubDomains; preload"

PERMISSIONS_POLICY = (
    "accelerometer=(), autoplay=(), camera=(), display-capture=(), "
    "encrypted-media=(), fullscreen=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), midi=(), payment=(), "
    "picture-in-picture=(), publickey-credentials-get=(), screen-wake-lock=(), "
    "usb=(), xr-spatial-tracking=()"
)

BASE_HEADERS = {
    "Content-Security-Policy": API_CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": PERMISSIONS_POLICY,
}


def hsts_enabled() -> bool:
    """
    Whether to claim HTTPS-only for this origin.

    Production only, and that is not caution for its own sake: HSTS is a
    PROMISE with a two-year memory. A browser that receives it on
    `http://localhost` will refuse to load localhost over HTTP afterwards -
    for every project on that machine, not just this one - and there is no way
    to withdraw it except `chrome://net-internals`. So it follows
    `is_production()`, with `HSTS_ENABLED` as the override for a deployment
    that is HTTPS but is not Render.
    """
    explicit = os.environ.get("HSTS_ENABLED")
    if explicit is not None:
        return explicit.strip().lower() == "true"
    return is_production()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the browser-facing security headers to every response."""

    def __init__(self, app):
        super().__init__(app)
        self._headers = dict(BASE_HEADERS)
        if hsts_enabled():
            self._headers["Strict-Transport-Security"] = HSTS

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for name, value in self._headers.items():
            # setdefault, not assignment: a route that has deliberately set a
            # different value for itself (there are none today) should win
            # over a blanket default rather than be silently overwritten.
            if name not in response.headers:
                response.headers[name] = value
        return response

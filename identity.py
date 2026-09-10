"""
Who is asking, as one opaque string.

WHY THIS EXISTS
---------------
`player_state.py` keys a player's game on an "identity" it deliberately never
parses, so that signing in changes *which string is passed in* and nothing
else. This file is the other half of that seam: it decides what that string is
for a given HTTP request, and it is the only place in the codebase that knows
how identity is carried.

Everything downstream - `app.py`, `sandbox_api.py`, `player_state.py` - takes
the string and asks no questions. That is what makes accounts a switch rather
than a rewrite.

TWO COOKIES, NOT ONE
--------------------
`zw_guest` is an anonymous, opaque, server-minted id. Every visitor gets one on
their first request and keeps it; it is what makes an anonymous player's board
survive a refresh without them ever seeing a sign-in prompt.

`zw_session` is an account session token, and only exists once someone has
signed in. When it is present AND valid AND accounts are switched on, identity
resolves to that account. Otherwise we fall back to the guest cookie.

Both are HttpOnly. The identity string is the key to a player's game, so a
value JavaScript can read is a value an XSS can steal. The server mints it;
the browser only carries it.

    guest:8f2a1c...    an anonymous visitor.
    user:42            a signed-in account.

HttpOnly IS NOT AUTHENTICITY
----------------------------
HttpOnly stops page scripts reading the cookie. It says nothing about what the
server will accept, and the two get conflated constantly. This server used to
accept any `zw_guest` value that merely began with `guest:` - so while nobody
could *guess* another visitor's 128-bit id, anyone could invent unlimited
identities of their own by editing one cookie. That was harmless while a
guest's data lived in a per-session in-memory database. It stopped being
harmless when guest history began persisting to Postgres so that it could be
claimed at signup: forged identities become rows.

So the guest cookie is now carried as `<id>.<mac>`, where the MAC is
HMAC-SHA256 over the id under `SESSION_COOKIE_SECRET`, compared with
`compare_digest`. An id the server did not mint does not verify, and is
replaced by a fresh one rather than trusted. This authenticates *the
mechanism*; the id itself is still the credential, exactly as a session token
is, and stealing the cookie still means assuming that guest.

`SESSION_COOKIE_SECRET` must be set and stable in a deployment. Without it a
random per-process key is used, which is safe but means every restart
invalidates every guest cookie and every visitor becomes a new guest.

The prefixes are for humans reading logs. Nothing downstream branches on them
except the one place that must: whether this player's chess history is allowed
to touch the learning database (see `is_guest`).

WHY A MIDDLEWARE AND NOT A DEPENDENCY
-------------------------------------
A dependency can read a cookie but cannot set one - only a response can, and
dependencies run before the response exists. Resolving in middleware means the
identity is on `request.state` for every route (including the sandbox router,
which is mounted separately) and the Set-Cookie is attached on the way out,
once, in one place.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading

from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

GUEST_COOKIE = "zw_guest"
SESSION_COOKIE = "zw_session"

# A year. The guest cookie is the only thing standing between a visitor and
# losing the game they are in the middle of, and it identifies nobody - there
# is no reason to expire it aggressively.
GUEST_COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def _cookie_security() -> tuple[bool, str]:
    """
    (secure, samesite) for the cookies this module sets.

    `Lax` everywhere, and that is a CHANGE - production used to send
    `SameSite=None`.

    The old value rested on a belief this file states elsewhere and CLAUDE.md
    had already corrected: that the frontend on Vercel and the API on Render
    are "genuinely cross-site". They are not. `chess-frontend/vercel.json`
    rewrites `/api/:path*` to the Render URL SERVER-SIDE, so the browser only
    ever sees the Vercel origin and these cookies are first-party. Verified by
    request, not read from config: `https://chess-app-rho-swart.vercel.app/api/health`
    answers 200 from the Vercel origin with `x-render-origin-server: uvicorn`.

    `None` was therefore not buying anything, and it was costing something
    real. `SameSite=None` means "send this cookie on cross-site requests",
    which is precisely what a CSRF needs - and roughly twenty POST routes in
    this app take no request body, so a plain HTML form on any website could
    submit to them with the victim's cookie attached and no preflight to stop
    it. `csrf.py` has the whole account. `Lax` closes it at the browser, which
    is the only place it can be closed for free.

    What `Lax` still permits, and why nothing breaks: the cookie IS sent on
    top-level GET navigations, so the Google sign-in round trip
    (`/api/auth/google/callback`, a redirect arriving from google.com) still
    carries `zw_oauth_state`. What it stops is exactly the cross-site POST.

    `COOKIE_SAMESITE` remains the override, and remains the thing to reach for
    if a browser is genuinely reaching Render directly rather than through the
    rewrite - a preview build with `VITE_API_BASE` pointed at the API, say.
    Setting it back to `none` re-opens the hole above, which is why `csrf.py`
    exists as a second lock and does not depend on this value.
    """
    explicit = os.environ.get("COOKIE_SAMESITE")
    if explicit:
        samesite = explicit.lower()
    else:
        samesite = "lax"
    secure_env = os.environ.get("COOKIE_SECURE")
    if secure_env is not None:
        secure = secure_env.lower() == "true"
    else:
        # Secure whenever this is a deployment, and additionally whenever
        # SameSite=None - which every current browser rejects outright without
        # it, so the two travel together whether or not anyone said so.
        #
        # `is_production()` is in this condition because the SameSite default
        # is now `lax` in both environments: deriving Secure from SameSite
        # alone, as this used to, would have quietly dropped the flag from
        # every production cookie the moment the default changed. Render sets
        # COOKIE_SECURE=true explicitly as well, so this is the belt behind
        # that brace.
        secure = samesite == "none" or is_production()
    return secure, samesite


def is_production() -> bool:
    """
    Whether this process is serving a deployment rather than a laptop.

    Render sets RENDER itself; PRODUCTION is the manual escape hatch for
    anywhere else. Public because it decides more than cookie flags now - see
    app.py, which uses it to keep the interactive API docs off a public host.
    """
    return bool(os.environ.get("RENDER") or os.environ.get("PRODUCTION"))


_secret_env = os.environ.get("SESSION_COOKIE_SECRET")
if _secret_env:
    _SIGNING_KEY = _secret_env.encode("utf-8")
else:
    # Safe, but not stable: every restart invalidates every guest cookie, so
    # each visitor silently becomes a new guest and loses the thread between
    # their games. Fine on a laptop, wrong in a deployment - hence the warning
    # rather than a silent default.
    _SIGNING_KEY = secrets.token_bytes(32)
    logger.warning(
        "⚠️ SESSION_COOKIE_SECRET is unset - guest cookies are signed with a "
        "random per-process key and will not survive a restart. Set it in any "
        "deployment."
    )


def _sign(identity: str) -> str:
    """The cookie value for an identity: the id, then its MAC."""
    mac = hmac.new(_SIGNING_KEY, identity.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{identity}.{mac}"


def verify_guest_cookie(raw):
    """
    The guest identity inside a cookie value, or None if it is not ours.

    None covers every rejection there is - absent, malformed, wrong shape,
    wrong prefix, bad MAC - because the caller does exactly the same thing in
    all of them (mint a fresh guest), and enumerating the reasons back to a
    client only helps someone probing.

    `compare_digest`, not `==`, so the comparison cannot be timed to recover a
    valid MAC byte by byte.
    """
    if not raw or "." not in raw:
        return None
    identity, _, mac = raw.rpartition(".")
    if not identity.startswith("guest:") or not mac:
        return None
    expected = hmac.new(_SIGNING_KEY, identity.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, expected):
        return None
    return identity


def new_guest_id() -> str:
    """An unguessable anonymous id. `secrets`, not `uuid4`, because this is
    a credential: holding it is what grants access to that player's game."""
    return "guest:" + secrets.token_hex(16)


def is_guest(identity: str) -> bool:
    """
    Whether this identity is anonymous.

    The single behavioural fork in the whole seam, and it exists for one
    reason: a guest's play must not be written to `data/learning.db`. See
    `guest_learning.py`.
    """
    return not identity.startswith("user:")


def account_id_of(identity: str):
    """The account id inside a `user:<id>` identity, or None for a guest."""
    if identity.startswith("user:"):
        return identity[len("user:"):]
    return None


# ---------------------------------------------------------------------------
# Retiring a guest identity
#
# A guest identity has to be able to STOP being usable, and until now none
# could. The sequence that made this a security problem: a guest plays, signs
# up, their games are claimed (`games.owner` rewritten to `user:<id>`), then
# they log out. Logout deleted the session cookie and nothing else, so the
# request fell back to the *same* `zw_guest` cookie the browser still held.
# That identity still keyed a live `PlayerSession` in `player_state`, holding
# the board AND `current_game_id` - a row that now belongs to the account. The
# signed-out guest was handed the claimed position back and could keep writing
# moves into account-owned history.
#
# So a claimed identity is revoked here, and the browser is issued a brand new
# one in the same response.
#
# WHY A PROCESS-LOCAL SET AND NOT A DATABASE LOOKUP
# ------------------------------------------------
# Checking `claimed_guests` would mean a query on every single guest request,
# on the hot path, to answer "no" almost every time. It is also not what makes
# this safe. Two things do, and they are durable:
#
#   * ownership. Claiming rewrote `games.owner`, and every read filters on it,
#     so a replayed cookie reaches no claimed row however it arrives.
#   * the live session. `PlayerStore` is memory, so the board and the
#     `current_game_id` that were the actual leak do not outlive the process.
#
# This set closes the window those two leave open - the life of one process,
# where the claimed guest's session is still resident - and it is populated in
# that same process at claim time. It is deliberately bounded: an unbounded
# set keyed on anything a caller influences is a slow memory leak.
# ---------------------------------------------------------------------------

MAX_REVOKED_GUESTS = 20000

_revoked_guests: set = set()
_revoked_lock = threading.Lock()


def revoke_guest(identity: str) -> None:
    """Refuse this guest identity from here on. One-way, like claiming."""
    if not identity or not identity.startswith("guest:"):
        return
    with _revoked_lock:
        if len(_revoked_guests) >= MAX_REVOKED_GUESTS:
            # Oldest-ish first. `set.pop` is arbitrary rather than FIFO, which
            # is fine: this is a bound, not a cache with a hit rate to protect,
            # and anything evicted is still covered by row ownership.
            _revoked_guests.pop()
        _revoked_guests.add(identity)


def guest_is_revoked(identity: str) -> bool:
    with _revoked_lock:
        return identity in _revoked_guests


def sign_guest(identity: str) -> str:
    """The cookie value for a guest identity. Public form of `_sign`."""
    return _sign(identity)


def set_guest_cookie(response, identity: str) -> None:
    """Put `identity` in the guest cookie on `response`, with the right flags.

    One place that knows the flags, so an endpoint issuing a cookie and the
    middleware minting one cannot drift apart.
    """
    secure, samesite = _cookie_security()
    response.set_cookie(
        GUEST_COOKIE,
        _sign(identity),
        max_age=GUEST_COOKIE_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )


def issue_fresh_guest(response) -> str:
    """Mint a brand-new guest identity onto `response`, and return it.

    Used by the endpoints that end an identity: signup, sign-in, logout and
    account deletion. Each of those is a moment where the caller stops being
    who they were, and every one of them previously left the old guest cookie
    in place.
    """
    identity = new_guest_id()
    set_guest_cookie(response, identity)
    return identity


def retire_guest(identity: str, response) -> str:
    """Revoke a guest identity and hand the browser a fresh one.

    Returns the new identity. A no-op on an account identity, so callers that
    do not know which they hold can call it unconditionally.
    """
    if not identity or not is_guest(identity):
        return identity
    revoke_guest(identity)
    return issue_fresh_guest(response)


class IdentityMiddleware(BaseHTTPMiddleware):
    """
    Resolve the caller's identity, and mint a guest cookie if they have none.

    `resolve_account` is injected rather than imported so this file stays
    ignorant of how accounts work - it is handed a callable that turns a
    session token into an account identity (or None) and asks nothing more.
    `app.py` passes `auth_service`'s; the tests pass a fake. With accounts
    switched off, nothing is injected at all and every caller is a guest,
    which is exactly the shipping configuration.
    """

    def __init__(self, app, resolve_account=None):
        super().__init__(app)
        self._resolve_account = resolve_account

    async def dispatch(self, request, call_next):
        identity = None

        token = request.cookies.get(SESSION_COOKIE)
        if token and self._resolve_account is not None:
            try:
                identity = self._resolve_account(token)
            except Exception as e:
                # A broken account lookup must not take the whole app down
                # with it. Degrade to guest: the player keeps playing, and
                # the failure is in the log rather than in their face.
                logger.warning(f"⚠️ Account session lookup failed, treating as guest: {e}")
                identity = None

        mint_guest = False
        if identity is None:
            # verify_guest_cookie, not a prefix check: a cookie the server did
            # not sign is not an identity, it is someone's suggestion.
            identity = verify_guest_cookie(request.cookies.get(GUEST_COOKIE))
            # A revoked identity is one whose games have been claimed by an
            # account. The MAC still verifies - the server did mint it - so
            # this check, not the signature, is what stops a browser (or a
            # saved cookie) returning to it after a sign-up.
            if identity is not None and guest_is_revoked(identity):
                identity = None
            if identity is None:
                identity = new_guest_id()
                mint_guest = True

        request.state.identity = identity
        response = await call_next(request)

        # An endpoint that issued its own guest cookie wins. Signup and logout
        # both hand out a FRESH identity, and a middleware Set-Cookie appended
        # after theirs would be the value the browser keeps - handing back the
        # very identity the endpoint just retired.
        if mint_guest and not self._response_sets_guest_cookie(response):
            set_guest_cookie(response, identity)
        return response

    @staticmethod
    def _response_sets_guest_cookie(response) -> bool:
        return any(
            value.startswith(GUEST_COOKIE + "=")
            for value in response.headers.getlist("set-cookie")
        )


def identity_of(request) -> str:
    """
    The resolved identity for a request, for use inside an endpoint.

    Falls back to minting a throwaway if the middleware did not run. That is
    not a normal path - it means someone called an endpoint directly in a test
    without the middleware installed - and returning a fresh guest keeps that
    working in isolation rather than raising an AttributeError that reads like
    a bug in the endpoint.
    """
    identity = getattr(request.state, "identity", None)
    if identity is None:
        return new_guest_id()
    return identity

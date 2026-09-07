"""
The account routes - and the one gate that makes them unavailable.

Every route here is real and finished. Every one of them refuses while
`ACCOUNTS_ENABLED` is false, which is the default and what ships.

THE REFUSAL IS THE POINT
------------------------
`_require_accounts_enabled()` runs first in every handler that changes or reads
account state, and raises 503 with a message written for a person:

    "Accounts aren't available yet - you're playing as a guest."

503 rather than 404, deliberately. 404 says "no such thing" and invites the
frontend to treat it as a bug or a version mismatch; 503 says "this exists and
is switched off", which is the truth, is a temporary condition by definition,
and is what tells a client it is worth asking again later. `/api/auth/config`
is the exception - it always answers, because the frontend has to be able to
ask whether accounts are on without getting an error for its trouble.

Doing it here, on the server, is what makes it real. Hiding a button in React
leaves the route open to anyone who opens devtools; there would be nothing
stopping a stranger creating accounts in a half-finished system.

COOKIES
-------
Sign-in sets the HttpOnly `zw_session` cookie and sign-out clears it. The
handler never returns the token in the body: a token in JSON is a token in
JavaScript's reach, which defeats the reason the cookie is HttpOnly.

Signing in deliberately does NOT migrate the guest's in-progress game onto the
new account. The identity changes, so `player_state` hands back that account's
game instead - a clean switch rather than a half-transfer whose failure modes
(two identities, one board) are exactly what `player_state.py` exists to
prevent. If carrying a game across sign-in is wanted later, it is one explicit
`PlayerStore` method, not an accident of ordering.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from auth_service import AuthError, accounts_enabled, auth_service
from identity import SESSION_COOKIE, _cookie_security, account_id_of, identity_of, is_guest
import google_oauth
from learning_service import LearningService
from settings_service import settings_service
from rate_limit import limit_login, limit_signup
from utils import create_success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["accounts"])
account_router = APIRouter(prefix="/api/account", tags=["accounts"])

# One string, one place. The frontend shows this verbatim rather than writing
# its own copy, so the message a user sees cannot drift from what the server
# actually does.
UNAVAILABLE_MESSAGE = "Accounts aren't available yet - you're playing as a guest."


class SignupRequest(BaseModel):
    username: str
    password: str
    # Optional at the API boundary because the existing 85 account tests sign
    # up without one and they are testing behaviour that has not changed.
    # `auth_service.validate_email` rejects anything malformed that IS sent.
    email: str = None


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class SettingsRequest(BaseModel):
    # Deliberately untyped at the boundary. `settings_service.sanitise()` is
    # the allowlist, and it has to run anyway - declaring the shape twice
    # would mean two places to update when a preference is added, and the one
    # that gets forgotten is always the one that matters.
    prefs: dict


class DeleteAccountRequest(BaseModel):
    # Typed back to confirm. Not security - the session already authorises
    # this - but a deliberate speed bump in front of the one irreversible
    # action in the product.
    confirm_username: str


def _require_accounts_enabled() -> None:
    if not accounts_enabled():
        raise HTTPException(status_code=503, detail=UNAVAILABLE_MESSAGE)


def _claim_guest_history(request: Request, user_id) -> int:
    """
    Hand this browser's guest games to the account that just signed in.

    Called from both signup and login, not just signup. Someone can play as a
    guest on a second device and then sign in to an account they already
    have, and those games are as much theirs as the ones from the day they
    registered. claim_guest_games() is one-way and once per guest identity,
    so calling it on every sign-in is safe: the second call for a given guest
    finds it already claimed and moves nothing.

    Failure here must never fail the sign-in. Signing in succeeded, the games
    are still in the database under the guest identity, and the claim can be
    retried. Losing the session over it would be the worse outcome by far.
    """
    identity = identity_of(request)
    if not is_guest(identity):
        return 0
    try:
        return LearningService.claim_guest_games(identity, "user:%s" % user_id)
    except Exception as e:
        logger.warning("Sign-in succeeded but claiming guest history did not: %s" % e)
        return 0


def _set_session_cookie(response: Response, token: str) -> None:
    secure, samesite = _cookie_security()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )


@router.get("/config")
def auth_config():
    """
    Whether accounts are switched on, and what to say if they are not.

    Always 200, even when accounts are off - this is the endpoint the UI asks
    on load to decide whether its Sign in button opens a form or the
    unavailable notice, so answering with an error would leave it guessing.
    """
    enabled = accounts_enabled()
    return create_success_response(
        "Account configuration retrieved",
        {
            "accounts_enabled": enabled,
            "guest_mode": True,
            # So the UI can decide whether to draw a Google button at all,
            # rather than drawing one that answers 503. Both must be true for
            # the button to work: accounts on, and Google configured.
            "google": enabled and google_oauth.configured(),
            "unavailable_message": None if enabled else UNAVAILABLE_MESSAGE,
        },
    )


@router.get("/me")
def whoami(request: Request):
    """
    Who the caller is. Answers for guests too, so the UI has one call.

    Not gated: with accounts off this reports `signed_in: false` and a guest
    identity, which is exactly what the header needs to render "Guest". Never
    returns the raw identity string - that value is a credential, and the
    frontend has no use for it.
    """
    identity = identity_of(request)
    account = account_id_of(identity)
    if account is None:
        return create_success_response(
            "Guest session",
            {"signed_in": False, "guest": True, "username": None, "accounts_enabled": accounts_enabled()},
        )
    user = auth_service.get_user(account)
    return create_success_response(
        "Signed in",
        {
            "signed_in": True,
            "guest": False,
            "username": user["username"] if user else None,
            "accounts_enabled": accounts_enabled(),
        },
    )


@router.post("/signup", dependencies=[Depends(limit_signup)])
def signup(request: SignupRequest, response: Response, request_ctx: Request):
    """Create an account and sign in as it. 503 while accounts are off."""
    _require_accounts_enabled()
    try:
        user = auth_service.create_user(request.username, request.password, request.email)
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    claimed = _claim_guest_history(request_ctx, user["id"])
    token = auth_service.start_session(user["id"])
    _set_session_cookie(response, token)
    return create_success_response("Account created", {
        "username": user["username"], "signed_in": True, "claimed_games": claimed,
    })


@router.post("/login", dependencies=[Depends(limit_login)])
def login(request: LoginRequest, response: Response, request_ctx: Request):
    """Sign in to an existing account. 503 while accounts are off."""
    _require_accounts_enabled()
    user = auth_service.verify_password(request.username, request.password)
    if user is None:
        # One message for both "no such user" and "wrong password". Telling
        # them apart is a free username oracle.
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    claimed = _claim_guest_history(request_ctx, user["id"])
    token = auth_service.start_session(user["id"])
    _set_session_cookie(response, token)
    return create_success_response("Signed in", {
        "username": user["username"], "signed_in": True, "claimed_games": claimed,
    })


@router.post("/logout")
def logout(request: Request, response: Response):
    """
    End this session and drop the cookie.

    Not gated behind the flag, and not an error if there was no session: sign
    out has to work even in the states nobody planned for - accounts switched
    off after someone signed in, a token whose row is already gone. The
    outcome the caller wants is "I am signed out", and that is always
    achievable.
    """
    token = request.cookies.get(SESSION_COOKIE)
    ended = auth_service.end_session(token) if token else False
    response.delete_cookie(SESSION_COOKIE, path="/")
    return create_success_response("Signed out", {"signed_in": False, "ended_session": ended})


# ---------------------------------------------------------------------------
# Google sign-in
#
# The browser is redirected to Google and back; it never handles a credential
# of its own. See google_oauth.py for why the code flow rather than an ID
# token posted from the page, and for what is and is not verified on the way
# back.
# ---------------------------------------------------------------------------

OAUTH_STATE_COOKIE = "zw_oauth_state"
# Long enough to sign in, short enough that a stale one is not lying around.
OAUTH_STATE_MAX_AGE = 10 * 60


def _google_redirect_uri(request: Request) -> str:
    """Where Google sends the browser back to.

    Configured explicitly rather than derived from the request, because it has
    to match a URI registered in the Google console exactly, and a value taken
    from a Host header is a value an attacker can influence.
    """
    configured = os.environ.get("GOOGLE_REDIRECT_URI")
    if configured:
        return configured
    return str(request.url_for("google_callback"))


def _frontend_url() -> str:
    """Where to send the browser after signing in.

    The frontend is on Vercel and this API is on Render, so "back where they
    came from" is a different origin and cannot be inferred - it is
    configuration. Defaults to the dev frontend.
    """
    return os.environ.get("FRONTEND_URL", "http://localhost:3001")


@router.get("/google/start")
def google_start(request: Request):
    """Begin Google sign-in by redirecting to Google."""
    _require_accounts_enabled()
    if not google_oauth.configured():
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured on this deployment.",
        )
    state = google_oauth.new_state()
    url = google_oauth.authorization_url(state, _google_redirect_uri(request))
    response = RedirectResponse(url, status_code=302)
    secure, samesite = _cookie_security()
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=OAUTH_STATE_MAX_AGE,
        httponly=True,
        secure=secure,
        # Lax, never None, and never Strict. Strict would not be sent at all
        # on the redirect back from Google and the flow could never complete;
        # None would make it a cross-site cookie for no reason. Lax is exactly
        # right for a top-level navigation returning to us.
        samesite="lax",
        path="/",
    )
    return response


@router.get("/google/callback", name="google_callback")
def google_callback(request: Request, code: str = None, state: str = None, error: str = None):
    """
    Finish Google sign-in.

    Ends at the same place as a password login: one internal identity,
    `user:<id>`, a session cookie, and the guest history on this browser
    claimed. Nothing downstream can tell which route an account arrived by,
    which is the point.
    """
    _require_accounts_enabled()
    if not google_oauth.configured():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured.")

    if error:
        # The user pressed cancel, most likely. Not an error worth a stack
        # trace - send them back to the app.
        logger.info(f"Google sign-in was not completed: {error}")
        return RedirectResponse(f"{_frontend_url()}?auth=cancelled", status_code=302)

    expected_state = request.cookies.get(OAUTH_STATE_COOKIE)
    # Both must be present AND equal. Missing-and-missing must not compare
    # equal, or a callback with no state at all would pass.
    if not state or not expected_state or not hmac.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail="Sign-in could not be verified. Try again.")
    if not code:
        raise HTTPException(status_code=400, detail="Google did not return an authorization code.")

    try:
        profile = google_oauth.exchange_code(code, _google_redirect_uri(request))
    except google_oauth.GoogleAuthError as e:
        logger.warning(f"⚠️ Google sign-in failed: {e}")
        raise HTTPException(status_code=401, detail="Google sign-in failed. Try again.")

    user = auth_service.find_by_federated(google_oauth.PROVIDER, profile["subject"])

    if user is None:
        email = profile.get("email")
        # Deliberately NOT linking an unknown Google account to an existing
        # account that happens to share an email address. Password signup does
        # not verify email addresses yet, so an existing account's email is
        # only a claim - and auto-linking on it would hand whoever registered
        # first the account of whoever actually owns the address, or the
        # reverse. Refuse, explain, and revisit when email verification exists
        # (recorded in DEPLOY.md).
        if email and profile.get("email_verified") and auth_service.find_by_email(email):
            raise HTTPException(
                status_code=409,
                detail=("An account already exists with that email address. "
                        "Sign in with your password instead."),
            )
        user = auth_service.create_user(
            _username_from_profile(profile), None,
            email if profile.get("email_verified") else None,
        )
        auth_service.link_federated(google_oauth.PROVIDER, profile["subject"], user["id"])
        logger.info(f"👤 Account created via Google: {user['username']} (id={user['id']})")
    else:
        # Idempotent, so a repeated callback re-links rather than erroring.
        auth_service.link_federated(google_oauth.PROVIDER, profile["subject"], user["id"])

    claimed = _claim_guest_history(request, user["id"])
    token = auth_service.start_session(user["id"])
    response = RedirectResponse(f"{_frontend_url()}?auth=ok&claimed={claimed}", status_code=302)
    _set_session_cookie(response, token)
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    return response


def _username_from_profile(profile: dict) -> str:
    """A username for a brand-new Google account.

    Derived from the email's local part where possible, stripped to what
    `validate_username` accepts, and suffixed until it is free. A collision
    loop rather than one attempt, because two people called `magnus` at
    different domains is not an error case, it is Tuesday.
    """
    base = (profile.get("email") or "").split("@")[0]
    base = re.sub(r"[^A-Za-z0-9_-]", "", base)[:24] or "player"
    if len(base) < 3:
        base = f"{base}player"[:24]
    candidate = base
    for _ in range(50):
        if auth_service.username_available(candidate):
            return candidate
        candidate = f"{base[:24]}-{secrets.token_hex(3)}"
    # 50 collisions on a random 6-hex suffix is not going to happen, but
    # falling through to a guaranteed-unique name beats raising in a sign-in.
    return f"player-{secrets.token_hex(6)}"


# ---------------------------------------------------------------------------
# The account itself: profile, preferences, password, deletion
#
# Every route here requires a real account. `_require_account()` is the only
# place that decides, so adding a route cannot accidentally skip the check -
# the same reasoning as sandbox_api's `_require()`.
# ---------------------------------------------------------------------------


def _require_account(request: Request):
    """The signed-in account id, or 401. Never trusts a client-supplied id."""
    _require_accounts_enabled()
    identity = identity_of(request)
    account_id = account_id_of(identity)
    if account_id is None:
        raise HTTPException(status_code=401, detail="You need to be signed in.")
    return account_id


@account_router.get("")
def account_profile(request: Request):
    """Username, email, and how this account signs in."""
    account_id = _require_account(request)
    user = auth_service.get_user(account_id)
    if user is None:
        # The session outlived the account - deleted in another tab, most
        # likely. Answer as if signed out rather than 500.
        raise HTTPException(status_code=401, detail="You need to be signed in.")
    methods = auth_service.auth_methods(account_id)
    return create_success_response("Account retrieved", {
        "username": user["username"],
        "email": user.get("email"),
        "created_at": user.get("created_at"),
        "auth_methods": methods,
    })


@account_router.get("/settings")
def get_settings(request: Request):
    """This account's preferences, defaults filled in."""
    account_id = _require_account(request)
    return create_success_response("Settings retrieved",
                                   {"prefs": settings_service.get(account_id)})


@account_router.put("/settings")
def put_settings(payload: SettingsRequest, request: Request):
    """
    Merge preferences into this account.

    A merge, not a replace: the frontend saves the one preference that
    changed, and replacing would wipe the other four.
    """
    account_id = _require_account(request)
    return create_success_response("Settings saved",
                                   {"prefs": settings_service.update(account_id, payload.prefs)})


@account_router.post("/password")
def change_password(payload: PasswordChangeRequest, response: Response, request: Request):
    """
    Change the password, then re-issue this browser's session.

    `change_password` ends every session for the account, which is the point
    of changing a password - but it would also sign out the person who just
    did it, on the device they did it from. So a fresh session is minted here
    and set on the way out: every OTHER device is signed out, this one is not.
    """
    account_id = _require_account(request)
    try:
        auth_service.change_password(account_id, payload.current_password, payload.new_password)
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    token = auth_service.start_session(account_id)
    _set_session_cookie(response, token)
    return create_success_response("Password changed", {"signed_in": True})


@account_router.delete("")
def delete_account(payload: DeleteAccountRequest, response: Response, request: Request):
    """
    Delete this account and its chess history, irreversibly.

    The username has to be typed back. That is not authorisation - the
    session already authorises it - it is a speed bump in front of the only
    action in the product that cannot be undone.
    """
    account_id = _require_account(request)
    user = auth_service.get_user(account_id)
    if user is None:
        raise HTTPException(status_code=401, detail="You need to be signed in.")
    if (payload.confirm_username or "").strip().lower() != user["username"].lower():
        raise HTTPException(
            status_code=400,
            detail="Type your username exactly to confirm.",
        )
    auth_service.delete_user(account_id)
    # The cookie now points at a session row that no longer exists, which
    # would resolve to a guest anyway - but leaving it set means the browser
    # keeps sending a dead credential, so clear it here.
    response.delete_cookie(SESSION_COOKIE, path="/")
    return create_success_response("Account deleted", {"signed_in": False})

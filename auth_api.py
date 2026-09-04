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

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from auth_service import AuthError, accounts_enabled, auth_service
from identity import SESSION_COOKIE, _cookie_security, account_id_of, identity_of
from rate_limit import limit_login, limit_signup
from utils import create_success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["accounts"])

# One string, one place. The frontend shows this verbatim rather than writing
# its own copy, so the message a user sees cannot drift from what the server
# actually does.
UNAVAILABLE_MESSAGE = "Accounts aren't available yet - you're playing as a guest."


class SignupRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


def _require_accounts_enabled() -> None:
    if not accounts_enabled():
        raise HTTPException(status_code=503, detail=UNAVAILABLE_MESSAGE)


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
def signup(request: SignupRequest, response: Response):
    """Create an account and sign in as it. 503 while accounts are off."""
    _require_accounts_enabled()
    try:
        user = auth_service.create_user(request.username, request.password)
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    token = auth_service.start_session(user["id"])
    _set_session_cookie(response, token)
    return create_success_response("Account created", {"username": user["username"], "signed_in": True})


@router.post("/login", dependencies=[Depends(limit_login)])
def login(request: LoginRequest, response: Response):
    """Sign in to an existing account. 503 while accounts are off."""
    _require_accounts_enabled()
    user = auth_service.verify_password(request.username, request.password)
    if user is None:
        # One message for both "no such user" and "wrong password". Telling
        # them apart is a free username oracle.
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    token = auth_service.start_session(user["id"])
    _set_session_cookie(response, token)
    return create_success_response("Signed in", {"username": user["username"], "signed_in": True})


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

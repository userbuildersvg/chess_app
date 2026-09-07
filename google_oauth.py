"""
Sign in with Google, as an authorization-code flow that runs on the server.

BUILT, AND UNCONFIGURED BY DEFAULT
----------------------------------
Exactly the shape `auth_service.py` already uses for accounts themselves: the
code is real and complete, and it is switched off by the absence of
configuration rather than by a flag someone has to remember. With no
`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in the environment,
`configured()` is False, both routes answer 503, and `/api/auth/config` says
`google: false` so the UI does not offer a button that cannot work.

Filling those two variables in is the whole of switching it on - though note
that accounts as a whole are still behind `ACCOUNTS_ENABLED`, and this rides
on top of that rather than around it.

WHY THE CODE FLOW AND NOT A GOOGLE ID TOKEN FROM THE BROWSER
------------------------------------------------------------
The alternative - Google Identity Services in the page, handing our frontend
an ID token to POST to us - means the browser is trusted to say who it is, and
the security of the whole thing rests on us verifying that token perfectly.
Here the browser never carries a credential at all. It is redirected to
Google, Google redirects it back with a single-use code, and the *server*
exchanges that code for tokens over its own TLS connection using a secret the
browser has never seen. Nothing the client sends decides who they are.

ID TOKEN VERIFICATION
---------------------
The ID token is deliberately NOT signature-verified here, and that is
Google's own documented allowance rather than a shortcut: a token received
directly from Google's token endpoint, over a TLS connection we opened to
`oauth2.googleapis.com` and validated, cannot have been substituted by anyone
who is not Google. Signature verification exists for tokens that arrive by
some other route - from the browser, from a partner, from a queue - and it is
the browser route this module deliberately does not use.

What IS checked, because TLS says nothing about them: `aud` must be our client
id (a token minted for a different application is not a sign-in to ours),
`iss` must be Google, and `sub` must be present. `email_verified` is checked
by the caller before any account linking happens.

If this ever starts accepting a token from anywhere but its own HTTPS exchange
with Google, that decision has to be revisited and real signature verification
added. That is the condition; it is written here so the next person meets it.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

PROVIDER = "google"

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

# Only what is needed to know who signed in. No Drive, no contacts, no
# offline access - a refresh token we never use is a credential we would be
# storing for nothing.
SCOPES = "openid email profile"

# Google is a dependency in the sign-in path, so it gets a timeout like every
# other outbound call in this codebase. Ten seconds is generous for a token
# exchange and short enough that a hung Google does not hold a worker.
TIMEOUT_SECONDS = float(os.environ.get("GOOGLE_OAUTH_TIMEOUT", "10"))


class GoogleAuthError(Exception):
    """Anything that went wrong in the exchange. The message is for our log."""


def client_id() -> Optional[str]:
    return os.environ.get("GOOGLE_CLIENT_ID") or None


def client_secret() -> Optional[str]:
    return os.environ.get("GOOGLE_CLIENT_SECRET") or None


def configured() -> bool:
    """Whether Google sign-in can work at all. Read at call time, not cached
    at import, so the same process can be reconfigured in a test."""
    return bool(client_id() and client_secret())


def new_state() -> str:
    """An unguessable value tying a callback to the browser that started it.

    This is the CSRF defence for the callback: without it, an attacker can
    send a victim's browser to our callback URL carrying the attacker's own
    authorization code, and the victim ends up silently signed in to the
    attacker's account - where anything they then do is visible to the
    attacker. The state is set as a short-lived cookie at /start and must come
    back matching at /callback.
    """
    return secrets.token_urlsafe(32)


def authorization_url(state: str, redirect_uri: str) -> str:
    """Where to send the browser to begin."""
    if not configured():
        raise GoogleAuthError("Google sign-in is not configured")
    from urllib.parse import urlencode

    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        # We only ever want identity, never a refresh token, so no
        # access_type=offline and no prompt=consent.
        "include_granted_scopes": "true",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def _decode_id_token_payload(id_token: str) -> dict:
    """The claims inside a JWT, without verifying its signature.

    Safe only because of where this token came from - see the module
    docstring. Padding is added back because JWT segments are base64url with
    it stripped.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise GoogleAuthError("ID token is not a JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, binascii.Error) as e:
        raise GoogleAuthError(f"ID token payload is not readable: {e}")


def exchange_code(code: str, redirect_uri: str) -> dict:
    """
    Trade an authorization code for the signer-in's identity.

    Returns {"subject", "email", "email_verified", "name"}. `subject` is
    Google's `sub` - immutable, and the only thing an account should ever be
    linked on. Raises GoogleAuthError for anything that does not come back as
    a usable identity.
    """
    if not configured():
        raise GoogleAuthError("Google sign-in is not configured")

    try:
        response = httpx.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": client_id(),
                "client_secret": client_secret(),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise GoogleAuthError(f"Could not reach Google's token endpoint: {e}")

    if response.status_code != 200:
        # Google's body here can echo the code back; log the status only.
        raise GoogleAuthError(f"Token exchange refused with {response.status_code}")

    try:
        payload = response.json()
    except ValueError as e:
        raise GoogleAuthError(f"Token endpoint did not return JSON: {e}")

    id_token = payload.get("id_token")
    if not id_token:
        raise GoogleAuthError("Token response carried no id_token")

    claims = _decode_id_token_payload(id_token)

    # aud and iss are not implied by TLS: a token minted for some other
    # application is still a genuine Google token, it is simply not a sign-in
    # to us. Accepting one would let any Google developer sign in as anybody.
    if claims.get("aud") != client_id():
        raise GoogleAuthError("ID token was issued for a different application")
    if claims.get("iss") not in VALID_ISSUERS:
        raise GoogleAuthError("ID token was not issued by Google")

    subject = claims.get("sub")
    if not subject:
        raise GoogleAuthError("ID token carries no subject")

    return {
        "subject": subject,
        "email": claims.get("email"),
        # Google sends this as a bool or as the string "true" depending on the
        # path; normalise rather than making the caller guess.
        "email_verified": claims.get("email_verified") in (True, "true", "True"),
        "name": claims.get("name"),
    }

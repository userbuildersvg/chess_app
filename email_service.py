"""
Transactional email, through Mailjet.

BUILT, AND UNCONFIGURED BY DEFAULT
----------------------------------
The same pattern as accounts and Google sign-in: the code is complete and it
is switched off by the absence of configuration rather than by a flag. With no
credentials, `configured()` is False, `send()` returns False, and the caller
carries on - a password reset request still answers exactly as it would have,
because the one thing that endpoint must never do is behave differently
depending on the state of the mail system. Locally, the reset link can be
logged instead so the flow can be walked end to end without sending anything.

CONFIGURATION
-------------
    MAILJET_API_KEY      the public key from Account Settings > API Key Management
    MAILJET_SECRET_KEY   its secret half - these are a PAIR, both required
    MAILJET_FROM_EMAIL   the sender, which must be a validated address or a
                         validated domain in Mailjet, or every send is refused
    MAILJET_FROM_NAME    the display name (optional; defaults to "Zugzwang")

Mailjet authenticates with HTTP Basic using the key as the username and the
secret as the password - unlike a single bearer token, which is why there are
two variables here rather than one.

WHAT IS NEVER LOGGED
--------------------
The credentials, and the reset link. The credentials are obvious. The link is
less so, and it is the more likely leak: logs get shipped, tailed and pasted
into chat, and a reset link in a log is an account takeover for anyone who
reads it. So the only place a token appears is the email body, and the only
place the link is printed is the explicit local-development path below, which
requires `RESET_LINK_TO_LOG=true` and says why it exists.

WHY NOT THE MAILJET SDK
-----------------------
`httpx` is already a dependency and every other outbound call in this codebase
(Gemini, three times over) is a hand-written POST with an explicit timeout. One
HTTP call does not justify a new package, and the failure behaviour needed here
- never raise, never block the response, never leak a body into a log - is
easier to guarantee when the call is right here.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Send API v3.1. v3 is still accepted by Mailjet but has a different request
# shape and a vaguer error format; pinning the version in the URL means an
# account defaulting to something else cannot change what this sends.
MAILJET_ENDPOINT = "https://api.mailjet.com/v3.1/send"

DEFAULT_FROM_NAME = "Zugzwang"

# Email is in the request path of "I forgot my password", so it gets a timeout
# like every other outbound call here. Ten seconds is generous for one API
# call and short enough that a hung Mailjet does not hold a worker.
TIMEOUT_SECONDS = float(os.environ.get("MAILJET_TIMEOUT", "10"))


def api_key():
    return os.environ.get("MAILJET_API_KEY") or None


def secret_key():
    return os.environ.get("MAILJET_SECRET_KEY") or None


def from_email():
    return os.environ.get("MAILJET_FROM_EMAIL") or None


def from_name() -> str:
    return os.environ.get("MAILJET_FROM_NAME") or DEFAULT_FROM_NAME


def configured() -> bool:
    """
    Whether email can actually be sent.

    All three of key, secret and sender, because any one of them missing fails
    at send time rather than at startup - and a half-configured mail system
    looks exactly like a working one from the outside, since the endpoint that
    uses it is deliberately silent about failure.

    Read at call time, not cached at import, so the same process can be
    reconfigured in a test.
    """
    return bool(api_key() and secret_key() and from_email())


def send(to: str, subject: str, text: str, html: str) -> bool:
    """
    Send one email. Returns whether it went.

    Never raises. Every caller is in the middle of serving a request whose
    response must not depend on whether the mail provider is up - a password
    reset that answers differently when email is broken is a password reset
    that tells an attacker when email is broken.

    Both a text and an HTML part, because a reset link that arrives as an
    unclickable blob in a plain-text client is a support ticket.
    """
    if not configured():
        return False
    payload = {
        "Messages": [
            {
                "From": {"Email": from_email(), "Name": from_name()},
                "To": [{"Email": to}],
                "Subject": subject,
                "TextPart": text,
                "HTMLPart": html,
            }
        ]
    }
    try:
        response = httpx.post(
            MAILJET_ENDPOINT,
            # Basic auth, key as username and secret as password. httpx builds
            # the header itself, which keeps the credentials out of anything
            # this module constructs and therefore out of anything it could log.
            auth=(api_key(), secret_key()),
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        # The exception type and nothing else: an httpx error string can
        # include the request headers, and those carry the credentials.
        logger.warning(f"⚠️ Could not reach Mailjet: {type(e).__name__}")
        return False

    if response.status_code >= 400:
        # Status only. Mailjet echoes the recipient and the sender back in its
        # error bodies, and neither belongs in a log.
        logger.warning(f"⚠️ Mailjet refused the message with {response.status_code}")
        return False

    # A 200 is not success on its own. Mailjet's Send API answers 200 with a
    # per-message status, so a rejected recipient or an unvalidated sender
    # arrives looking like an accepted request - which is exactly the failure
    # that would otherwise be discovered by a user not receiving their reset.
    try:
        messages = response.json().get("Messages", [])
        statuses = {m.get("Status") for m in messages}
    except Exception:
        logger.warning("⚠️ Mailjet returned a body this code could not read")
        return False

    if statuses != {"success"}:
        # The statuses, not the errors: Mailjet's per-message error details
        # name the recipient address.
        logger.warning(f"⚠️ Mailjet accepted the request but not the message: {sorted(statuses)}")
        return False
    return True


def log_reset_link_locally(link: str) -> None:
    """
    Print a reset link to the log, for local development only.

    Guarded twice - `RESET_LINK_TO_LOG=true` AND not production - because a
    reset link in a log is an account takeover for anyone who can read the
    log, and logs are the thing people paste into chat when asking for help.
    Without email configured and without this, the local flow is untestable;
    with it, it is one variable that cannot be set by accident.
    """
    from identity import is_production

    if is_production() or os.environ.get("RESET_LINK_TO_LOG", "").lower() != "true":
        return
    logger.info(f"✉️ [local only] password reset link: {link}")

"""
Transactional email, through Resend.

BUILT, AND UNCONFIGURED BY DEFAULT
----------------------------------
The same pattern as accounts and Google sign-in: the code is complete and it
is switched off by the absence of configuration rather than by a flag. With no
`RESEND_API_KEY`, `configured()` is False, `send()` returns False, and the
caller carries on - a password reset request still answers exactly as it would
have, because the one thing it must never do is behave differently depending
on the state of the mail system. Locally, the reset link is logged instead so
the flow can be walked end to end without sending anything.

WHAT IS NEVER LOGGED
--------------------
The API key, and the reset link. The key is obvious. The link is less so, and
it is the more likely leak: logs get shipped, tailed and pasted into chat, and
a reset link in a log is an account takeover for anyone who reads it. So the
only place a token appears is the email body, and the only place the link is
printed is the explicit local-development path below, which requires
`RESET_LINK_TO_LOG=true` and says why it exists.

WHY NOT THE RESEND SDK
----------------------
`httpx` is already a dependency and every other outbound call in this codebase
(Gemini, three times over) is a hand-written POST with an explicit timeout. One
HTTP call does not justify a new package, and the failure behaviour we need -
never raise, never block the response, never leak the body into a log - is
easier to guarantee when the call is right here.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"

# Resend rejects a `from` that is not on a verified domain, so this has no
# safe default - a wrong value fails at send time with a 403 that reads like a
# bug. It is configuration, and DEPLOY.md says what to put in it.
DEFAULT_FROM = "Zugzwang <onboarding@resend.dev>"

# Email is in the request path of "I forgot my password", so it gets a timeout
# like every other outbound call here. Ten seconds is generous for one API
# call and short enough that a hung Resend does not hold a worker.
TIMEOUT_SECONDS = float(os.environ.get("RESEND_TIMEOUT", "10"))


def api_key():
    return os.environ.get("RESEND_API_KEY") or None


def sender() -> str:
    return os.environ.get("RESEND_FROM") or DEFAULT_FROM


def configured() -> bool:
    """Whether email can actually be sent. Read at call time, not cached at
    import, so the same process can be reconfigured in a test."""
    return bool(api_key())


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
    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key()}",
                "Content-Type": "application/json",
            },
            json={
                "from": sender(),
                "to": [to],
                "subject": subject,
                "text": text,
                "html": html,
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        # The exception type and nothing else: an httpx error string can
        # include the request headers, and those carry the API key.
        logger.warning(f"⚠️ Could not reach Resend: {type(e).__name__}")
        return False

    if response.status_code >= 400:
        # Status only. Resend echoes parts of the request back in its error
        # bodies, and this one contains a recipient address.
        logger.warning(f"⚠️ Resend refused the message with {response.status_code}")
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

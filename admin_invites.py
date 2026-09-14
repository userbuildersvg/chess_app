"""
One-time admin invite codes: a deployment-safe way to make an account admin
without touching the database by hand.

    ADMIN_INVITE_SECRET=<strong secret>
    ADMIN_INVITE_CODE_HASHES=<hmac>,<hmac>,...

`tools/generate_admin_invites.py` prints ten raw codes once and the two env
lines above. The app only ever sees a code when someone types it: it is
normalised, HMAC-SHA256'd under the secret, and compared against the env
allowlist. HMAC rather than a bare SHA-256 so that a leaked hash list cannot
be brute-forced offline without the secret as well.

A consumed hash is written to `admin_invite_redemptions`; its primary key is
what makes a code one-time. Invalid, unknown and already-used codes all get
the same answer, so the endpoint is not an oracle for which codes exist.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time

import db

CODE_PREFIX = "zz-admin"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford: no I, L, O, U
SECRET_LEN = 12                                  # 60 bits
GROUP_LEN = 4
MAX_INPUT_LEN = 64
_STRIP_RE = re.compile(r"[^0-9A-Za-z]")
_CONFUSABLE = {"I": "1", "L": "1", "O": "0"}


class InviteError(Exception):
    """Refused; the message is always the same one, by design."""
    MESSAGE = "That admin invite code is invalid or has already been used."

    def __init__(self, category: str):
        super().__init__(self.MESSAGE)
        self.category = category


def generate_code() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(SECRET_LEN))
    return "-".join([CODE_PREFIX] + [body[i:i + GROUP_LEN] for i in range(0, SECRET_LEN, GROUP_LEN)])


def canonical(raw) -> str | None:
    """The one spelling that gets hashed, or None if the input is not a code."""
    if not raw or not isinstance(raw, str) or len(raw) > MAX_INPUT_LEN:
        return None
    cleaned = _STRIP_RE.sub("", raw).upper()
    prefix = _STRIP_RE.sub("", CODE_PREFIX).upper()
    if not cleaned.startswith(prefix):
        return None
    # Confusables only in the body: the prefix has an I in it.
    body = "".join(_CONFUSABLE.get(ch, ch) for ch in cleaned[len(prefix):])
    if len(body) != SECRET_LEN or any(ch not in ALPHABET for ch in body):
        return None
    return "-".join([CODE_PREFIX] + [body[i:i + GROUP_LEN] for i in range(0, SECRET_LEN, GROUP_LEN)])


def hash_code(code: str, secret: str | None = None) -> str:
    key = (secret if secret is not None else os.environ.get("ADMIN_INVITE_SECRET", "")).encode("utf-8")
    return hmac.new(key, code.encode("utf-8"), hashlib.sha256).hexdigest()


def configured_hashes() -> frozenset:
    return frozenset(h.strip().lower() for h in os.environ.get("ADMIN_INVITE_CODE_HASHES", "").split(",") if h.strip())


def enabled() -> bool:
    return bool(os.environ.get("ADMIN_INVITE_SECRET")) and bool(configured_hashes())


def redeem(raw_code, user_id) -> None:
    """Make `user_id` admin in exchange for an unused code, or raise InviteError.

    One transaction: the redemption row and the flag land together, and a
    second redemption of the same hash fails on the primary key before it
    grants anything.
    """
    if not enabled():
        raise InviteError("not_configured")
    code = canonical(raw_code)
    if code is None:
        raise InviteError("malformed")
    digest = hash_code(code)
    if not any(hmac.compare_digest(digest, h) for h in configured_hashes()):
        raise InviteError("unknown")
    with db.connection() as conn:
        with conn.transaction():
            already = conn.execute(
                "SELECT 1 FROM admin_invite_redemptions WHERE code_hash = %s", (digest,)).fetchone()
            if already is not None:
                raise InviteError("already_used")
            conn.execute(
                "INSERT INTO admin_invite_redemptions (code_hash, user_id, redeemed_at) VALUES (%s, %s, %s)",
                (digest, int(user_id), time.time()))
            conn.execute("UPDATE users SET is_admin = true WHERE id = %s", (int(user_id),))

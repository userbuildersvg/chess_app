"""
Password hashing, in one place.

    PBKDF2-HMAC-SHA256, 600,000 iterations, a 16-byte random salt per
    password, and an optional server-side pepper.

WHY PBKDF2 AND NOT ARGON2ID
---------------------------
600,000 iterations of PBKDF2-HMAC-SHA256 is the OWASP Password Storage
Cheat Sheet's own figure, it is in the standard library, and it costs no
memory - which matters on a 512 MB Render instance where an Argon2id login
would take ~64 MiB each and a handful of concurrent sign-ins could take the
process down. A switch later is a `SCHEME` bump plus `needs_rehash`; every
stored hash records which scheme made it.

THE PEPPER
----------
`PASSWORD_PEPPER` is a secret that lives only in the server's environment -
never in the database, the repo, the bundle or a log line. With it set,
the password is first run through HMAC-SHA256 under the pepper and the
result is what PBKDF2 stretches. A database dump alone is then not enough
to start guessing: the guesser needs the pepper too.

Hashes made before the pepper existed carry scheme `pbkdf2` and verify
without it; a successful login rehashes them under the current scheme, so
setting the pepper is a config change, not a migration. Rotating the
pepper is NOT free: hashes under the old pepper cannot be verified without
it, so a rotation needs both values for a transition - not supported here,
and deliberately so.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

ITERATIONS = 600_000
SALT_BYTES = 16
SCHEME_PLAIN = "pbkdf2"            # PBKDF2 over the password itself (legacy)
SCHEME_PEPPERED = "pbkdf2_pepper_v1"
MIN_LENGTH = 8
# A denial-of-service guard, not a policy: the server pays the PBKDF2 cost.
MAX_LENGTH = 256


def pepper() -> bytes:
    return os.environ.get("PASSWORD_PEPPER", "").encode("utf-8")


def current_scheme() -> str:
    return SCHEME_PEPPERED if pepper() else SCHEME_PLAIN


def _material(password: str, scheme: str) -> bytes:
    raw = password.encode("utf-8")
    if scheme == SCHEME_PEPPERED:
        return hmac.new(pepper(), raw, hashlib.sha256).hexdigest().encode("ascii")
    return raw


def _digest(password: str, salt: bytes, iterations: int, scheme: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", _material(password, scheme), salt, iterations)


def hash_password(password: str) -> dict:
    """salt, digest, iterations and scheme for a new password."""
    salt = secrets.token_bytes(SALT_BYTES)
    scheme = current_scheme()
    return {"salt": salt, "password_hash": _digest(password, salt, ITERATIONS, scheme),
            "iterations": ITERATIONS, "scheme": scheme}


def verify_password(password: str, salt: bytes, stored: bytes, iterations: int, scheme: str) -> bool:
    """Constant-time. A peppered hash cannot be checked without the pepper."""
    if scheme == SCHEME_PEPPERED and not pepper():
        _digest(password or "", bytes(salt), iterations, SCHEME_PLAIN)  # same wall-clock as a miss
        return False
    return hmac.compare_digest(bytes(stored), _digest(password or "", bytes(salt), iterations, scheme))


def burn_time(password: str = "") -> None:
    """Spend one verification's worth of CPU, for the paths that must answer
    'no' at the same speed as a wrong password."""
    _digest(password or "", b"\x00" * SALT_BYTES, ITERATIONS, SCHEME_PLAIN)


def needs_rehash(iterations: int, scheme: str) -> bool:
    return iterations < ITERATIONS or scheme != current_scheme()


def validate(password) -> str:
    """The password, or a ValueError with a sentence for the person."""
    password = password or ""
    if not password.strip():
        raise ValueError("Password cannot be empty.")
    if len(password) < MIN_LENGTH:
        raise ValueError(f"Password must be at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise ValueError(f"Password must be at most {MAX_LENGTH} characters.")
    return password

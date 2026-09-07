"""
Accounts: sign up, sign in, sign out. Real, and switched off by default.

WHY IT IS BUILT BUT DISABLED
----------------------------
The intent is to deploy guest play now and accounts later, without the later
part being a build. So this is the finished thing - real password hashing, real
sessions, real constraints - behind one environment variable:

    ACCOUNTS_ENABLED=true

While that is false (the default, and what ships) every account route answers
503 with a plain "not currently available", `IdentityMiddleware` is given no
account resolver, and every visitor is a guest. Turning accounts on is setting
one variable. Nothing else changes shape, which is the whole point: the seam
was already there in `player_state.py`, and `identity.py` already speaks the
only language the rest of the app understands.

That also means the disabled state is enforced on the SERVER. A frontend that
merely hides the button still ships the button; anyone with devtools finds the
route. Here the route exists, is documented, and refuses.

PASSWORDS
---------
PBKDF2-HMAC-SHA256 from the standard library, 600,000 iterations (the OWASP
2023 figure for this construction), 16-byte per-user salt. Not bcrypt/argon2
because that means a compiled dependency in `requirements.txt` for a service
that has so far deliberately hand-rolled its small pieces, and PBKDF2 at this
iteration count is a defensible choice rather than a compromise. `compare_digest`
everywhere a secret is compared, so verification time does not leak the answer.

Stored: the salt, the iteration count and the derived key. The iteration count
lives in the row on purpose - it is the thing that has to go up over time, and
storing it per-row means raising it later rehashes users as they sign in rather
than locking every existing account out.

SESSIONS
--------
Opaque 32-byte tokens, stored HASHED. A session token is a bearer credential:
anyone holding it is that user until it expires, so the database keeps only a
SHA-256 of it and a leaked table dump does not hand over live sessions. The
token itself exists exactly twice - in the response that created it and in the
user's cookie.

WHAT THIS IS NOT
----------------
No email verification, no password reset, no OAuth, no rate limiting of its
own (`rate_limit.py` covers the routes). Those are real requirements for a
public launch and each is a deliberate omission, listed in DEPLOY.md rather
than half-built here.

Storage is Postgres (Neon), through the shared pool in `db.py`. It used to be
SQLite next to `learning.db`, on Render's free plan, where the disk is
EPHEMERAL - a redeploy deleted every account. That was DEPLOY.md's blocker 1
in front of `ACCOUNTS_ENABLED`, and moving here is what removes it.

The port was deliberately narrow: `_connect()` was already the only place that
knew about SQLite, so only it, the SQL placeholders and the duplicate-username
error type changed. The locks below are kept even though Postgres does not
need them for the reason SQLite did - see `__init__`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from typing import Optional

from psycopg import errors as pg_errors
from psycopg.rows import dict_row

import db

logger = logging.getLogger(__name__)

# Kept as a name because `test_accounts.py` reads it to assert that the old
# SQLite file is never written. It now points at a file nothing creates, which
# is exactly what that assertion should find.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "accounts.db")

PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
TOKEN_BYTES = 32
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200


def accounts_enabled() -> bool:
    """
    Read at call time, never cached at import.

    Cached, a test could not switch it and the only way to try the enabled
    path would be to restart the process - which is precisely the path most
    in need of testing before it is ever switched on for real.
    """
    return os.environ.get("ACCOUNTS_ENABLED", "false").lower() == "true"


class AuthError(Exception):
    """A refusal the caller can safely show the user."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _hash_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, connect=None):
        # `connect` exists for tests, and replaces what `db_path` used to do:
        # point this service at a throwaway database so a test that creates
        # accounts does not create them in the real one. See
        # `db.temporary_schema()`. Production passes nothing and gets the
        # shared pool.
        self._connect_fn = connect
        # Kept, though Postgres no longer needs it for SQLite's reason (one
        # writer at a time). Two simultaneous signups of the same name are now
        # settled by the UNIQUE index either way - but the lock costs nothing
        # on a single-process backend, and removing it would be a behaviour
        # change smuggled into a storage port.
        self._lock = threading.Lock()
        # The schema is created once, from `schema.sql`, and it is shared with
        # the learning tables - so unlike the SQLite version there is no
        # "switched-off feature quietly creating a file" to avoid. Still lazy,
        # so importing this module reaches no network.
        self._schema_ready = False
        # A SEPARATE lock from `_lock`, and it has to be. Write methods hold
        # `_lock` and then call `_connect()`, which ensures the schema - so
        # sharing one non-reentrant lock deadlocks the very first write.
        self._schema_lock = threading.Lock()

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn()
        self._ensure_schema()
        return db.connection()

    def _ensure_schema(self) -> None:
        # An injected connection brings its own schema with it, already built.
        if self._schema_ready or self._connect_fn is not None:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            self._schema_ready = True
            db.apply_schema()

    # `_init_schema` is gone: the tables live in `schema.sql` alongside the
    # learning tables, because they are now one database and one migration
    # story rather than two files that happened to sit in the same folder.

    # ---------- validation ----------

    @staticmethod
    def validate_username(username: str) -> str:
        username = (username or "").strip()
        if not USERNAME_RE.match(username):
            raise AuthError(
                "Username must be 3-32 characters, letters, numbers, underscore or hyphen only."
            )
        return username

    @staticmethod
    def validate_password(password: str) -> str:
        password = password or ""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        # Not a policy, a denial-of-service guard: PBKDF2 cost is paid by the
        # server, and an unbounded password is an unbounded amount of our CPU
        # per login attempt.
        if len(password) > MAX_PASSWORD_LENGTH:
            raise AuthError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
        return password

    # ---------- accounts ----------

    def create_user(self, username: str, password: str) -> dict:
        username = self.validate_username(username)
        password = self.validate_password(password)
        salt = secrets.token_bytes(SALT_BYTES)
        digest = _hash_password(password, salt)
        now = time.time()
        with self._lock, self._connect() as conn:
            try:
                # RETURNING rather than lastrowid: Postgres has no equivalent,
                # and asking for the id in the same statement is one round
                # trip instead of two anyway.
                row = conn.execute(
                    "INSERT INTO users (username, username_ci, salt, password_hash, iterations, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (username, username.lower(), salt, digest, PBKDF2_ITERATIONS, now),
                ).fetchone()
            except pg_errors.UniqueViolation:
                raise AuthError("That username is already taken.", status_code=409)
            user_id = row[0]
        logger.info(f"👤 Account created: {username} (id={user_id})")
        return {"id": user_id, "username": username, "created_at": now}

    def verify_password(self, username: str, password: str) -> Optional[dict]:
        """
        The user row if the password is right, None otherwise.

        Deliberately gives the caller no way to distinguish "no such user"
        from "wrong password" - that distinction is a username oracle. A dummy
        hash is computed for a missing user so both answers cost the same
        wall-clock time.
        """
        with self._connect() as conn:
            row = conn.cursor(row_factory=dict_row).execute(
                "SELECT * FROM users WHERE username_ci = %s", ((username or "").strip().lower(),)
            ).fetchone()

        if row is None:
            _hash_password(password or "", b"\x00" * SALT_BYTES)
            return None

        # bytea comes back as `memoryview` on some psycopg paths and `bytes`
        # on others; compare_digest wants one concrete type, and pbkdf2_hmac
        # wants real bytes for the salt.
        expected = bytes(row["password_hash"])
        actual = _hash_password(password or "", bytes(row["salt"]), row["iterations"])
        if not hmac.compare_digest(expected, actual):
            return None

        # Re-hash on the way in if the cost factor has since gone up, so
        # raising it is a config change rather than a migration.
        if row["iterations"] < PBKDF2_ITERATIONS:
            new_salt = secrets.token_bytes(SALT_BYTES)
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE users SET salt = %s, password_hash = %s, iterations = %s WHERE id = %s",
                    (new_salt, _hash_password(password, new_salt), PBKDF2_ITERATIONS, row["id"]),
                )

        return {"id": row["id"], "username": row["username"]}

    def get_user(self, user_id) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, created_at FROM users WHERE id = %s", (user_id,)
            ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "username": row[1], "created_at": row[2]}

    # ---------- sessions ----------

    def start_session(self, user_id, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
        """A fresh session token. Returned in the clear exactly once - only
        its hash is stored, so it cannot be recovered from the database."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (%s, %s, %s, %s)",
                (_hash_token(token), user_id, now, now + ttl_seconds),
            )
            conn.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (now, user_id))
        return token

    def resolve_session(self, token: str) -> Optional[str]:
        """
        `user:<id>` for a live session token, None otherwise.

        This is the callable handed to `IdentityMiddleware`, and the reason
        its return type is a plain string: the middleware must not learn what
        an account is. An expired row is deleted on sight rather than swept on
        a timer - the only moment a dead session matters is when someone tries
        to use it.
        """
        if not token:
            return None
        token_hash = _hash_token(token)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, expires_at FROM sessions WHERE token_hash = %s", (token_hash,)
            ).fetchone()
            if row is None:
                return None
            user_id, expires_at = row
            if expires_at < time.time():
                with self._lock:
                    conn.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))
                return None
        return f"user:{user_id}"

    def end_session(self, token: str) -> bool:
        if not token:
            return False
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_hash_token(token),))
            return cur.rowcount > 0

    def end_all_sessions(self, user_id) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
            return cur.rowcount

    def purge_expired_sessions(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE expires_at < %s", (time.time(),))
            return cur.rowcount


auth_service = AuthService()

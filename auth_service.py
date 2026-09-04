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

Storage is SQLite next to `learning.db`. On Render's free plan that disk is
EPHEMERAL - it does not survive a redeploy. That is fine while accounts are
off and must be fixed before they go on; it is why `_connect` is the only
place that knows about SQLite.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

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
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        # Serialises schema creation and writes. SQLite handles concurrent
        # readers itself; this is here because FastAPI runs sync endpoints on
        # a thread pool and two simultaneous signups of the same name would
        # otherwise both pass the "is it taken" check. The UNIQUE index is the
        # real guarantee - this just turns a race into a clean error.
        self._lock = threading.Lock()
        # Schema creation is LAZY, and that is deliberate. Building it in the
        # constructor would create `data/accounts.db` on every start, including
        # the shipping configuration where accounts are switched off - a
        # feature that is supposed to be doing nothing would be quietly
        # touching the disk. Nothing here runs until something actually asks
        # for an account.
        self._schema_ready = False
        # A SEPARATE lock from `_lock`, and it has to be. Write methods hold
        # `_lock` and then call `_connect()`, which ensures the schema - so
        # sharing one non-reentrant lock deadlocks the very first write.
        self._schema_lock = threading.Lock()

    def _connect(self):
        self._ensure_schema()
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            # Set before creating, because _init_schema goes back through
            # _connect() and would otherwise recurse forever.
            self._schema_ready = True
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
            self._init_schema()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT NOT NULL,
                    username_ci   TEXT NOT NULL,
                    salt          BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    iterations    INTEGER NOT NULL,
                    created_at    REAL NOT NULL,
                    last_login_at REAL
                );
                -- Case-insensitive uniqueness, enforced by the database rather
                -- than by a check-then-insert that two threads can both pass.
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_ci
                    ON users (username_ci);

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id    INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions (expires_at);
                """
            )

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
                cur = conn.execute(
                    "INSERT INTO users (username, username_ci, salt, password_hash, iterations, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (username, username.lower(), salt, digest, PBKDF2_ITERATIONS, now),
                )
            except sqlite3.IntegrityError:
                raise AuthError("That username is already taken.", status_code=409)
            user_id = cur.lastrowid
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
            row = conn.execute(
                "SELECT * FROM users WHERE username_ci = ?", ((username or "").strip().lower(),)
            ).fetchone()

        if row is None:
            _hash_password(password or "", b"\x00" * SALT_BYTES)
            return None

        expected = row["password_hash"]
        actual = _hash_password(password or "", row["salt"], row["iterations"])
        if not hmac.compare_digest(expected, actual):
            return None

        # Re-hash on the way in if the cost factor has since gone up, so
        # raising it is a config change rather than a migration.
        if row["iterations"] < PBKDF2_ITERATIONS:
            new_salt = secrets.token_bytes(SALT_BYTES)
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE users SET salt = ?, password_hash = ?, iterations = ? WHERE id = ?",
                    (new_salt, _hash_password(password, new_salt), PBKDF2_ITERATIONS, row["id"]),
                )

        return {"id": row["id"], "username": row["username"]}

    def get_user(self, user_id) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}

    # ---------- sessions ----------

    def start_session(self, user_id, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
        """A fresh session token. Returned in the clear exactly once - only
        its hash is stored, so it cannot be recovered from the database."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_hash_token(token), user_id, now, now + ttl_seconds),
            )
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id))
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
                "SELECT user_id, expires_at FROM sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] < time.time():
                with self._lock:
                    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
        return f"user:{row['user_id']}"

    def end_session(self, token: str) -> bool:
        if not token:
            return False
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
            return cur.rowcount > 0

    def end_all_sessions(self, user_id) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            return cur.rowcount

    def purge_expired_sessions(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            return cur.rowcount


auth_service = AuthService()

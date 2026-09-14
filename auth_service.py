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

# How long a password reset link works for. Long enough to survive a slow
# mail hop and someone finishing what they were doing first; short enough that
# a link sitting in an inbox someone else later reads is usually already dead.
RESET_TTL_SECONDS = int(os.environ.get("RESET_TTL_SECONDS", 45 * 60))
RESET_TOKEN_BYTES = 32

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
# Deliberately permissive. The only thing worth rejecting here is input that
# is obviously not an address at all; anything stricter starts refusing valid
# addresses, and the real proof that an address exists is a message arriving
# at it - which this milestone does not send (no verification, no reset; see
# DEPLOY.md).
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 254  # RFC 5321's limit on a forward path
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

    @staticmethod
    def validate_email(email):
        """A normalised address, or None if none was supplied.

        Lower-cased for the uniqueness key only - the address is also stored
        as typed, because the local part of an address is technically
        case-sensitive and rewriting what someone gave us is not our call.
        """
        if email is None or not str(email).strip():
            return None
        email = str(email).strip()
        if len(email) > MAX_EMAIL_LENGTH or not EMAIL_RE.match(email):
            raise AuthError("That does not look like an email address.")
        return email

    # ---------- accounts ----------

    def create_user(self, username: str, password, email=None) -> dict:
        """
        A new account.

        `password` may be None, and that is not an oversight: an account
        created by signing in with Google has no password and must not be
        given a guessable placeholder. Such a row simply has no usable
        password hash, and `verify_password` refuses it - so the only way into
        that account is the provider it was created from.
        """
        username = self.validate_username(username)
        email = self.validate_email(email)
        if password is None:
            # 32 random bytes hashed under the same parameters as a real
            # password. Not a sentinel value and not an empty string: whatever
            # ends up in that column should be indistinguishable from a hash,
            # so that a bug elsewhere cannot turn "no password" into "any
            # password". `has_password` is the flag that actually decides.
            salt = secrets.token_bytes(SALT_BYTES)
            digest = _hash_password(secrets.token_hex(32), salt)
            has_password = False
        else:
            password = self.validate_password(password)
            salt = secrets.token_bytes(SALT_BYTES)
            digest = _hash_password(password, salt)
            has_password = True
        now = time.time()
        with self._lock, self._connect() as conn:
            try:
                # RETURNING rather than lastrowid: Postgres has no equivalent,
                # and asking for the id in the same statement is one round
                # trip instead of two anyway.
                row = conn.execute(
                    "INSERT INTO users (username, username_ci, salt, password_hash,"
                    " iterations, created_at, email, email_ci, has_password)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (username, username.lower(), salt, digest, PBKDF2_ITERATIONS, now,
                     email, email.lower() if email else None, has_password),
                ).fetchone()
            except pg_errors.UniqueViolation as e:
                # Two different constraints reach here, and telling them apart
                # matters: "that username is taken" and "that email is already
                # registered" are different problems for the person signing up.
                if "email" in str(e).lower():
                    raise AuthError("That email is already registered.", status_code=409)
                raise AuthError("That username is already taken.", status_code=409)
            user_id = row[0]
        logger.info(f"👤 Account created: {username} (id={user_id})")
        return {"id": user_id, "username": username, "email": email, "created_at": now}

    # ---------- federated sign-in ----------

    def find_by_federated(self, provider: str, subject: str):
        """The account linked to a provider identity, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT u.id, u.username FROM federated_identities f"
                " JOIN users u ON u.id = f.user_id"
                " WHERE f.provider = %s AND f.subject = %s",
                (provider, subject),
            ).fetchone()
        return {"id": row[0], "username": row[1]} if row else None

    def username_available(self, username: str) -> bool:
        """Whether a username is free. Advisory only - the UNIQUE index is
        what actually decides, and the caller must still handle losing the
        race between this answer and its INSERT."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE username_ci = %s", ((username or "").strip().lower(),)
            ).fetchone()
        return row is None

    def find_by_email(self, email):
        """The account with this address, or None."""
        email = self.validate_email(email)
        if not email:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username FROM users WHERE email_ci = %s", (email.lower(),)
            ).fetchone()
        return {"id": row[0], "username": row[1]} if row else None

    def auth_methods(self, user_id) -> list:
        """How this account can sign in, e.g. ["password", "google"].

        Read rather than inferred, because the two are independent: an
        account can have a password, a linked provider, or both, and the
        settings screen has to say which without guessing.
        """
        methods = []
        with self._connect() as conn:
            row = conn.execute(
                'SELECT has_password FROM users WHERE id = %s', (user_id,)
            ).fetchone()
            if row and row[0]:
                methods.append('password')
            for r in conn.execute(
                'SELECT provider FROM federated_identities WHERE user_id = %s ORDER BY provider',
                (user_id,),
            ).fetchall():
                methods.append(r[0])
        return methods

    def link_federated(self, provider: str, subject: str, user_id) -> None:
        """
        Record that a provider identity belongs to an account.

        The key is (provider, subject) - the provider's own immutable id, not
        the email address. Emails get changed and, inside a workspace,
        reassigned to different people; linking on one is how an account ends
        up handed to a stranger. ON CONFLICT DO NOTHING makes a repeated
        sign-in a no-op rather than an error, which is what makes the whole
        OAuth callback safe to retry.
        """
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO federated_identities (provider, subject, user_id, created_at)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (provider, subject) DO NOTHING",
                (provider, subject, user_id, time.time()),
            )

    def verify_password(self, username: str, password: str) -> Optional[dict]:
        """
        The user row if the password is right, None otherwise.

        Deliberately gives the caller no way to distinguish "no such user"
        from "wrong password" - that distinction is a username oracle. A dummy
        hash is computed for a missing user so both answers cost the same
        wall-clock time.
        """
        # Username OR email, so that someone who signed up with both does not
        # have to remember which one this form wanted.
        identifier = (username or "").strip().lower()
        with self._connect() as conn:
            row = conn.cursor(row_factory=dict_row).execute(
                "SELECT * FROM users WHERE username_ci = %s OR email_ci = %s",
                (identifier, identifier),
            ).fetchone()

        if row is None:
            _hash_password(password or "", b"\x00" * SALT_BYTES)
            return None

        # An account created through Google has no password. Spend the same
        # wall-clock time refusing it as a wrong password costs, so that "this
        # account is Google-only" is not something an attacker can learn by
        # timing the response.
        if not row.get("has_password", True):
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
                "SELECT id, username, created_at, email, is_admin FROM users WHERE id = %s", (user_id,)
            ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "username": row[1], "created_at": row[2], "email": row[3],
                "is_admin": bool(row[4])}

    def change_password(self, user_id, current_password: str, new_password: str) -> None:
        """
        Replace a password, having first proved the old one.

        Requiring the current password is not ceremony: the session cookie is
        all an attacker needs if they have borrowed an unlocked browser, and
        without this check that is enough to lock the real owner out of their
        own account permanently. It is the one place where "you are signed in"
        is deliberately not sufficient.

        Refused outright for an account that has no password - a Google
        account's credential lives at Google, and inventing one here would
        create a second way in that its owner never asked for.
        """
        with self._connect() as conn:
            row = conn.cursor(row_factory=dict_row).execute(
                "SELECT * FROM users WHERE id = %s", (user_id,)
            ).fetchone()
        if row is None:
            raise AuthError("No such account.", status_code=404)
        if not row.get("has_password", True):
            raise AuthError(
                "This account signs in with Google and has no password to change.",
                status_code=400,
            )

        expected = bytes(row["password_hash"])
        actual = _hash_password(current_password or "", bytes(row["salt"]), row["iterations"])
        if not hmac.compare_digest(expected, actual):
            raise AuthError("Your current password is not right.", status_code=401)

        new_password = self.validate_password(new_password)
        salt = secrets.token_bytes(SALT_BYTES)
        digest = _hash_password(new_password, salt)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE users SET salt = %s, password_hash = %s, iterations = %s WHERE id = %s",
                (salt, digest, PBKDF2_ITERATIONS, user_id),
            )
        # Every other session is now signing in with a password that no longer
        # exists. Ending them is the point of changing a password after a
        # suspected compromise; the caller re-issues one for this browser.
        ended = self.end_all_sessions(user_id)
        logger.info(f"🔑 Password changed for user {user_id}; {ended} session(s) ended")

    def delete_user(self, user_id) -> None:
        """
        Delete an account and everything hanging off it.

        One statement, because every dependent row is reachable by ON DELETE
        CASCADE from here: sessions, federated identities, settings, and the
        games - which take their moves with them. That is the whole reason
        ownership lives on `games` and cascades downward rather than being
        repeated on every table; deletion is the case where a missed foreign
        key leaves someone's chess history in the database after they asked
        for it to be gone.

        `claimed_guests` rows are deliberately NOT removed. They are a ledger
        of which guest identities have already been converted, not user
        content, and dropping one would let that guest identity be claimed a
        second time - by whoever next signs up in a browser still carrying
        that cookie.

        > ⚠️ **Every OWNER-KEYED table has to be listed here by hand.**
        > `owner` is the opaque identity string, not a foreign key to `users`,
        > so no cascade reaches these rows - that is the price of the identity
        > seam that lets a guest and an account be the same kind of thing.
        > `imported_games` was added by the improvement profile and was missed,
        > and the result was an account "deleted" with its entire imported
        > library and every finding still in the database. If you add a table
        > keyed on `owner`, add its DELETE to the list below in the same
        > commit. `game_findings` is the exception and needs no line: it hangs
        > off `imported_games.id` by a real foreign key and cascades.
        """
        owner = f"user:{user_id}"
        with self._lock, self._connect() as conn:
            with conn.transaction():
                # Games played here, with their moves by cascade.
                conn.execute("DELETE FROM games WHERE owner = %s", (owner,))
                # The improvement profile: imported games, and their findings
                # by cascade. See the warning above.
                conn.execute("DELETE FROM imported_games WHERE owner = %s", (owner,))
                # The account itself, taking sessions, settings, federated
                # identities and reset tokens with it.
                conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
        logger.info(f"🗑️ Account {user_id} deleted, with its games and imported library")

    # ---------- password reset ----------

    def create_reset_token(self, email: str):
        """
        Mint a reset token for the account with this email, or return None.

        Returns `(token, user)` on success and None when there is no such
        account, when it has no email, or when it signs in with Google and has
        no password to reset. **The caller must answer identically in every
        one of those cases** - the whole point of returning None rather than
        raising is that the endpoint above cannot accidentally turn "no such
        account" into a different HTTP response, which is a free check on
        whether an address is registered.

        Any outstanding tokens for the account are invalidated first. Two live
        reset links for one account is two chances for the older one - sitting
        in an inbox, or in a forwarded message - to still work.
        """
        account = self.find_by_email(email)
        if account is None:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT has_password FROM users WHERE id = %s", (account["id"],)
            ).fetchone()
        if not row or not row[0]:
            # A Google account has no password. Setting one through a reset
            # link would create a second way in that its owner never asked
            # for, so this is refused - and the endpoint tells them by email
            # rather than by HTTP status.
            return None

        token = secrets.token_urlsafe(RESET_TOKEN_BYTES)
        now = time.time()
        with self._lock, self._connect() as conn:
            with conn.transaction():
                conn.execute(
                    "UPDATE password_resets SET used_at = %s"
                    " WHERE user_id = %s AND used_at IS NULL",
                    (now, account["id"]),
                )
                conn.execute(
                    "INSERT INTO password_resets (token_hash, user_id, created_at, expires_at)"
                    " VALUES (%s, %s, %s, %s)",
                    (_hash_token(token), account["id"], now, now + RESET_TTL_SECONDS),
                )
        # The user id, never the token, and never the address.
        logger.info(f"🔑 Password reset token issued for user {account['id']}")
        return token, account

    def account_has_password(self, email: str) -> bool:
        """Whether the account with this email signs in with a password.

        Used only to decide WHICH email to send. Never to decide what to
        answer over HTTP.
        """
        account = self.find_by_email(email)
        if account is None:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT has_password FROM users WHERE id = %s", (account["id"],)
            ).fetchone()
        return bool(row and row[0])

    def reset_password(self, token: str, new_password: str) -> dict:
        """
        Redeem a reset token and set a new password.

        Raises AuthError for a token that is unknown, expired, or already
        used - one message for all three, because telling them apart tells
        someone probing which of their guesses were once real.

        Everything that must happen together happens in one transaction: the
        password changes, the token is marked used, and every OTHER
        outstanding token for the account is killed. If any of it fails, none
        of it did.

        Sessions are ended AFTER the transaction commits, and deliberately
        all of them. A password reset is what someone does when they think
        another person has their account; leaving that person's existing
        session alive is leaving the door they came in through open.
        """
        if not token:
            raise AuthError("That reset link is not valid any more.", status_code=400)
        new_password = self.validate_password(new_password)
        token_hash = _hash_token(token)
        now = time.time()

        with self._lock, self._connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT user_id, expires_at, used_at FROM password_resets"
                    " WHERE token_hash = %s FOR UPDATE",
                    (token_hash,),
                ).fetchone()
                # One message for unknown, expired and already-used. The three
                # are different facts about someone else's account and none of
                # them is the requester's business.
                if row is None or row[2] is not None or row[1] < now:
                    raise AuthError("That reset link is not valid any more.", status_code=400)
                user_id = row[0]

                salt = secrets.token_bytes(SALT_BYTES)
                digest = _hash_password(new_password, salt)
                conn.execute(
                    "UPDATE users SET salt = %s, password_hash = %s, iterations = %s,"
                    " has_password = true WHERE id = %s",
                    (salt, digest, PBKDF2_ITERATIONS, user_id),
                )
                # This token, and any sibling still outstanding.
                conn.execute(
                    "UPDATE password_resets SET used_at = %s"
                    " WHERE user_id = %s AND used_at IS NULL",
                    (now, user_id),
                )

        ended = self.end_all_sessions(user_id)
        logger.info(f"🔑 Password reset completed for user {user_id}; {ended} session(s) ended")
        user = self.get_user(user_id)
        return user or {"id": user_id}

    def purge_expired_resets(self) -> int:
        """Drop reset rows that are long past use. Safe to run repeatedly.

        Kept a day beyond expiry rather than deleted on the hour, so that a
        person clicking a stale link gets "this is not valid any more" from a
        row that still exists rather than from a lookup that finds nothing -
        the same answer either way, but the audit trail survives the day.
        """
        try:
            with self._lock, self._connect() as conn:
                return conn.execute(
                    "DELETE FROM password_resets WHERE expires_at < %s",
                    (time.time() - 24 * 60 * 60,),
                ).rowcount
        except Exception as e:
            logger.warning(f"⚠️ Could not purge expired reset tokens: {e}")
            return 0

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

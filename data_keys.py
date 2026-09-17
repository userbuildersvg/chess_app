"""
Per-account encryption of the chess data that could reconstruct someone's
games and decisions, and the key destruction that makes deletion final.

    APP_MASTER_KEY (env, 32 bytes, base64 or hex)
      └─ wraps one random 256-bit data key per account  (user_data_keys)
           └─ AES-256-GCM over each sensitive field       ("enc1:<base64>")

WHAT IS ENCRYPTED
-----------------
Account-owned rows only: imported PGNs, the position snapshot on every
finding, the intent / diagnosis / rule / caveat text of a durable
correction, its evidence packet and practice position. Counts, themes,
timestamps, source labels and pseudonymous actor keys stay in the clear -
they are what the product and the admin overview aggregate, and none of
them can rebuild a game. Guest rows are not encrypted: a guest has no
account row to hang a key on, the identity is a cookie, and nothing
imported as a guest is ever claimed into an account.

READ-BOTH
---------
Rows written before this existed are plaintext and stay readable; a value
is only decrypted when it carries the `enc1:` prefix. New writes are
encrypted whenever a master key is configured. `tools/encrypt_existing.py`
converts what is already there, once, after the key is set.

WHY THIS MAKES DELETION FINAL
-----------------------------
Deleting an account hard-deletes its rows AND overwrites its wrapped data
key with zeros (`destroyed_at` set). A database backup taken before the
deletion still holds the encrypted rows - and, being a backup of the same
database, the wrapped key too, encrypted under the master key. What it does
NOT hold is the plaintext: without the master key, the wrapped key is
noise, and a restore of an older backup would resurrect a key that the
live database has since destroyed. So the honest claim is the one in
DataRetention.tsx: encrypted copies that remain in backups cannot be read
without the destroyed key, and the operator does not keep the key.

PRODUCTION REFUSES TO BOOT WITHOUT THE KEY. On a laptop it is optional
and the module says so once at startup.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import secrets
import threading
import time
from typing import Optional

import db

logger = logging.getLogger(__name__)

PREFIX = "enc1:"
KEY_BYTES = 32
NONCE_BYTES = 12
MAX_CACHED = 5000


class KeyDestroyed(Exception):
    """The owner's data key has been destroyed; nothing of theirs can be read."""


class KeyUnreadable(Exception):
    """The owner's wrapped data key does not open under the current
    APP_MASTER_KEY - the master key was changed after this account first
    wrote encrypted data. Nothing is lost: restoring the previous master key
    makes every row readable again. Until then the account can read nothing
    sealed and must not write anything new under a key it cannot open."""

    MESSAGE = ("This account's saved data is encrypted under a server key that is no "
               "longer configured, so nothing can be saved to it right now. The operator "
               "has to restore the previous APP_MASTER_KEY.")


def decode_master_key(raw: str) -> bytes:
    """The 32 key bytes from a base64 or hex string, or a RuntimeError."""
    raw = raw.strip()
    for decode in (lambda v: base64.b64decode(v, validate=True), bytes.fromhex,
                   lambda v: base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))):
        try:
            key = decode(raw)
        except (ValueError, binascii.Error):
            continue
        if len(key) == KEY_BYTES:
            return key
    raise RuntimeError("APP_MASTER_KEY must be 32 bytes, base64 or hex encoded.")


def _master_key() -> Optional[bytes]:
    raw = os.environ.get("APP_MASTER_KEY", "").strip()
    return decode_master_key(raw) if raw else None


def rewrap_all(old_master: bytes, new_master: bytes, connect=None, dry_run: bool = True) -> dict:
    """
    Re-wrap every account data key from `old_master` to `new_master`, in
    place. The per-account keys and every sealed row are untouched; only the
    wrapping changes, so after this one master key opens everything.

    This exists because two backends with different APP_MASTER_KEYs shared one
    database (the dev stack and Render), and each could only open the accounts
    it had created. Keys already under `new_master` are left alone; keys under
    neither are reported and left alone; destroyed keys are skipped.
    """
    counts = {"rewrapped": 0, "already": 0, "unknown": 0, "destroyed": 0}
    opener = connect or db.connection
    with opener() as conn:
        with conn.transaction():
            rows = conn.execute(
                "SELECT user_id, encrypted_data_key, destroyed_at FROM user_data_keys ORDER BY user_id"
            ).fetchall()
            for uid, wrapped, gone in rows:
                if gone is not None:
                    counts["destroyed"] += 1
                    continue
                aad, blob = _wrap_aad(uid), bytes(wrapped)
                try:
                    _decrypt(new_master, blob, aad)
                    counts["already"] += 1
                    continue
                except Exception:
                    pass
                try:
                    key = _decrypt(old_master, blob, aad)
                except Exception:
                    counts["unknown"] += 1
                    logger.error(f"🔐 Data key for account {uid} opens under neither master key - left as is")
                    continue
                counts["rewrapped"] += 1
                if not dry_run:
                    conn.execute(
                        "UPDATE user_data_keys SET encrypted_data_key = %s WHERE user_id = %s",
                        (_encrypt(new_master, key, aad), uid),
                    )
                    forget(uid)
    return counts


def enabled() -> bool:
    return _master_key() is not None


def generate_master_key() -> str:
    return base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii")


def check_startup(production: bool) -> None:
    """Called once by app.py. Production without a key is a misconfiguration
    that would silently write plaintext, so it refuses; a laptop just says."""
    if enabled():
        logger.info("🔐 Account data encryption: on (APP_MASTER_KEY set)")
    elif production:
        raise RuntimeError(
            "APP_MASTER_KEY is not set. Production refuses to start rather than store "
            "account data unencrypted - generate one with tools/generate_master_key.py."
        )
    else:
        logger.warning("🔓 APP_MASTER_KEY unset - account data is stored unencrypted on this laptop")


# --- AES-GCM ----------------------------------------------------------------

def _aead(key: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key)


def _encrypt(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_BYTES)
    return nonce + _aead(key).encrypt(nonce, plaintext, aad)


def _decrypt(key: bytes, blob: bytes, aad: bytes) -> bytes:
    return _aead(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)


# --- per-account keys ---------------------------------------------------------

_cache: dict = {}
_cache_lock = threading.Lock()


def owner_id(owner) -> Optional[int]:
    """The account id inside `user:<id>` (or a bare id); None for a guest."""
    if isinstance(owner, bool):
        return None
    if isinstance(owner, int):
        return owner
    if isinstance(owner, str):
        # "user:42" from the identity seam, or "42" as account_id_of returns it.
        candidate = owner[5:] if owner.startswith("user:") else owner
        if candidate.isdigit():
            return int(candidate)
    return None


def _wrap_aad(uid: int) -> bytes:
    return f"zugzwang-user-key:{uid}".encode()


def _load_key(uid: int, create: bool, connect=None) -> Optional[bytes]:
    master = _master_key()
    if master is None:
        return None
    with _cache_lock:
        cached = _cache.get(uid)
    if cached is not None:
        if cached == b"":
            raise KeyDestroyed(uid)
        return cached
    opener = connect or db.connection
    with opener() as conn:
        row = conn.execute(
            "SELECT encrypted_data_key, destroyed_at FROM user_data_keys WHERE user_id = %s", (uid,)
        ).fetchone()
        if row is not None and row[1] is not None:
            _remember(uid, b"")
            raise KeyDestroyed(uid)
        if row is None:
            if not create:
                return None
            fresh = secrets.token_bytes(KEY_BYTES)
            wrapped = _encrypt(master, fresh, _wrap_aad(uid))
            # Two requests may race to create the first key; the primary key
            # decides, and the loser reads back the winner's.
            conn.execute(
                "INSERT INTO user_data_keys (user_id, encrypted_data_key, key_version, created_at)"
                " VALUES (%s, %s, 1, %s) ON CONFLICT (user_id) DO NOTHING", (uid, wrapped, time.time()))
            row = conn.execute(
                "SELECT encrypted_data_key, destroyed_at FROM user_data_keys WHERE user_id = %s", (uid,)
            ).fetchone()
    try:
        key = _decrypt(master, bytes(row[0]), _wrap_aad(uid))
    except Exception as exc:
        # InvalidTag from AES-GCM: the wrapped key was made under a different
        # master key. A named error, so a request answers with a sentence
        # instead of a 500, and the log says which account and why.
        logger.error(f"🔐 Data key for account {uid} does not open under the current "
                     f"APP_MASTER_KEY ({type(exc).__name__}) - was the master key rotated?")
        raise KeyUnreadable(uid) from exc
    _remember(uid, key)
    return key


def _remember(uid: int, key: bytes) -> None:
    with _cache_lock:
        if len(_cache) >= MAX_CACHED:
            _cache.clear()
        _cache[uid] = key


def forget(uid) -> None:
    with _cache_lock:
        _cache.pop(int(uid), None)


def destroy(uid, connect=None) -> bool:
    """Overwrite the wrapped key with zeros and mark it destroyed. Anything
    encrypted under it - live or in a backup - is unreadable from now on."""
    uid = int(uid)  # account ids arrive as strings from the identity cookie
    opener = connect or db.connection
    with opener() as conn:
        n = conn.execute(
            "UPDATE user_data_keys SET encrypted_data_key = %s, destroyed_at = %s"
            " WHERE user_id = %s AND destroyed_at IS NULL",
            (b"\x00" * (NONCE_BYTES + KEY_BYTES + 16), time.time(), uid),
        ).rowcount
    _remember(uid, b"")
    if n:
        logger.info(f"🔐 Data key destroyed for account {uid}")
    return n > 0


# --- field helpers ------------------------------------------------------------

def seal(owner, text: Optional[str], connect=None) -> Optional[str]:
    """Encrypt `text` for `owner`, or return it unchanged when there is no key
    (no master key, or a guest). Idempotent on already-sealed values."""
    if text is None or text.startswith(PREFIX):
        return text
    uid = owner_id(owner)
    if uid is None:
        return text
    key = _load_key(uid, create=True, connect=connect)
    if key is None:
        return text
    blob = _encrypt(key, text.encode("utf-8"), _wrap_aad(uid))
    return PREFIX + base64.b64encode(blob).decode("ascii")


def unseal(owner, text: Optional[str], connect=None) -> Optional[str]:
    """The plaintext of a sealed value, plaintext passed through, or None if
    it cannot be read (key destroyed or missing). Never raises on a row."""
    if text is None or not text.startswith(PREFIX):
        return text
    uid = owner_id(owner)
    if uid is None:
        return None
    try:
        key = _load_key(uid, create=False, connect=connect)
        if key is None:
            return None
        return _decrypt(key, base64.b64decode(text[len(PREFIX):]), _wrap_aad(uid)).decode("utf-8")
    except (KeyDestroyed, KeyUnreadable):
        return None
    except Exception as exc:  # corrupt blob: unreadable, not a 500
        logger.warning(f"🔐 Sealed value for account {uid} could not be read: {type(exc).__name__}")
        return None


def is_sealed(text) -> bool:
    return isinstance(text, str) and text.startswith(PREFIX)

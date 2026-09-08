"""
Closed beta access - the codes, the grants, and the one question that matters.

WHAT THIS IS
------------
Zugzwang is no longer publicly reachable. `beta_gate.py` asks this module one
question on every API request - `has_access(identity)` - and refuses the
request if the answer is no. Everything else here exists to make that answer
correct: minting codes, redeeming them, moving a grant from a guest onto the
account they sign up as, and the admin operations behind `tools/beta_codes.py`.

The authorization state lives in Postgres (migration 008) and nowhere else.
There is no beta cookie, no signed grant token, no client-supplied claim. A
visitor's browser carries exactly what it carried before - the HMAC-signed
guest identity cookie, or an account session - and this module looks that
identity up in a table. That is the whole reason DevTools, localStorage, React
state and `disable JavaScript` are irrelevant to it: none of them is an input.

THE CODE IS NEVER STORED
------------------------
`beta_codes.code_hash` is HMAC-SHA256 of the canonical code under a server-side
pepper. Not plain SHA-256: `ZG-BETA-XXXX-XXXX` over a 32-character alphabet is
40 bits, which is far beyond an online guesser facing a rate limiter and well
within an offline sweep of a leaked table. The pepper is what makes a stolen
table worthless.

The pepper is `BETA_CODE_PEPPER`, falling back to `SESSION_COOKIE_SECRET`
because any deployment that has accounts on already has that set and stable.
If neither is set this module RAISES rather than falling back to a random
per-process key - a random key would mean codes minted by the admin tool do
not validate in the server, which presents as "every code is wrong" and is a
miserable thing to debug. Failing at the first hash says what is missing.

**Rotating the pepper invalidates every outstanding code.** There is no way
around that and no migration for it: the codes cannot be re-hashed because the
server does not have them. Treat it exactly like `SESSION_COOKIE_SECRET` - set
once, never change.

WHY A GUEST CAN HOLD A GRANT
----------------------------
A redemption is bound to an identity string, and a guest identity is a
perfectly good one: it is server-minted, HMAC-signed, and already the key that
`games.owner` and `player_state` use. So redeeming from the landing page grants
access immediately, and the visitor can play before deciding whether to make an
account. When they do sign up, `transfer_to_account()` rewrites the redemption
onto `user:<id>` and sets `users.beta_access`, one-way, exactly as
`claim_guest_games()` rewrites ownership. From then on the access belongs to
the account and survives sign-out, a new browser and a cleared cookie - which
is what "the code is never required again" has to mean.

THE CACHE, AND WHY IT IS SMALL
------------------------------
`has_access()` runs on every gated request, so a database round trip per
request is not acceptable on a free instance sharing one connection pool with
the engine. Answers are cached in-process for a few seconds, and the cache is
INVALIDATED at the two moments an answer changes (redemption, transfer) rather
than merely expiring. The TTL is therefore a bound on staleness in the cases
nothing here knows about - an operator disabling a code in another process -
and not the mechanism by which access starts working.

Positive and negative answers are both cached. A negative TTL that is too long
would leave a visitor staring at the landing page after a successful
redemption; the explicit invalidation is what prevents that, so the TTL can
stay short without costing a query per request to a locked-out prober.
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

import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The code format
# ---------------------------------------------------------------------------

CODE_PREFIX = "ZG-BETA"

# Crockford's base32 alphabet: no I, L, O or U. The first three are excluded
# because they are indistinguishable from 1, 1 and 0 in most typefaces and
# these codes get read off a screen and typed into a box; U is excluded
# because its absence is what stops a random generator producing a word
# somebody has to read out over the phone.
#
# Exactly 32 characters, so each one carries five bits and the arithmetic
# below ("eight characters is 40 bits") is true rather than approximate.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Two groups of four. Fixed by the format the product asked for.
GROUP_LEN = 4
GROUPS = 2
SECRET_LEN = GROUP_LEN * GROUPS

# What a person may type. Deliberately permissive - lowercase, missing dashes,
# stray spaces - because rejecting a correct code over punctuation is a
# support ticket and reveals nothing useful in exchange.
_STRIP_RE = re.compile(r"[^A-Za-z0-9]")

# Crockford's decoding rules, which exist precisely so that a code read off a
# screen and retyped still works.
_CONFUSABLE = {"I": "1", "L": "1", "O": "0"}

# What `canonical()` will ACCEPT, as opposed to what `generate_code()` draws
# from. The two differ by exactly one character, `U`.
#
# Crockford leaves U out of the alphabet so that a random draw can never spell
# something unfortunate, and that reasoning applies to generation and only to
# generation. A hand-chosen vanity code (`tools/beta_codes.py add`) is written
# by a person who can see what it says, and refusing one because it contains a
# U would be the encoding's aesthetic preference overruling its author.
#
# It costs nothing and weakens nothing: no generated code contains a U, so
# there is no pair of codes this makes ambiguous, and the hash is taken over
# the canonical string either way. The 40-bit entropy claim above is about
# `ALPHABET` and is untouched - a vanity code has whatever entropy its author
# gave it, which is the trade they are making knowingly.
INPUT_ALPHABET = ALPHABET + "U"


class BetaError(Exception):
    """A redemption that cannot proceed, with the message a person should see."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# One refusal for a code that does not exist, is disabled, has expired, or has
# been used up.
#
# Telling those apart would help a tester with a genuinely expired code, and it
# would also tell somebody working through the code space which of their
# guesses hit a real row - which is the only information a guesser is missing.
# The operator can see the real reason in `tools/beta_codes.py`; the caller
# gets one answer.
INVALID_CODE_MESSAGE = (
    "That access code isn't valid. Check it for typos, or ask whoever "
    "invited you for a new one."
)


def generate_code() -> str:
    """A new code in `ZG-BETA-XXXX-XXXX` form.

    `secrets.choice`, not `random`: this is a credential, and the whole
    security of the scheme against an online guesser is that the 40 bits are
    actually unpredictable.

    Rejection-free by construction - the alphabet is exactly 32 characters, so
    every draw is uniform and there is no modulo bias to correct.
    """
    body = "".join(secrets.choice(ALPHABET) for _ in range(SECRET_LEN))
    groups = [body[i:i + GROUP_LEN] for i in range(0, SECRET_LEN, GROUP_LEN)]
    return "-".join([CODE_PREFIX] + groups)


def canonical(raw: str) -> Optional[str]:
    """
    The one spelling of a code that gets hashed, or None if it is not one.

    Uppercases, drops everything that is not alphanumeric, applies Crockford's
    confusable mapping, then insists on the exact shape. Returning None for
    anything malformed means a caller cannot accidentally hash a partial code
    and get a lookup miss that looks like a wrong code - the two are different
    and only this function can tell them apart.

    Validated against `INPUT_ALPHABET`, not `ALPHABET`, so a hand-chosen vanity
    code may contain a `U`. See the note beside that constant.
    """
    if not raw:
        return None
    cleaned = _STRIP_RE.sub("", raw).upper()
    cleaned = "".join(_CONFUSABLE.get(ch, ch) for ch in cleaned)
    prefix = _STRIP_RE.sub("", CODE_PREFIX).upper()
    if not cleaned.startswith(prefix):
        return None
    body = cleaned[len(prefix):]
    if len(body) != SECRET_LEN:
        return None
    if any(ch not in INPUT_ALPHABET for ch in body):
        return None
    groups = [body[i:i + GROUP_LEN] for i in range(0, SECRET_LEN, GROUP_LEN)]
    return "-".join([CODE_PREFIX] + groups)


def label_for(code: str) -> str:
    """The identifying, non-redeemable fragment stored alongside the hash.

    The first secret group only. Twenty of the forty bits, which is enough to
    name one code among a few hundred in a listing and nowhere near enough to
    redeem anything.
    """
    return code[:len(CODE_PREFIX) + 1 + GROUP_LEN] + "-" + "?" * GROUP_LEN


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

_pepper_cache: Optional[bytes] = None


def _pepper() -> bytes:
    """
    The key the code hash is taken under.

    Read lazily rather than at import so that a process which never touches a
    beta code - a pure unit test, a syntax check - does not have to have it
    configured. Cached, because `has_access` is on the hot path.
    """
    global _pepper_cache
    if _pepper_cache is not None:
        return _pepper_cache
    raw = os.environ.get("BETA_CODE_PEPPER") or os.environ.get("SESSION_COOKIE_SECRET")
    if not raw:
        raise BetaError(
            "Beta codes cannot be hashed: set BETA_CODE_PEPPER (or "
            "SESSION_COOKIE_SECRET) and keep it stable. A random per-process "
            "key would mean codes minted by the admin tool never validate.",
            status_code=500,
        )
    _pepper_cache = raw.encode("utf-8")
    return _pepper_cache


def hash_code(code: str) -> str:
    """HMAC-SHA256 of a canonical code under the pepper, as hex."""
    return hmac.new(_pepper(), code.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Is the gate on at all
# ---------------------------------------------------------------------------

def beta_required() -> bool:
    """
    Whether access is closed. **Defaults to closed.**

    The default is the security-relevant half of this function. A gate whose
    default is "off" is one an unset variable silently opens - on a new
    deployment, in a container that lost its environment, in a test that
    forgot to set it - and every one of those is a production incident that
    looks like nothing at all from the inside. So the app is private unless
    somebody explicitly says otherwise, and re-opening it is a deliberate act
    with a name: `BETA_ACCESS_REQUIRED=false`.
    """
    return os.environ.get("BETA_ACCESS_REQUIRED", "true").strip().lower() != "false"


# ---------------------------------------------------------------------------
# The authorization read
# ---------------------------------------------------------------------------

# Short. See the module docstring: the cache exists to keep a per-request query
# off a shared pool, and correctness after a redemption comes from explicit
# invalidation rather than from expiry.
CACHE_TTL_SECONDS = float(os.environ.get("BETA_CACHE_TTL", "30"))

# An unbounded dict keyed on something a caller influences (an identity they
# can mint by clearing a cookie) is a slow memory leak, the same reasoning as
# `identity.MAX_REVOKED_GUESTS`.
MAX_CACHE_ENTRIES = 20000

_cache: dict = {}
_cache_lock = threading.Lock()


def _cache_get(identity: str):
    with _cache_lock:
        entry = _cache.get(identity)
        if entry is None:
            return None
        allowed, expires = entry
        if expires < time.monotonic():
            _cache.pop(identity, None)
            return None
        return allowed


def _cache_put(identity: str, allowed: bool) -> None:
    with _cache_lock:
        if len(_cache) >= MAX_CACHE_ENTRIES:
            _cache.clear()
        _cache[identity] = (allowed, time.monotonic() + CACHE_TTL_SECONDS)


def invalidate(identity: str = None) -> None:
    """Forget a cached answer, or all of them.

    Called at the two moments an answer changes - a redemption and a transfer
    onto an account - so access begins working on the very next request rather
    than whenever the TTL happens to lapse.
    """
    with _cache_lock:
        if identity is None:
            _cache.clear()
        else:
            _cache.pop(identity, None)


def cached_access(identity: str):
    """
    The answer already in memory, or None if there is not one yet.

    Exists so `beta_gate` can settle the overwhelmingly common case - a tester
    who is already in, asking for the next thing - without touching Postgres
    and without leaving the event loop. `has_access()` is the same decision
    with the database fallback attached, and it BLOCKS: it opens a pooled
    connection and waits on a network round trip to Neon.

    That distinction is the whole reason this function exists. The gate runs on
    every single API request, and a blocking call inside an async middleware
    stalls the event loop for its whole duration - so on a cold cache, one
    visitor's round trip to Neon would be paid by every other request in
    flight. Splitting it lets the gate answer from memory synchronously and
    hand only the misses to a worker thread.
    """
    if not identity:
        return False
    if not beta_required():
        return True
    return _cache_get(identity)


def has_access(identity: str) -> bool:
    """
    Whether this identity may use the application. The whole gate, in one call.

    An account is authorized by `users.beta_access`; a guest by owning a row in
    `beta_redemptions`. Nothing else is consulted, and nothing the caller sent
    is an input - `identity` comes from `identity.py`, which resolves it from a
    server-signed cookie.

    **Fails closed.** A database that is missing or unreachable means no
    answer, and the answer we do not have is refused rather than assumed. That
    is a deliberate departure from how the rest of this app degrades (§13: "no
    database is not fatal", play carries on): everywhere else the fallback
    costs a feature, and here it would cost the entire access control. An
    outage locking testers out for a minute is recoverable; an outage opening
    the app to the public is not.
    """
    if not identity:
        return False
    if not beta_required():
        return True

    cached = _cache_get(identity)
    if cached is not None:
        return cached

    if not db.configured():
        logger.error(
            "❌ Beta access is required but DATABASE_URL is not set. Nobody can "
            "be authorized. Set DATABASE_URL, or BETA_ACCESS_REQUIRED=false to "
            "re-open the app."
        )
        return False

    try:
        with db.connection() as conn:
            if identity.startswith("user:"):
                account_id = identity[len("user:"):]
                row = conn.execute(
                    "SELECT beta_access FROM users WHERE id = %s", (account_id,)
                ).fetchone()
                allowed = bool(row and row[0])
            else:
                row = conn.execute(
                    "SELECT 1 FROM beta_redemptions WHERE identity = %s", (identity,)
                ).fetchone()
                allowed = row is not None
    except Exception as e:
        # Not cached. A transient failure must not pin a "no" in front of a
        # legitimate tester for the whole TTL.
        logger.warning(f"⚠️ Beta access check failed, refusing: {type(e).__name__}: {e}")
        return False

    _cache_put(identity, allowed)
    return allowed


def account_has_access(user_id) -> bool:
    """`users.beta_access` for one account. Used by the admin tool."""
    with db.connection() as conn:
        row = conn.execute("SELECT beta_access FROM users WHERE id = %s", (user_id,)).fetchone()
    return bool(row and row[0])


# ---------------------------------------------------------------------------
# Redemption
# ---------------------------------------------------------------------------

def redeem(raw_code: str, identity: str, ip: str = None) -> dict:
    """
    Spend a code and grant this identity access. Raises `BetaError` otherwise.

    The properties that make this safe, and how each is obtained:

    * **One transaction.** The row is locked, the counter is incremented and
      the grant is written together, so a crash between them is not a state.
    * **Race-safe.** `SELECT ... FOR UPDATE` serialises two simultaneous
      redemptions of one code, and the increment is additionally guarded by
      `uses < max_uses` in the UPDATE's own WHERE clause - so even without the
      lock the second one moves no rows and is refused.
    * **Idempotent per identity.** An identity that already holds a grant is
      told so and spends nothing. A double-clicked Continue button must not
      burn two codes.
    * **One refusal.** Missing, disabled, expired and exhausted are one
      message, so the endpoint cannot be used to probe which codes are real.
    * **Never trusts a caller.** `identity` is resolved from the signed cookie
      by the middleware; there is no parameter here that names a beneficiary.
    """
    code = canonical(raw_code)
    if code is None:
        # Deliberately the SAME message as a code that does not exist. A
        # distinct "malformed" reply would confirm the format to somebody who
        # was guessing at it, and the format is the cheap half to guess.
        raise BetaError(INVALID_CODE_MESSAGE)

    if not db.configured():
        raise BetaError(
            "Access codes cannot be checked right now. Try again in a minute.",
            status_code=503,
        )

    code_hash = hash_code(code)
    now = time.time()

    with db.connection() as conn:
        with conn.transaction():
            # An identity that already has a grant spends nothing. Checked
            # inside the transaction so two concurrent redemptions by one
            # browser cannot both pass it.
            existing = conn.execute(
                "SELECT code_id FROM beta_redemptions WHERE identity = %s FOR UPDATE",
                (identity,),
            ).fetchone()
            if existing is not None:
                logger.info("🎟️ Redemption skipped - identity already has beta access")
                return {"granted": True, "already": True}

            row = conn.execute(
                "SELECT id, enabled, expires_at, max_uses, uses FROM beta_codes"
                " WHERE code_hash = %s FOR UPDATE",
                (code_hash,),
            ).fetchone()
            if row is None:
                logger.info("🎟️ Redemption refused - no such code")
                raise BetaError(INVALID_CODE_MESSAGE)

            code_id, enabled, expires_at, max_uses, uses = row
            if not enabled:
                logger.info(f"🎟️ Redemption refused - code {code_id} is disabled")
                raise BetaError(INVALID_CODE_MESSAGE)
            if expires_at is not None and expires_at <= now:
                logger.info(f"🎟️ Redemption refused - code {code_id} expired")
                raise BetaError(INVALID_CODE_MESSAGE)
            if uses >= max_uses:
                logger.info(f"🎟️ Redemption refused - code {code_id} is used up")
                raise BetaError(INVALID_CODE_MESSAGE)

            # The guard is repeated in the WHERE clause on purpose. The row
            # lock above is what makes this correct under concurrency; this is
            # what makes it correct even if somebody later removes the lock.
            moved = conn.execute(
                "UPDATE beta_codes SET uses = uses + 1"
                " WHERE id = %s AND enabled AND uses < max_uses",
                (code_id,),
            ).rowcount
            if moved != 1:
                logger.info(f"🎟️ Redemption lost the race on code {code_id}")
                raise BetaError(INVALID_CODE_MESSAGE)

            user_id = identity[len("user:"):] if identity.startswith("user:") else None
            conn.execute(
                "INSERT INTO beta_redemptions"
                " (code_id, identity, user_id, redeemed_at, redeemed_ip)"
                " VALUES (%s, %s, %s, %s, %s)",
                (code_id, identity, user_id, now, ip),
            )
            if user_id is not None:
                conn.execute(
                    "UPDATE users SET beta_access = true WHERE id = %s", (user_id,)
                )

    invalidate(identity)
    logger.info(f"🎟️ Beta code {code_id} redeemed")
    return {"granted": True, "already": False}


def transfer_to_account(guest_identity: str, user_id) -> bool:
    """
    Move a guest's grant onto the account they just signed in as.

    Called from signup, sign-in and the Google callback, beside
    `claim_guest_games()` and for the same reason: the person is the same
    person, and the thing they redeemed is theirs. One-way and idempotent, so
    calling it on every sign-in is safe.

    Two outcomes are both successes and the difference matters:

    * the guest had a grant, so it becomes the account's and the guest row is
      gone - a signed-out browser falling back to that identity has nothing.
    * the account already had access, so a grant the guest was holding is
      released and the code it came from gets its slot back. That case is a
      tester who redeemed a second code on a second device and then signed
      into the account they already had: the grant cannot move onto the
      account (which has one) and cannot stay where it is (the guest identity
      is retired moments later by `_end_guest_identity`), so leaving it would
      quietly burn one invitation out of fifty for nothing. Restoring `uses`
      is safe here and only here - the code is going back to the person who
      just proved they hold it, not to whoever might have leaked it, which is
      why `revoke_identity()` deliberately does the opposite.

    Failure is logged and swallowed. Signing in has already succeeded by the
    time this runs; losing the session over a grant that can be re-derived
    would be the worse outcome, exactly as `_claim_guest_history` reasons.
    """
    if not guest_identity or not guest_identity.startswith("guest:"):
        return False
    user_identity = "user:%s" % user_id
    try:
        with db.connection() as conn:
            with conn.transaction():
                already = conn.execute(
                    "SELECT beta_access FROM users WHERE id = %s FOR UPDATE", (user_id,)
                ).fetchone()
                if already is not None and already[0]:
                    row = conn.execute(
                        "DELETE FROM beta_redemptions WHERE identity = %s"
                        " RETURNING code_id",
                        (guest_identity,),
                    ).fetchone()
                    if row is not None:
                        conn.execute(
                            "UPDATE beta_codes SET uses = greatest(uses - 1, 0)"
                            " WHERE id = %s",
                            (row[0],),
                        )
                        logger.info(
                            "🎟️ Account %s already had beta access - released the "
                            "guest's code back" % user_id
                        )
                    invalidate(guest_identity)
                    return True
                moved = conn.execute(
                    "UPDATE beta_redemptions SET identity = %s, user_id = %s"
                    " WHERE identity = %s",
                    (user_identity, user_id, guest_identity),
                ).rowcount
                if moved == 0:
                    return False
                conn.execute("UPDATE users SET beta_access = true WHERE id = %s", (user_id,))
    except Exception as e:
        logger.warning(f"⚠️ Could not transfer beta access to account {user_id}: {e}")
        return False

    invalidate(guest_identity)
    invalidate(user_identity)
    logger.info(f"🎟️ Beta access transferred to account {user_id}")
    return True


# ---------------------------------------------------------------------------
# Administration
#
# Server-side only. There is no HTTP route to any of this and there must not
# be: an endpoint that mints invitations is an endpoint that ends the beta.
# `tools/beta_codes.py` is the interface.
# ---------------------------------------------------------------------------

def create_codes(count: int, created_by: str = None, expires_at: float = None,
                 max_uses: int = 1, notes: str = None) -> list:
    """
    Mint `count` codes and return the plaintext. **The only time they exist.**

    Returned rather than logged, and the caller (`tools/beta_codes.py`) writes
    them to a file or the terminal. Nothing in this process keeps them: the
    row holds a keyed hash and a four-character label, so a code that is not
    written down here is gone.

    A duplicate hash is retried rather than raising. It will not happen - 40
    bits against a few hundred rows - but "will not happen" and "cannot
    happen" differ, and the recovery is one more draw.
    """
    if count < 1:
        raise BetaError("Ask for at least one code.")
    now = time.time()
    minted = []
    with db.connection() as conn:
        for _ in range(count):
            for attempt in range(5):
                code = generate_code()
                try:
                    conn.execute(
                        "INSERT INTO beta_codes"
                        " (code_hash, code_label, created_at, created_by,"
                        "  expires_at, enabled, max_uses, notes)"
                        " VALUES (%s, %s, %s, %s, %s, true, %s, %s)",
                        (hash_code(code), label_for(code), now, created_by,
                         expires_at, max_uses, notes),
                    )
                except Exception:
                    if attempt == 4:
                        raise
                    continue
                minted.append(code)
                break
    logger.info(f"🎟️ Minted {len(minted)} beta code(s)")
    return minted


def create_custom_code(raw_code: str, created_by: str = None, expires_at: float = None,
                       max_uses: int = 1, notes: str = None) -> str:
    """
    Store a code somebody chose rather than one this module drew.

    For the operator's own key: a string they can remember, that does not
    expire, and that admits them on every device they ever sit down at.
    `create_codes()` cannot do that job - it returns randomness, and randomness
    is the one thing a memorable key is not.

    **A chosen code has whatever entropy its author gave it, which is far less
    than the 40 bits of a generated one.** A key made of two English words is
    a far smaller space than eight uniform draws. That is a real reduction and
    it is the caller's to make knowingly - the rate limiter still bounds an
    online guesser hard (10 attempts per IP per 15 minutes, 20 per identity),
    and the mitigation if a chosen key ever leaks is the same as for any other:
    `disable`, then `revoke` whoever redeemed it.

    Everything else is identical to a generated code. Same canonicalisation,
    same keyed hash, same table, same one-refusal-for-every-failure. Nothing
    downstream can tell the two apart, which is the property that keeps this
    from being a second, weaker code path.

    Returns the canonical spelling of what was stored, so the caller can print
    the code exactly as it must be typed.
    """
    code = canonical(raw_code)
    if code is None:
        raise BetaError(
            "That is not a usable code. It must read ZG-BETA-XXXX-XXXX, with "
            "eight characters after the prefix drawn from %s." % INPUT_ALPHABET
        )
    with db.connection() as conn:
        existing = conn.execute(
            "SELECT id FROM beta_codes WHERE code_hash = %s", (hash_code(code),)
        ).fetchone()
        if existing is not None:
            # Named rather than swallowed. Silently reusing the row would mean
            # a second `add` quietly reset nothing and reported success, and the
            # operator would believe they had changed max_uses when they had not.
            raise BetaError("That code already exists (id %s)." % existing[0])
        conn.execute(
            "INSERT INTO beta_codes"
            " (code_hash, code_label, created_at, created_by,"
            "  expires_at, enabled, max_uses, notes)"
            " VALUES (%s, %s, %s, %s, %s, true, %s, %s)",
            (hash_code(code), label_for(code), time.time(), created_by,
             expires_at, max_uses, notes),
        )
    logger.info("🎟️ Stored a chosen beta code (%s)", label_for(code))
    return code


def list_codes(include_spent: bool = True, limit: int = 500) -> list:
    """Every code, as rows a human can read. Never includes anything redeemable."""
    sql = (
        "SELECT c.id, c.code_label, c.created_at, c.created_by, c.expires_at,"
        "       c.enabled, c.max_uses, c.uses, c.notes,"
        "       (SELECT string_agg(r.identity, ', ' ORDER BY r.redeemed_at)"
        "          FROM beta_redemptions r WHERE r.code_id = c.id) AS claimed_by,"
        "       (SELECT min(r.redeemed_at) FROM beta_redemptions r WHERE r.code_id = c.id)"
        "         AS claimed_at"
        "  FROM beta_codes c"
    )
    if not include_spent:
        sql += " WHERE c.enabled AND c.uses < c.max_uses"
    sql += " ORDER BY c.created_at, c.id LIMIT %s"
    with db.connection() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    keys = ("id", "label", "created_at", "created_by", "expires_at", "enabled",
            "max_uses", "uses", "notes", "claimed_by", "claimed_at")
    return [dict(zip(keys, row)) for row in rows]


def _resolve_code_id(conn, selector: str):
    """A code id from either a numeric id or a full code.

    Two ways in because an operator has two things to hand: the id printed by
    `list`, and the code itself as a tester pasted it into a support message.
    A label is deliberately NOT accepted - it is not unique, and disabling the
    wrong invitation is not a mistake worth enabling.
    """
    selector = (selector or "").strip()
    if selector.isdigit():
        return int(selector)
    code = canonical(selector)
    if code is None:
        return None
    row = conn.execute(
        "SELECT id FROM beta_codes WHERE code_hash = %s", (hash_code(code),)
    ).fetchone()
    return row[0] if row else None


def set_enabled(selector: str, enabled: bool) -> bool:
    """Switch a code on or off. Returns whether a row changed.

    Disabling does NOT revoke a grant already made from that code. Those are
    two different intentions and conflating them would mean an operator
    tidying up spent codes silently locked out the testers holding them - see
    `revoke_identity()` for the other one.
    """
    with db.connection() as conn:
        code_id = _resolve_code_id(conn, selector)
        if code_id is None:
            return False
        changed = conn.execute(
            "UPDATE beta_codes SET enabled = %s WHERE id = %s", (enabled, code_id)
        ).rowcount
    return changed == 1


def set_expiry(selector: str, expires_at: Optional[float]) -> bool:
    """Set or clear a code's expiry. `None` means it never expires."""
    with db.connection() as conn:
        code_id = _resolve_code_id(conn, selector)
        if code_id is None:
            return False
        changed = conn.execute(
            "UPDATE beta_codes SET expires_at = %s WHERE id = %s", (expires_at, code_id)
        ).rowcount
    return changed == 1


def revoke_identity(identity: str) -> bool:
    """
    Take access away from one redeemer, without giving the code back.

    The counterpart to `set_enabled(False)`: that one stops a code being
    redeemed again, this one stops a redemption that already happened from
    authorizing anything. Both exist because an invitation that leaked and a
    tester who has to be removed are different problems.

    `uses` is deliberately NOT decremented. The code was spent; handing the
    slot back would let a leaked invitation be re-redeemed by whoever leaked
    it, which is the opposite of what the operator asked for.
    """
    with db.connection() as conn:
        with conn.transaction():
            removed = conn.execute(
                "DELETE FROM beta_redemptions WHERE identity = %s", (identity,)
            ).rowcount
            if identity.startswith("user:"):
                conn.execute(
                    "UPDATE users SET beta_access = false WHERE id = %s",
                    (identity[len("user:"):],),
                )
    invalidate(identity)
    return removed > 0


def usage() -> dict:
    """Counts for the admin tool's summary line."""
    with db.connection() as conn:
        codes = conn.execute(
            "SELECT count(*), coalesce(sum(uses), 0),"
            "       count(*) FILTER (WHERE enabled AND uses < max_uses)"
            "  FROM beta_codes"
        ).fetchone()
        testers = conn.execute(
            "SELECT count(*) FILTER (WHERE identity LIKE 'user:%%'),"
            "       count(*) FILTER (WHERE identity LIKE 'guest:%%')"
            "  FROM beta_redemptions"
        ).fetchone()
    return {
        "codes": codes[0],
        "redemptions": codes[1],
        "available": codes[2],
        "accounts": testers[0],
        "guests": testers[1],
    }

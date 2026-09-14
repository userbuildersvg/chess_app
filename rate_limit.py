"""
Per-IP rate limiting for the endpoints that cost real money or real CPU.

The public Render deployment hands out one URL to a handful of players, but
anyone who finds that URL can script requests against it. The Gemini API key
never leaves the server (see gemini_chat_service.py), so the risk isn't key
theft - it's key *usage*: every /api/move and /api/chat call spends quota
billed to the server's key, not the caller's.

These limits are deliberately generous for a human playing chess (a real
game produces a move every few seconds at most) and hostile to anything
automated. /api/status is intentionally NOT limited - the frontend polls it
about once a second and it costs nothing.

Hand-rolled rather than pulling in slowapi: it's a fixed-window counter in a
dict, the process is single-instance on Render's free plan, and the rest of
this project writes its own small services the same way.
"""

import os
import time
import logging
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


# --- The one switch, and why it is shaped the way it is ---------------------
#
# `dependency.limiter` below is a seam for the TestClient suites: they hold the
# limiter object directly, so they can fill a bucket, prove it refuses, and
# clear it. That does not help a tool driving a REAL browser against a separate
# uvicorn process - `tools/verify/*` cannot reach into the server's memory - and
# the QA sweep for the beta-hardening sprint duly took four 429s from the PGN
# import bucket for no reason except that it was doing its job quickly. Reading
# those as failures, or sleeping nine seconds per case to avoid them, are both
# worse than saying "limits off" out loud.
#
# So it is an environment variable, and it copies the shape of
# `BETA_ACCESS_REQUIRED` (section 26) because that shape has already been
# argued for here:
#
#   * it FAILS SAFE - anything other than the literal string "false" leaves the
#     limits on, so a typo, an empty value or an unset variable all mean
#     "enforced". The dangerous direction needs to be the one that is hard to
#     reach by accident.
#   * it is LOUD - turning limits off logs a warning at import, so a deployment
#     that shipped with this set cannot do so quietly. The failure being guarded
#     against is not an attacker; it is a future session copying a dev command
#     into render.yaml.
#
# It must never be set in production. What it protects is a Gemini key that
# bills the server, and `rate_limit.py` was itself once nearly lost in a merge
# (see the note at the end of section 7) - this file has a history of being the
# thing that quietly goes missing.
RATE_LIMITS_ENABLED = os.environ.get("RATE_LIMITS_ENABLED", "true").strip().lower() != "false"

if not RATE_LIMITS_ENABLED:
    logger.warning(
        "🚦 RATE LIMITING IS OFF (RATE_LIMITS_ENABLED=false). Every endpoint "
        "that spends Gemini quota or Stockfish time is unthrottled. This is for "
        "local verification only - never set it in production."
    )


class RateLimiter:
    """Sliding-window request counter, keyed by client IP."""

    def __init__(self, max_requests: int, window_seconds: int, name: str):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.name = name
        self._hits = defaultdict(deque)
        self._lock = Lock()

    def check(self, client_ip: str) -> None:
        """Record a hit for client_ip, or raise HTTP 429 if it's over budget.

        The combined form, and what almost every caller wants: one call per
        request, where making the request is itself the thing being counted.
        `refuse_if_full` and `record` are the two halves, for the one caller
        that has to count something other than the attempt - see them.
        """
        self._enforce(client_ip, record=True)

    def refuse_if_full(self, key: str) -> None:
        """Raise HTTP 429 if `key` is over budget, WITHOUT spending from it.

        For a bucket that counts outcomes rather than attempts. `login_by_username`
        counts failed sign-ins: a correct password must cost nothing, or
        ordinary use would lock an account out of itself. Pair with `record`.
        """
        self._enforce(key, record=False)

    def record(self, key: str) -> None:
        """Spend one from `key`'s budget, refusing nothing.

        The other half of `refuse_if_full`. Never raises: by the time a caller
        knows the outcome it wants to count, the work is already done and
        turning that into a 429 would refuse a request that has already had
        its effect.
        """
        now = time.monotonic()
        with self._lock:
            self._hits[key].append(now)
            self._prune(now - self.window_seconds)

    def _enforce(self, client_ip: str, record: bool) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            hits = self._hits[client_ip]
            while hits and hits[0] < cutoff:
                hits.popleft()

            if len(hits) >= self.max_requests:
                retry_after = int(hits[0] + self.window_seconds - now) + 1
                logger.warning(
                    f"🚦 Rate limit hit on '{self.name}' from {client_ip} "
                    f"({self.max_requests}/{self.window_seconds}s)"
                )
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Too many requests - slow down a moment and try again."
                    ),
                    headers={"Retry-After": str(retry_after)},
                )

            if record:
                hits.append(now)
            self._prune(cutoff)

    def _prune(self, cutoff: float) -> None:
        """Drop keys with nothing live in them. Caller holds the lock.

        Without this, one key per visitor accumulates forever. Cheap, because
        it only does work once the dict has grown large.
        """
        if len(self._hits) > 1024:
            stale = [k for k, h in self._hits.items() if not h or h[-1] < cutoff]
            for k in stale:
                del self._hits[k]


    def reset(self) -> None:
        """Forget every recorded hit. For tests only."""
        with self._lock:
            self._hits.clear()


# How many proxies sit in front of this process and APPEND to X-Forwarded-For.
#
# **The default is 0, which means "trust no part of that header".** That is
# fail-closed on purpose, and it is a correction: this defaulted to 1, and an
# independent audit reproduced a full bypass of every IP-keyed limit through
# the dev proxy because of it. See `client_ip` for the mechanism.
#
# The number must match the deployment, and getting it wrong is only safe in
# one direction:
#
#   * TOO LOW - the limiter buckets everyone behind the proxy together. Real
#     users may share a bucket. Annoying, never insecure.
#   * TOO HIGH - the limiter reads an entry the caller wrote, and every limit
#     keyed on it is gone. Silent, and total.
#
# ONE HOP MEANS ONE ENTRANCE. The number describes a path, not a process, so
# a deployment that can be reached BOTH through its proxy and around it cannot
# have a single correct value. `docker-compose.yml` is exactly that shape: it
# publishes 8080 to the host alongside nginx on 3000, so with hops=1 the
# nginx path is right (verified - a rotating header is refused) and the direct
# path is wrong (verified - a rotating header is not). That is a local
# convenience, not what ships: Render exposes one port with its edge always in
# front. If this all-in-one image is ever deployed somewhere that exposes the
# backend port directly, stop publishing it or set hops to 0.
#
# So the default is the harmless direction and a deployment opts in.
# `render.yaml` sets 1, which is right for Render: their edge is the only hop
# that appends, because Dockerfile.backend runs uvicorn directly with no nginx
# of its own.
#
# > Verify this after a deploy rather than assuming it. The check is the one
# > in `test_security.py`: send N+1 failed logins with a FIXED X-Forwarded-For
# > and confirm a 429 arrives, then repeat with a DIFFERENT value each time and
# > confirm the 429 still arrives. If the second run never gets one, this
# > number is too high for that environment.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))


def client_ip(request: Request) -> str:
    """
    The caller's real IP - the rightmost entry we did not let them write.

    THIS USED TO READ THE LEFTMOST ENTRY, AND THAT DEFEATED EVERY LIMIT IN
    THIS FILE. `X-Forwarded-For` is built by appending: each proxy adds the
    address it received the request from, so the list reads
    `client, proxy1, proxy2`. Anything to the LEFT of the first hop we control
    was written by the caller. A client that sends its own header simply has
    its value prepended, so

        curl -H 'X-Forwarded-For: 1.2.3.4' …

    arrived here as `1.2.3.4` - a different bucket key on every request, which
    means unlimited /api/move (Gemini quota, billed to us), unlimited
    /api/beta/redeem (code guessing), unlimited /api/auth/login (password
    guessing). The buckets were all correct and none of them applied.

    So we count back from the RIGHT instead. The rightmost entry was written
    by the proxy nearest us, which the caller cannot influence; stepping left
    by `TRUSTED_PROXY_HOPS - 1` more lands on the address our outermost
    trusted proxy saw the request come from. Anything further left is the
    caller's own text and is ignored.

    A CHAIN SHORTER THAN THE PROMISED HOPS IS NOT TRUSTED AT ALL. This is the
    half that was wrong, and it was wrong in the direction that costs
    everything. The previous version clamped the index at 0, so a chain with
    fewer entries than `TRUSTED_PROXY_HOPS` fell back to the LEFTMOST entry -
    which is the caller's own text. An audit reproduced it end to end through
    `localhost:3001`: twelve failed logins with a fixed header gave
    `401 x10, 429 x2`, and the same twelve with a different header each time
    gave `401 x12`. Unlimited password guessing, from the outside, against a
    limiter that was working perfectly.

    The cause was that the clamp was written as an IndexError guard, and
    "return something rather than raise" quietly meant "return the attacker's
    value rather than raise". Vite's dev proxy forwards `X-Forwarded-For`
    without appending a hop of its own, so on that path the chain is one entry
    long and every byte of it came from the caller.

    So a short chain now falls back to the socket peer, which is the one
    address in the request that nobody outside the machine can write.

    Falls back to the socket peer whenever the header is absent, unusable, or
    not trusted - local dev, the Docker stack, and any deployment that has not
    declared its proxies.
    """
    peer = request.client.host if request.client else "unknown"
    if TRUSTED_PROXY_HOPS <= 0:
        return peer

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    chain = [part.strip() for part in forwarded.split(",") if part.strip()]

    # Fewer entries than the proxies that were supposed to have appended them
    # means the header is not the one this deployment was described as having.
    # Every entry in it is therefore unaccounted for, so none of it is used.
    if len(chain) < TRUSTED_PROXY_HOPS:
        logger.warning(
            f"🚦 X-Forwarded-For has {len(chain)} entr(y/ies) but "
            f"TRUSTED_PROXY_HOPS={TRUSTED_PROXY_HOPS}; ignoring the header and "
            f"limiting on the socket peer. Check TRUSTED_PROXY_HOPS for this "
            f"environment."
        )
        return peer

    return chain[len(chain) - TRUSTED_PROXY_HOPS]


def rate_limit(max_requests: int, window_seconds: int, name: str):
    """
    Build a FastAPI dependency enforcing one limit bucket.

    Used via `dependencies=[Depends(...)]` on the route rather than as a
    parameter, so the existing endpoint signatures (several of which already
    have a body parameter named `request`) don't have to change.
    """
    limiter = RateLimiter(max_requests, window_seconds, name)

    async def dependency(request: Request) -> None:
        # Checked per request rather than by skipping the dependency at build
        # time, so the flag can be read once and the route table still looks
        # identical either way - a route that is limited in production and
        # absent from the dependency list locally is a difference waiting to
        # hide a bug.
        if not RATE_LIMITS_ENABLED:
            return
        limiter.check(client_ip(request))

    # Exposed so a test can exercise this bucket deliberately - fill it, prove
    # it refuses, then clear it - rather than tripping over it while testing
    # something else and reading the 429 as a bug in the thing under test.
    # Nothing in the app touches it.
    dependency.limiter = limiter
    return dependency


# Playing a move triggers the Stockfish-candidates + Gemini-choice round trip,
# so this is the main spend path. 30/min is far more than a human game needs
# and still caps a runaway script at roughly one game's worth per minute.
limit_move = rate_limit(30, 60, "move")

# Chat is a direct Gemini call with the whole transcript attached, so it's the
# most expensive request per hit and the easiest to abuse for free LLM access.
limit_chat = rate_limit(10, 60, "chat")

# No Gemini spend, but a full-game regrade is a burst of blocking Stockfish
# searches - cheap to trigger, expensive to serve.
limit_regrade = rate_limit(6, 60, "regrade")


# --- Learner Mode -----------------------------------------------------------
#
# The sandbox did not exist when the limits above were written, and it is now
# the most expensive surface in the app. Every one of these endpoints spends
# either Gemini quota, blocking Stockfish time, or both - and the sandbox is
# reachable without playing a game at all, so nothing else throttles it.
#
# Deliberately tighter than the real game's. A demonstration is watched, not
# played: a student clicks "AI move", reads the coaching, then clicks again.
# Nobody legitimately drives these faster than this.

# Scenario generation is a Gemini call plus up to six Stockfish evaluations
# ("make it winnable" builds and scores several candidate positions), and each
# one produces a brand-new session against a store capped at 50. The tightest
# limit here for that reason.
limit_scenario = rate_limit(6, 60, "sandbox-scenario")

# One sandbox half-move: the same Stockfish-candidates + Gemini-choice round
# trip as a real move, plus a detached narration call. So it costs more per
# hit than /api/move while being easier to spam - the board is not waiting on
# a human to think.
limit_sandbox_move = rate_limit(20, 60, "sandbox-move")

# The coach chat: a Gemini call with the whole transcript attached AND a full
# rank plus depth-15 refine to give the model the engine's ordering. The
# single most expensive request in the application.
limit_sandbox_chat = rate_limit(10, 60, "sandbox-chat")

# No Gemini spend, but a full rank plus a depth-15 refine (~1.0-1.3s) that
# takes the shared engine lock - so abusing it stalls move selection for
# everyone, including the real game.
limit_alternatives = rate_limit(20, 60, "sandbox-alternatives")

# The eval bar refetches on every position change while it is switched on, so
# it needs the same headroom as move play rather than the tighter analysis
# budget - a demonstration auto-playing a long line would otherwise trip a
# limit just by being watched with the bar open.
limit_eval = rate_limit(40, 60, "sandbox-eval")


# --- Post-Mortem ------------------------------------------------------------
#
# A third surface with its own cost shape. Unlike the game and the sandbox,
# the expensive thing here is not per-move: it is the whole-game scan, which
# is one Stockfish search per position - ~80 of them for a normal game, on the
# engine the live game is using.

# Importing is cheap in itself (a parse and a replay, no engine, no Gemini) but
# every accepted import creates a review holding a whole tree, against a store
# capped at 20. This limits how fast that store can be churned.
limit_postmortem_import = rate_limit(10, 60, "postmortem-import")

# Starting a scan. The tightest limit in this section by a distance, because
# one hit is a minute of engine time and the endpoint is idempotent - a second
# request for a game already being scanned costs nothing and returns the same
# progress, so nobody legitimately needs to send many.
limit_postmortem_analysis = rate_limit(6, 60, "postmortem-analysis")

# Branching, the AI's reply inside a branch, and the full-depth analysis of one
# position. Each is a real search plus, for the reply, a Gemini call - the same
# round trip as a real move, so the same budget as the sandbox's.
limit_postmortem_move = rate_limit(20, 60, "postmortem-move")

# The review coach: a Gemini call with the whole transcript attached AND a full
# rank plus depth-15 refine, exactly like the sandbox coach it mirrors.
limit_postmortem_chat = rate_limit(10, 60, "postmortem-chat")


# Accounts. Deliberately the tightest buckets in the file: unlike a move or a
# chat, a sign-in attempt is something an attacker wants to make thousands of
# in a row, and PBKDF2 at 600k iterations means every one of those costs the
# server real CPU. Signup is tighter still - a legitimate person creates an
# account approximately once, so anything above a handful an hour from one IP
# is somebody enumerating names or filling the table.
limit_login = rate_limit(10, 300, "auth-login")

# A SECOND bucket for login, keyed by the USERNAME being guessed rather than by
# the caller. Same shape as `forgot_by_email` and `redeem_by_identity`, and
# added for a reason the audit made concrete: every bucket above is keyed on an
# address, and an address is only as trustworthy as the proxy configuration
# that produced it. `client_ip` is fail-closed now, but "the limiter is correct
# provided one environment variable matches the deployment" is a thin thing to
# rest credential-stuffing defence on, and a distributed attacker defeats a
# per-IP bucket without needing any header at all.
#
# A username is different: it is in the request body, it is the thing the
# attack is FOR, and an attacker working through passwords for one account
# cannot vary it. So this bounds the actual attack - guessing one person's
# password - independently of how many proxies anybody believes are in front.
#
# 20 failed attempts per 15 minutes against any one account.
#
# It counts FAILURES ONLY, and it is checked BEFORE the password is verified
# (see auth_api.login). Both halves matter and neither is free:
#
#   * failures only, so ordinary use never fills it. Somebody who signs in
#     correctly forty times in an hour spends nothing, and a shared account
#     does not throttle itself.
#   * checked first, so a full bucket costs no PBKDF2. At 600,000 iterations a
#     verification is deliberately expensive, and letting an attacker keep
#     buying them after the limit is reached would turn a credential-stuffing
#     defence into a CPU exhaustion vector on a free instance.
#
# **The trade-off, stated rather than glossed:** an attacker who knows a
# username can spend the bucket on purpose and lock that account out of
# password sign-in for up to fifteen minutes. That is a real denial of
# service and it is accepted knowingly, because the alternative is worse in
# both directions - either unlimited guessing, or unlimited PBKDF2. The window
# is short, it is per-account rather than global, and it does not touch an
# existing session, a Google sign-in or password reset, so a locked-out user
# who is already signed in anywhere is unaffected.
#
# If that trade ever stops being acceptable, the standard escape is to let a
# CORRECT password through even when the bucket is full - which removes the
# lockout entirely at the cost of paying PBKDF2 for every attempt. Do not make
# that change without also bounding the CPU some other way.
#
# KNOWN IMPRECISION, measured rather than assumed. The key is the identifier
# as submitted, normalised with .strip().lower() exactly as
# `auth_service.verify_password` normalises it - so `alice`, `ALICE` and
# ` alice ` share one bucket. But sign-in accepts a username OR an email for
# the same account, and those are different strings, so one account has TWO
# buckets and a determined attacker gets 40 attempts per 15 minutes instead of
# 20. Verified by inspecting the live keys: {'alice': 3, 'alice@…': 1}.
#
# Left as it is, deliberately. This is the SECOND limit on this endpoint; the
# per-IP bucket above (10 per 5 minutes) is the primary and is unaffected, and
# 40 online guesses per quarter hour against an eight-character minimum is not
# an attack that goes anywhere. The fix, if it is ever wanted, is to resolve
# the identifier to a canonical account key before bucketing - one indexed
# lookup, before the PBKDF2 - rather than to key on what the caller typed.
login_by_username = RateLimiter(20, 900, "auth-login-username")
limit_signup = rate_limit(5, 3600, "auth-signup")

# Asking for a reset costs an email and a PBKDF2-free database lookup, so the
# spend is somebody else's inbox rather than our CPU. Per IP, which bounds one
# machine hammering the endpoint.
limit_password_forgot = rate_limit(5, 900, "auth-forgot")

# Redeeming one is cheap but guessable in principle, so the attempt rate is
# capped too. 256 bits of token makes brute force hopeless anyway; this is
# about not serving the attempt at all.
limit_password_reset = rate_limit(10, 900, "auth-reset")

# A SECOND bucket for forgot-password, keyed by the target address rather than
# the caller. The per-IP limit above does nothing against a distributed
# caller pointing many machines at one person's inbox, which is the actual
# abuse this endpoint enables - the victim is the mailbox owner, not us. Used
# directly rather than as a dependency, because the key comes from the request
# body and a dependency only sees the request.
forgot_by_email = RateLimiter(3, 900, "auth-forgot-email")


# The learning loop (learning_loop_api.py).
#
# Diagnosis is the expensive one: a Gemini call on its own model chain, and a
# rejected reply costs a second attempt down the chain. It is also something a
# person does deliberately, one decision at a time, so a low ceiling costs a
# real user nothing.
limit_diagnosis = rate_limit(8, 60, "learning-diagnosis")

# Starting a re-test and answering it. No engine and no model - the bank is a
# constant and the check is a set membership - so this is only here to stop
# the endpoint being used as a free write into a player's practice history.
limit_practice = rate_limit(30, 60, "learning-practice")

# The event sink. Generous, because the UI emits one per meaningful step of
# the loop and a player working through a correction will legitimately produce
# a dozen in a minute; bounded, because it is an unauthenticated write.
limit_learning_event = rate_limit(120, 60, "learning-event")


# The closed beta gate (beta_api.py).
#
# A redemption attempt is the one unauthenticated write left in the
# application that grants anything, so these are the tightest buckets in the
# file after signup's.
#
# The code space is 40 bits (beta_service.ALPHABET, eight characters over a
# 32-character alphabet). At ten attempts per fifteen minutes, working through
# even a millionth of it takes tens of thousands of years - so the limit is
# not what makes guessing hopeless, the entropy is. What the limit does is
# stop the endpoint being used as a cheap oracle at all, and stop a thousand
# parallel attempts from taking a row lock each on the way to being refused.
limit_beta_redeem = rate_limit(10, 900, "beta-redeem")

# Admin invite codes (admin_invites.py): 60 bits of code space and a
# signed-in account required, so the same shape as the beta bucket is plenty.
limit_admin_invite = rate_limit(10, 900, "admin-invite")

# A SECOND bucket, keyed by the caller's identity rather than their IP - the
# same shape as `forgot_by_email` and for the same reason. The per-IP limit
# does nothing against a distributed guesser, and unlike an IP, the identity
# is something the attempt cannot do without: the grant is written onto it, so
# a caller cycling identities has to cycle cookies too and abandons every
# guess they have already spent. Used directly rather than as a dependency,
# because the key comes from the resolved identity and a dependency only sees
# the request.
#
# Deliberately more generous than the per-IP bucket. A real tester types one
# code once; a shared office NAT is many testers behind one address, and this
# is the bucket that keeps the second of them from being refused for the
# first's typos.
redeem_by_identity = RateLimiter(20, 900, "beta-redeem-identity")

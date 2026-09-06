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

import time
import logging
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding-window request counter, keyed by client IP."""

    def __init__(self, max_requests: int, window_seconds: int, name: str):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.name = name
        self._hits = defaultdict(deque)
        self._lock = Lock()

    def check(self, client_ip: str) -> None:
        """Record a hit for client_ip, or raise HTTP 429 if it's over budget."""
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

            hits.append(now)

            # Without this, one IP per visitor accumulates forever. Cheap to
            # do here since it only runs when the dict has grown large.
            if len(self._hits) > 1024:
                stale = [ip for ip, h in self._hits.items() if not h or h[-1] < cutoff]
                for ip in stale:
                    del self._hits[ip]


def client_ip(request: Request) -> str:
    """
    The caller's real IP.

    On Render the request arrives through their edge proxy and then through
    this container's own nginx, so request.client.host is a local hop, not
    the player. The leftmost X-Forwarded-For entry is the original client.
    Falls back to the socket peer for local dev, where there's no proxy.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(max_requests: int, window_seconds: int, name: str):
    """
    Build a FastAPI dependency enforcing one limit bucket.

    Used via `dependencies=[Depends(...)]` on the route rather than as a
    parameter, so the existing endpoint signatures (several of which already
    have a body parameter named `request`) don't have to change.
    """
    limiter = RateLimiter(max_requests, window_seconds, name)

    async def dependency(request: Request) -> None:
        limiter.check(client_ip(request))

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
limit_signup = rate_limit(5, 3600, "auth-signup")


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

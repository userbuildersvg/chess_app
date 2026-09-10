"""
A ceiling on how much request body this process will read into memory.

THE GAP
-------
Every size check in this application happens AFTER the body has been read.
`postmortem_state.parse_pgn` refuses a PGN over 512KB, and `profile_api`
refuses more than 50 games' worth - but both of those are looking at a `str`
that FastAPI has already materialised, which means a 500MB POST is 500MB of
resident memory on a 512MB free instance before anybody gets to say no. The
refusal is correct and arrives too late to be a defence.

So the limit moves in front of the parse. This is a pure ASGI middleware
rather than a `BaseHTTPMiddleware` for exactly that reason: `BaseHTTPMiddleware`
hands the request downstream without touching the body, so the only place a
byte can be counted before it is buffered is around the raw `receive` channel.

TWO CHECKS, BECAUSE THERE ARE TWO WAYS TO SEND A BODY
-----------------------------------------------------
1. `Content-Length` is present, which is what every browser sends for a JSON
   or form body. Then the answer is known before a single byte is read, and
   the request is refused having cost nothing.
2. It is not - a chunked upload, or a hand-built client. Then bytes are
   counted as they arrive and the request is cut off at the ceiling. This path
   costs at most `limit` bytes, which is the point.

A caller who lies about `Content-Length` is caught by the second check, so the
first one is an optimisation and not the security boundary.

THE LIMITS THEMSELVES
---------------------
Derived from what each route legitimately accepts rather than picked round:
the numbers already exist, and duplicating them as a guess is how the guard
ends up refusing something the parser would have accepted.
"""

from __future__ import annotations

import logging

from starlette.datastructures import Headers

logger = logging.getLogger(__name__)

KB = 1024
MB = 1024 * KB

# Everything else. A move is `{"move":"e4"}`; the largest ordinary body in the
# app is a chat message. 64KB is roughly a thousand times what any of them
# need, which is the right kind of margin: generous enough that no real
# request is ever near it, small enough that a flood of them is not a memory
# event.
DEFAULT_MAX_BODY_BYTES = 64 * KB

# The two upload paths, each set from the constant its own parser enforces, so
# a body that clears this guard is one the route can actually accept and the
# two limits cannot drift apart.
#
# Imported lazily inside `_build_limits` rather than at module scope: this
# module sits in the middleware stack and importing the whole post-mortem and
# profile layers to learn two integers would make the import graph circular
# the first time either of them wants a header.
def _build_limits() -> dict:
    from postmortem_state import MAX_PGN_BYTES
    from profile_api import MAX_BODY_BYTES as PROFILE_MAX_BODY_BYTES

    return {
        # One game. The slack is the JSON envelope and `source_name` around
        # the PGN text, which the parser's own limit does not count.
        "/api/postmortem/import": MAX_PGN_BYTES + 32 * KB,
        # A library import: up to 50 games in one request, which is already
        # `MAX_PGN_BYTES * MAX_GAMES_PER_REQUEST` in profile_api.
        "/api/profile/games": PROFILE_MAX_BODY_BYTES + 32 * KB,
    }


# Bodies only arrive on these. Listed rather than inferred so that a GET is
# waved through without constructing anything.
BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class BodyLimitMiddleware:
    """Refuse a request body larger than its route is ever going to accept."""

    def __init__(self, app, default_limit: int = DEFAULT_MAX_BODY_BYTES):
        self.app = app
        self.default_limit = default_limit
        self._limits = _build_limits()

    def limit_for(self, path: str) -> int:
        return self._limits.get(path, self.default_limit)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in BODY_METHODS:
            await self.app(scope, receive, send)
            return

        limit = self.limit_for(scope.get("path", ""))
        headers = Headers(scope=scope)

        declared = headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await self._refuse(scope, send, limit, declared=int(declared))
                    return
            except ValueError:
                # A Content-Length that is not a number is not something to
                # reason about. The counting wrapper below still applies.
                pass

        # A flag and a truncated stream, NOT an exception.
        #
        # Raising out of `receive` was the obvious implementation and it does
        # not work: FastAPI wraps the whole body read in
        #
        #     except Exception as e:
        #         raise HTTPException(400, "There was an error parsing the body")
        #
        # so any exception raised here is swallowed and the caller is told
        # their JSON was malformed - a wrong answer to a request that was
        # perfectly well-formed and merely enormous, and one that gives no clue
        # what the real limit was.
        #
        # So instead the stream is simply ENDED at the ceiling: downstream sees
        # a short body, does whatever it does with it, and the response it
        # produces is discarded and replaced with the 413 below. Downstream
        # never gets more than `limit` bytes either way, which is the property
        # that matters.
        read = 0
        exceeded = False
        started = False

        async def counting_receive():
            nonlocal read, exceeded
            if exceeded:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                read += len(message.get("body", b""))
                if read > limit:
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def replacing_send(message):
            nonlocal started
            if not exceeded:
                await send(message)
                return
            # Drop whatever downstream decided to say and send the 413 once.
            if message["type"] == "http.response.start":
                started = True
                await self._refuse(scope, send, limit, declared=read)
            elif message["type"] == "http.response.body" and not started:
                # A body with no start before it should not happen; if it
                # does, say nothing rather than emitting a naked body frame.
                pass

        await self.app(scope, counting_receive, replacing_send)

    async def _refuse(self, scope, send, limit: int, declared: int) -> None:
        logger.warning(
            f"📦 Refused {scope.get('method')} {scope.get('path')}: "
            f"body of {declared} bytes over the {limit}-byte limit"
        )
        body = (
            b'{"detail":"That request is too large. '
            b'Send a smaller file or a shorter message."}'
        )
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                # `SecurityHeadersMiddleware` sits outside this one and
                # would add these anyway. They are repeated because this
                # response is generated without calling downstream at all,
                # and a refusal that depends on another middleware still
                # being above it is a refusal that loses its headers the day
                # somebody reorders the stack.
                (b"x-content-type-options", b"nosniff"),
                (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                (b"referrer-policy", b"no-referrer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

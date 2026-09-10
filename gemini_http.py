"""
One HTTP connection to Gemini, kept open and shared.

The measurement
---------------

Every Gemini service in this app opened its own client per request:

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(url, json=payload)

which is a fresh TCP connect and a full TLS handshake to
`generativelanguage.googleapis.com` on **every move, every chat turn, every
narration, every scenario and every classification** - and then throws the
connection away. Measured against the real endpoint, five calls each way with
an identical payload:

    new client every call    median 1917ms
    pooled client, reused    median 1388ms

**A median of 530ms per call, 28%, spent on setup rather than on thinking.**

This is the cheapest possible kind of latency to remove, because it changes
nothing about what is sent, what comes back, or what is done with it. The
request is byte-identical, the response is byte-identical, and the model does
exactly as much work. The only difference is that the socket was already open.

Why one client for all six callers
----------------------------------

They all talk to one host with one key, so they can share one connection pool -
and pooling only pays when connections are reused, which means the fewer pools
the better. `max_keepalive_connections` is comfortably above the number of
callers that can be in flight at once (a move, a narration and a chat turn can
overlap; §5's chain-lead argument is about model QUOTA, not about sockets).

Keep-alive is what makes this work across moves rather than only within one:
the connection opened for move 1 is still open for move 2, so only the first
call of a session pays a handshake - and `warm()` at startup means even that
one is paid before a player is waiting on it.

The one rule
------------

**Never log a URL from here.** The key used to travel in the query string
(`?key=...`), which is how it reached the logs once already (§7): httpx logs
every request at INFO and `app.py` has to hold the `httpx` logger at WARNING to
prevent it. This module sends the key as the `x-goog-api-key` HEADER instead,
so the URL is safe even if someone turns that logging back on - the header is
not logged by httpx. That is a second lock on the same door, not a replacement
for the first.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Above the number of Gemini callers that can realistically overlap: a move,
# its narration, a chat turn and a scan-time classification. Sized so a burst
# never has to open a connection it will immediately discard.
_MAX_KEEPALIVE = 10
_MAX_CONNECTIONS = 20
# How long an idle socket is kept. Long enough to span a player thinking about
# their move, which is the gap that matters - a 30-second think should not cost
# a handshake on the way back.
_KEEPALIVE_EXPIRY = 90.0

_client: Optional[httpx.AsyncClient] = None


def client() -> httpx.AsyncClient:
    """
    The shared client, created on first use.

    No timeout is set here on purpose: the services have deliberately different
    ones (§5 - move selection cannot wait as long as chat, because chat has no
    Stockfish move ready to play instead), and each passes its own per request.
    """
    global _client
    # `getattr` because the test suites swap `httpx.AsyncClient` for a stand-in,
    # and a stand-in is not obliged to carry every attribute of the real thing.
    # A missing flag means "not closed", which is the right reading of a fake
    # that was just installed.
    if _client is None or getattr(_client, "is_closed", False):
        _client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=_MAX_KEEPALIVE,
                max_connections=_MAX_CONNECTIONS,
                keepalive_expiry=_KEEPALIVE_EXPIRY,
            ),
        )
    return _client


async def post(url: str, payload: dict, api_key: str, timeout: float) -> httpx.Response:
    """
    POST to Gemini on the shared connection.

    `api_key` goes in a header rather than the query string. Functionally the
    same to the API; materially different to this project, which has already
    leaked this key once through a logged URL.
    """
    return await client().post(
        url,
        json=payload,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        timeout=timeout,
    )


async def warm(api_key: str) -> None:
    """
    Open the connection before anybody is waiting on it.

    Called at startup. The pool saves a handshake on every call after the
    first; this is what stops the FIRST move of a session paying for it while a
    player watches. It is best-effort by design - a failure here means the next
    real call opens the connection itself, exactly as it would have anyway, so
    nothing is broken and nothing needs saying beyond a debug line.
    """
    if not api_key:
        return
    try:
        # A HEAD to the models list: it opens the socket and completes the TLS
        # handshake without generating a single token, so it costs no quota.
        await client().head(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": api_key},
            timeout=5.0,
        )
        logger.info("🔌 Gemini connection warmed")
    except Exception as exc:
        logger.debug(f"Gemini connection warm-up skipped: {type(exc).__name__}: {exc}")


def reset() -> None:
    """
    Drop the cached client without closing it.

    For tests. There is one client for the whole process, so a suite that
    swaps `httpx.AsyncClient` for a fake has to clear the cache as well -
    otherwise the client built from the FIRST fake goes on serving every later
    test in the run, which shows up as one suite mysteriously answering with
    another suite's scripted responses.

    Deliberately not async and deliberately not closing: a fake has nothing to
    close, and the real one is closed by `close()` at shutdown.
    """
    global _client
    _client = None


async def close() -> None:
    """
    Shut the pool down with the app, so the event loop can finish.

    Tolerant of a client that is not a real one: the suites that drive the app
    through `TestClient` fire this shutdown hook with their own stand-in
    installed, and a stand-in has nothing to close. Same reasoning as the
    `getattr` in `client()` - the alternative is every fake in the project
    having to grow an `aclose` for a hook it does not care about.
    """
    global _client
    closer = getattr(_client, "aclose", None) if _client is not None else None
    if closer is not None and not getattr(_client, "is_closed", False):
        try:
            await closer()
        except Exception as exc:  # pragma: no cover - shutdown must not raise
            logger.debug(f"Gemini pool close skipped: {type(exc).__name__}: {exc}")
    _client = None

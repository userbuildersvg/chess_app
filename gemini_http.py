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
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ===========================================================================
# Per-model cooldown
# ===========================================================================
#
# One user action used to be able to cost six or seven provider calls. The
# chains are ordered lists and every model in them is tried on any failure,
# so a provider having a bad minute - 429 RESOURCE_EXHAUSTED on the popular
# models, 503 UNAVAILABLE under load - meant the whole list was walked, at
# one request each, for a single move or a single question. The answer is
# not a shorter list (a list is what recovery is made of) but a MEMORY: a
# model that has just said "not now" is not asked again for a while.
#
# It lives here, in the one place every service already calls, so all six
# chains get it without each growing its own copy - and because the model
# name is in the URL, nothing about the call sites has to change to make it
# work.
#
# Three rules:
#
#   - 429 cools that model for what it ASKED for (Retry-After, or the
#     retryDelay the JSON body carries), and for five minutes when it names
#     no figure. A quota window is minutes long; retrying inside it is a
#     request that cannot succeed.
#   - 503 / overloaded cools it for a minute. That one is a queue, not a
#     wall, and it clears on its own.
#   - Any success clears it. A model that answered is well.
#
# It is PER MODEL and never global: cooling gemini-3.5-flash must leave the
# rest of the chain eligible, or one busy model becomes an outage.

_DEFAULT_429_COOLDOWN = float(os.environ.get("GEMINI_COOLDOWN_429", "300"))
_DEFAULT_503_COOLDOWN = float(os.environ.get("GEMINI_COOLDOWN_503", "60"))
# A model may not be parked for longer than this however large a retryDelay
# it sends, so a single bad answer cannot take a model out of the chain for
# the rest of the process.
_MAX_COOLDOWN = float(os.environ.get("GEMINI_COOLDOWN_MAX", "900"))

# model -> monotonic time it becomes eligible again.
_cooldowns: dict[str, float] = {}


class ModelCooling(Exception):
    """
    Raised instead of calling a model that has just refused.

    An exception rather than a sentinel because that is what every caller
    already handles: the chains treat any raised error as "this model did not
    work, try the next one", which is exactly the right reading.
    """

    def __init__(self, model: str, seconds_left: float):
        super().__init__(f"{model} is cooling for another {seconds_left:.0f}s")
        self.model = model
        self.seconds_left = seconds_left


def model_of(url: str) -> str:
    """The model a Gemini URL addresses. '' when the shape is unfamiliar."""
    if "/models/" not in url:
        return ""
    tail = url.split("/models/", 1)[1]
    # ".../models/<model>:generateContent?..." - the name ends at the verb.
    return tail.split(":", 1)[0].split("?", 1)[0].strip()


def cooling_for(model: str) -> float:
    """Seconds until `model` may be called again; 0 when it is eligible."""
    until = _cooldowns.get(model)
    if until is None:
        return 0.0
    left = until - time.monotonic()
    if left <= 0:
        _cooldowns.pop(model, None)
        return 0.0
    return left


def cool(model: str, seconds: float, *, reason: str, feature: str = "") -> None:
    """Park one model. Never logs anything but metadata."""
    if not model or seconds <= 0:
        return
    seconds = min(seconds, _MAX_COOLDOWN)
    _cooldowns[model] = time.monotonic() + seconds
    logger.warning(
        f"🧊 Gemini cooldown: model={model} feature={feature or 'unknown'} "
        f"reason={reason} for={seconds:.0f}s"
    )


def clear(model: str) -> None:
    """A model that answered is well again."""
    if model in _cooldowns:
        _cooldowns.pop(model, None)
        logger.info(f"🔥 Gemini cooldown cleared: model={model}")


def eligible(models: list, limit: int) -> list:
    """
    The models worth trying for ONE user action, in order, at most `limit`.

    Two jobs in one call, because they are the same question. Cooled models
    are dropped - asking them is a request that cannot succeed - and what is
    left is capped, because a logical request that walks seven models is
    seven times the quota and seven times the wait for one answer. The
    caller's own deterministic fallback is what the cap falls through to,
    and every service already has one.

    An empty list is a real answer: every model is cooling, so the honest
    move is the fallback now rather than a round of calls that will fail.
    """
    live = [m for m in models if cooling_for(m) <= 0]
    if not live and models:
        logger.warning(
            f"🧊 Gemini: all {len(models)} models in this chain are cooling - "
            "using the deterministic fallback"
        )
    return live[:max(1, limit)] if live else []


def _retry_delay_from(response: "httpx.Response") -> Optional[float]:
    """
    What the provider asked us to wait, if it asked.

    Two places carry it: the Retry-After header, and Google's own error body
    (`details[].retryDelay`, e.g. "41s"). The body is only parsed for that
    one field - nothing else from it is read, logged or kept.
    """
    header = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    try:
        body = response.json()
    except Exception:
        return None
    details = (body.get("error") or {}).get("details") or []
    for entry in details if isinstance(details, list) else []:
        delay = entry.get("retryDelay") if isinstance(entry, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                continue
    return None


def note(response: "httpx.Response", *, feature: str = "") -> None:
    """
    Record what one response says about the health of its model.

    Called for every response the shared `post()` returns, so a service gets
    this without doing anything - and a service that inspects the status
    itself still sees exactly what it saw before.
    """
    model = model_of(str(response.request.url)) if response.request is not None else ""
    if not model:
        return
    status = response.status_code
    if status == 429:
        asked = _retry_delay_from(response)
        cool(model, asked or _DEFAULT_429_COOLDOWN, reason="429", feature=feature)
    elif status in (500, 502, 503, 504):
        cool(model, _DEFAULT_503_COOLDOWN, reason=str(status), feature=feature)
    elif 200 <= status < 300:
        clear(model)


def reset_cooldowns() -> None:
    """For the suites, which must not inherit another test's parked models."""
    _cooldowns.clear()


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


async def post(
    url: str,
    payload: dict,
    api_key: str,
    timeout: float,
    *,
    feature: str = "",
) -> httpx.Response:
    """
    POST to Gemini on the shared connection.

    `api_key` goes in a header rather than the query string. Functionally the
    same to the API; materially different to this project, which has already
    leaked this key once through a logged URL.

    Two things happen around the call that every caller wants and none of
    them should have to write. A model that is cooling is not called at all
    (`ModelCooling` is raised, which the chains already read as "try the next
    one"), and whatever comes back is recorded against that model's health -
    so a 429 here is what stops the NEXT request paying for the same refusal.

    `feature` is for the log line only: which service was asking. It is never
    sent to the provider, and nothing about the payload - prompt, FEN, PGN or
    user text - is logged anywhere in this module.
    """
    model = model_of(url)
    left = cooling_for(model)
    if left > 0:
        raise ModelCooling(model, left)
    response = await client().post(
        url,
        json=payload,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        timeout=timeout,
    )
    note(response, feature=feature)
    return response


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

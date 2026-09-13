"""
Recent public games from Chess.com and Lichess, by username.

WHAT THIS IS AND IS NOT
-----------------------
Public games only. It asks each site for the games a username has played,
through the endpoints both publish for exactly that, and it never holds a
password, a token or an OAuth grant for either. The username the person typed
is the whole of what leaves this server, and it goes to one of two fixed
hosts - there is no URL in the request body, and nothing here fetches an
address a caller composed.

Two sites, two shapes:

  * Chess.com: an archive list, then the newest few MONTHLY archives as JSON
    (`.../games/{YYYY}/{MM}`). The JSON carries the PGN and, beside it, the
    fields the PGN does not - `rated`, `rules`, `time_class`, `end_time`. The
    `/pgn` sibling of that endpoint is the same archive with those stripped
    off, so the JSON is used.
  * Lichess: one PGN stream (`/api/games/user/{username}`), newest first,
    already capped by `max`.

Everything comes back as one shape (`ExternalGame` dicts) so the API and the
UI have one thing to render, and every failure maps to a short category the
frontend can say in a sentence. Provider exception strings, URLs and status
codes stop here.

LIMITS, ALL OF THEM DELIBERATE
------------------------------
`max_games` capped at 50, Chess.com at 3 months, one request 8 seconds, one
response body 8 MB. A username with a decade of blitz behind it would
otherwise turn "import recent games" into a bulk crawl, on a free instance,
on somebody else's rate limit.
"""

from __future__ import annotations

import io
import logging
import re
import time
from datetime import datetime, timezone

import chess.pgn
import httpx

logger = logging.getLogger(__name__)

SOURCES = ("chesscom", "lichess")
SOURCE_LABELS = {"chesscom": "Chess.com", "lichess": "Lichess"}

MAX_GAMES_DEFAULT = 20
MAX_GAMES_CAP = 50
CHESSCOM_MAX_MONTHS = 3
TIMEOUT_SECONDS = 8.0
# A heavy blitz player's month on Chess.com is a few MB of JSON; this is
# a ceiling against a runaway body, not a budget.
MAX_RESPONSE_BYTES = 8_000_000
CACHE_TTL_SECONDS = 60
CACHE_MAX_ENTRIES = 64

USER_AGENT = "Zugzwang/4.5 (chess coaching app; public game import)"

CHESSCOM_BASE = "https://api.chess.com/pub/player"
LICHESS_BASE = "https://lichess.org/api/games/user"

# Chess.com: letters, digits, underscore, hyphen. Lichess: the same, 2-30. Both
# are case-insensitive on their side. The pattern is the whole of what may
# reach a URL, which is what keeps `username` from ever being a path.
_USERNAME_RULES = {
    "chesscom": re.compile(r"^[A-Za-z0-9_-]{1,50}$"),
    "lichess": re.compile(r"^[A-Za-z0-9_-]{2,30}$"),
}
USERNAME_MAX_CHARS = 50


class ExternalError(Exception):
    """A failure the person can be told about in one sentence.

    `category` is the stable token (`username_not_found`, `no_games`,
    `provider_unavailable`, `rate_limited`, `invalid_username`,
    `invalid_source`, `too_many`, `unsupported_variant`); `message` is the
    sentence. Nothing from the provider's own error text is in either.
    """

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


def _label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_source(source) -> str:
    source = (source or "").strip().lower()
    if source not in SOURCES:
        raise ExternalError("invalid_source", "Choose Chess.com or Lichess.")
    return source


def validate_username(source: str, username) -> str:
    """The username as it may appear in a URL, or an ExternalError."""
    source = validate_source(source)
    text = (username or "").strip()
    # A pasted profile URL is a common thing to type into a username box.
    text = re.sub(r"^https?://(www\.)?(chess\.com/member/|lichess\.org/@/)", "", text, flags=re.I)
    text = text.strip("/ @")
    if not text or len(text) > USERNAME_MAX_CHARS or not _USERNAME_RULES[source].match(text):
        raise ExternalError(
            "invalid_username",
            f"That does not look like a {_label(source)} username.",
        )
    return text


def clamp_max_games(value) -> int:
    """`max_games` as an int within [1, MAX_GAMES_CAP]; over the cap is refused."""
    try:
        n = int(value) if value is not None else MAX_GAMES_DEFAULT
    except (TypeError, ValueError):
        raise ExternalError("too_many", f"Ask for between 1 and {MAX_GAMES_CAP} games.")
    if n < 1 or n > MAX_GAMES_CAP:
        raise ExternalError("too_many", f"Ask for between 1 and {MAX_GAMES_CAP} games.")
    return n


# ---------------------------------------------------------------------------
# URLs - built here and nowhere else
# ---------------------------------------------------------------------------

def chesscom_archives_url(username: str) -> str:
    return f"{CHESSCOM_BASE}/{username.lower()}/games/archives"


def chesscom_month_url(username: str, year: int, month: int) -> str:
    return f"{CHESSCOM_BASE}/{username.lower()}/games/{year:04d}/{month:02d}"


def lichess_export_url(username: str) -> str:
    return f"{LICHESS_BASE}/{username}"


def lichess_export_params(max_games: int) -> dict:
    return {
        "max": max_games, "moves": "true", "tags": "true", "clocks": "false",
        "evals": "false", "opening": "true", "finished": "true", "sort": "dateDesc",
    }


# ---------------------------------------------------------------------------
# HTTP - one client shape, one byte cap, one error mapping
# ---------------------------------------------------------------------------

def make_client(**kw) -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}, **kw)


# What the fetchers open when handed no client. A test seam: the API suites
# point it at an httpx.MockTransport so no request leaves the machine.
client_factory = make_client


def _get(client: httpx.Client, source: str, url: str, params=None, accept="application/json") -> bytes:
    """GET with the byte cap, mapping every failure to a category.

    404 is "no such user" on both sites. 429 is their rate limit, reported as
    such rather than as an outage so the person knows waiting will work.
    Anything else - a 5xx, a timeout, a refused connection, a body past the
    cap - is "temporarily unavailable", because from the person's side those
    are the same thing and the details are ours to log, not theirs to read.
    """
    label = _label(source)
    try:
        with client.stream("GET", url, params=params, headers={"Accept": accept}) as response:
            if response.status_code == 404:
                raise ExternalError("username_not_found", f"No {label} account with that username.")
            if response.status_code == 429:
                raise ExternalError("rate_limited", f"{label} is asking us to slow down. Try again in a minute.")
            if response.status_code >= 400:
                logger.warning(f"⚠️ {label} answered {response.status_code} for a public games request")
                raise ExternalError("provider_unavailable", f"{label} is temporarily unavailable.")
            buf = bytearray()
            for chunk in response.iter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_RESPONSE_BYTES:
                    logger.warning(f"⚠️ {label} response exceeded {MAX_RESPONSE_BYTES} bytes; refused")
                    raise ExternalError("provider_unavailable", f"{label} sent more than we can take at once. Ask for fewer games.")
            return bytes(buf)
    except ExternalError:
        raise
    except httpx.HTTPError as exc:
        logger.warning(f"⚠️ {label} request failed: {type(exc).__name__}")
        raise ExternalError("provider_unavailable", f"{label} is temporarily unavailable.")


# ---------------------------------------------------------------------------
# PGN -> one game shape
# ---------------------------------------------------------------------------

_PLACEHOLDERS = frozenset({"?", "??", "????", "????.??.??", "*", "-", ""})


def _clean(value, limit=120):
    text = (value or "").strip()
    if text in _PLACEHOLDERS:
        return None
    return text[:limit]


def _played_at(headers) -> float | None:
    """UTCDate + UTCTime as an epoch, or None. Date alone is fine too."""
    date = _clean(headers.get("UTCDate")) or _clean(headers.get("Date"))
    if not date:
        return None
    clock = _clean(headers.get("UTCTime")) or "00:00:00"
    try:
        return datetime.strptime(f"{date} {clock}", "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _opening_from_chesscom_url(url) -> str | None:
    # e.g. https://www.chess.com/openings/Sicilian-Defense-Najdorf-Variation-6.Be3
    if not url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    tail = re.sub(r"-\d+\..*$", "", tail)  # drop the trailing move list
    return tail.replace("-", " ").strip()[:120] or None


def _normalise_variant(value) -> str:
    text = (value or "standard").strip().lower().replace(" ", "")
    if text in ("standard", "chess", "fromposition"):
        return "standard"
    return text[:40]


def parse_pgn_game(source: str, pgn: str, external_id: str | None = None, extra: dict | None = None) -> dict | None:
    """One PGN game -> the common shape, or None if it has no moves.

    `extra` carries fields the site gives outside the PGN (Chess.com's JSON).
    The PGN is replayed here to count plies and to find out whether it can
    be replayed at all; a game python-chess cannot walk is not offered.
    """
    extra = extra or {}
    try:
        game = chess.pgn.read_game(io.StringIO(pgn))
    except Exception:
        return None
    if game is None:
        return None
    try:
        moves = [n.move.uci() for n in game.mainline() if n.move is not None]
    except Exception:
        return None
    if not moves:
        return None
    h = game.headers
    if external_id is None:
        # Lichess writes the game URL in Site; Chess.com writes it in Link
        # and puts the plain word "Chess.com" in Site. Either way the id is
        # the last path segment of whichever is a URL.
        urls = [u for u in (_clean(h.get("Link"), 200), _clean(h.get("Site"), 200)) if u and "://" in u]
        external_id = urls[0].rstrip("/").rsplit("/", 1)[-1] if urls else None
    variant = _normalise_variant(extra.get("variant") or h.get("Variant"))
    event = _clean(h.get("Event"), 160) or ""
    rated = extra.get("rated")
    if rated is None and event:
        rated = event.lower().startswith("rated")
    opening = _clean(h.get("Opening")) or _opening_from_chesscom_url(h.get("ECOUrl"))
    return {
        "external_id": (external_id or None) and str(external_id)[:80],
        "source": source,
        "white": _clean(h.get("White")),
        "black": _clean(h.get("Black")),
        "result": _clean(h.get("Result"), 12),
        "date": _clean(h.get("UTCDate")) or _clean(h.get("Date")),
        "played_at": extra.get("played_at") or _played_at(h),
        "time_control": _clean(h.get("TimeControl"), 40) or _clean(extra.get("time_control"), 40),
        "rated": bool(rated) if rated is not None else None,
        "variant": variant,
        "supported": variant == "standard" and not h.get("SetUp") == "1",
        "opening": opening,
        "eco": _clean(h.get("ECO"), 8),
        "white_elo": _clean(h.get("WhiteElo"), 8),
        "black_elo": _clean(h.get("BlackElo"), 8),
        "move_count": len(moves),
        "pgn": str(game),
    }


# ---------------------------------------------------------------------------
# Chess.com
# ---------------------------------------------------------------------------

def _month_of(archive_url: str):
    m = re.search(r"/games/(\d{4})/(\d{2})$", archive_url)
    return (int(m.group(1)), int(m.group(2))) if m else None


def fetch_chesscom(username: str, max_games: int, client: httpx.Client | None = None,
                   max_months: int = CHESSCOM_MAX_MONTHS) -> list:
    own = client is None
    client = client or client_factory()
    try:
        raw = _get(client, "chesscom", chesscom_archives_url(username))
        try:
            archives = httpx.Response(200, content=raw).json().get("archives") or []
        except Exception:
            raise ExternalError("provider_unavailable", "Chess.com is temporarily unavailable.")
        months = [m for m in (_month_of(u) for u in archives) if m]
        if not months:
            raise ExternalError("no_games", "No recent public games found for that Chess.com username.")
        # Newest first, and only the last few months - never the whole archive.
        months = sorted(months, reverse=True)[:max_months]
        games = []
        for year, month in months:
            body = _get(client, "chesscom", chesscom_month_url(username, year, month))
            try:
                rows = httpx.Response(200, content=body).json().get("games") or []
            except Exception:
                raise ExternalError("provider_unavailable", "Chess.com is temporarily unavailable.")
            # Newest first, and parse only as many as are still wanted: a
            # month can hold a thousand games and replaying each one costs
            # more than fetching them did.
            rows.sort(key=lambda r: r.get("end_time") or 0, reverse=True)
            for row in rows:
                if len(games) >= max_games:
                    break
                pgn = row.get("pgn")
                if not pgn:
                    continue
                url = row.get("url") or ""
                parsed = parse_pgn_game(
                    "chesscom", pgn,
                    external_id=url.rstrip("/").rsplit("/", 1)[-1] if url else None,
                    extra={
                        "rated": row.get("rated"),
                        "variant": row.get("rules"),
                        "time_control": row.get("time_control"),
                        "played_at": row.get("end_time"),
                    },
                )
                if parsed:
                    games.append(parsed)
            if len(games) >= max_games:
                break
        if not games:
            raise ExternalError("no_games", "No recent public games found for that Chess.com username.")
        games.sort(key=lambda g: g.get("played_at") or 0, reverse=True)
        return games[:max_games]
    finally:
        if own:
            client.close()


# ---------------------------------------------------------------------------
# Lichess
# ---------------------------------------------------------------------------

def fetch_lichess(username: str, max_games: int, client: httpx.Client | None = None) -> list:
    own = client is None
    client = client or client_factory()
    try:
        raw = _get(
            client, "lichess", lichess_export_url(username),
            params=lichess_export_params(max_games), accept="application/x-chess-pgn",
        )
        text = raw.decode("utf-8", errors="replace")
        stream = io.StringIO(text)
        games = []
        while len(games) < max_games:
            try:
                game = chess.pgn.read_game(stream)
            except Exception:
                break
            if game is None:
                break
            parsed = parse_pgn_game("lichess", str(game))
            if parsed:
                games.append(parsed)
        if not games:
            raise ExternalError("no_games", "No recent public games found for that Lichess username.")
        return games
    finally:
        if own:
            client.close()


# ---------------------------------------------------------------------------
# The one entry point, with a small cache
# ---------------------------------------------------------------------------

_cache: dict = {}


def _cache_get(key):
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    _cache.pop(key, None)
    return None


def _cache_put(key, value):
    if len(_cache) >= CACHE_MAX_ENTRIES:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[key] = (time.monotonic() + CACHE_TTL_SECONDS, value)


def clear_cache():
    _cache.clear()


def fetch_recent(source, username, max_games=None, client: httpx.Client | None = None) -> list:
    """Validated source and username in; newest `max_games` public games out.

    The cache is keyed on the validated triple and lives a minute: the search
    and the import that follows it are two requests seconds apart, and the
    second should not cost the site a second crawl.
    """
    source = validate_source(source)
    username = validate_username(source, username)
    n = clamp_max_games(max_games)
    key = (source, username.lower(), n)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    games = fetch_chesscom(username, n, client) if source == "chesscom" else fetch_lichess(username, n, client)
    _cache_put(key, games)
    return games


__all__ = [
    "CHESSCOM_MAX_MONTHS", "ExternalError", "MAX_GAMES_CAP", "MAX_GAMES_DEFAULT",
    "MAX_RESPONSE_BYTES", "SOURCES", "SOURCE_LABELS", "TIMEOUT_SECONDS",
    "USERNAME_MAX_CHARS", "USER_AGENT", "chesscom_archives_url", "chesscom_month_url",
    "clamp_max_games", "clear_cache", "fetch_chesscom", "fetch_lichess", "fetch_recent",
    "lichess_export_params", "lichess_export_url", "make_client", "parse_pgn_game",
    "validate_source", "validate_username",
]

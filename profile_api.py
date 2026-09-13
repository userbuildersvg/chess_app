"""
`/api/profile/*` - the imported library and the profile built from it.

WHY IT NEEDS AN ACCOUNT
-----------------------
Every other surface in this app works as a guest, deliberately. This one does
not, and the reason is not a business rule: a profile is a claim about somebody
built from ten or more of their games over days or weeks, and a guest identity
is a cookie. Someone who imported a library as a guest, waited out the scan and
then cleared their cookies would lose all of it with no way to get it back and
no warning that this was possible. Asking for an account first is the honest
version.

The refusal is a 401 with a sentence, and the UI turns it into a sign-up
prompt rather than an error.

WHY UPLOADS ARE VALIDATED HERE AND NOT IN THE WORKER
----------------------------------------------------
`postmortem_state.parse_pgn` is the door. It is the same validator Review uses,
and it exists because `MoveTree` hands FENs to Stockfish and move_quality.py
documents that the engine *segfaults* on an impossible position rather than
rejecting it - taking the live game down with it. A PGN is a stranger's bytes,
so it is checked before it is stored, not before it is analysed: a row that
cannot be analysed is a row that should never have been written.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time

import chess
import chess.pgn
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import external_games
import learning_events
import postmortem_state
import profile_service
from identity import identity_of, is_guest
from postmortem_state import MAX_PGN_BYTES, PgnError, parse_pgn
from rate_limit import rate_limit
from utils import create_success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])

# Importing is cheap for the caller and expensive for the server - every game
# accepted is a minute of engine time later. The bucket is generous enough for
# somebody pasting a season's games in one sitting and tight enough that it
# cannot be used to fill the queue.
limit_import = rate_limit(40, 3600, "profile-import")
limit_read = rate_limit(120, 3600, "profile-read")
# Each search is one to four requests to somebody else's public API under our
# User-Agent, so the bucket is per person and small.
limit_external = rate_limit(30, 3600, "profile-external")

# One request. The per-owner library cap in profile_service is the real limit;
# this only stops a single body being unreasonable.
MAX_GAMES_PER_REQUEST = 50
MAX_BODY_BYTES = MAX_PGN_BYTES * MAX_GAMES_PER_REQUEST


class ImportRequest(BaseModel):
    """PGN text, which may hold many games, plus which side is the caller's."""
    pgn: str
    # 'white' | 'black' | 'auto'. `auto` matches the account's username against
    # the PGN's White/Black headers and falls back to white - see
    # `_colour_for`, which is careful about why that fallback is acceptable.
    player_color: str = "auto"
    source_name: str = "import.pgn"


ACCOUNT_REQUIRED_MESSAGE = (
    "Create a free account to import recent games from Chess.com or Lichess."
)


def _account_required_response() -> JSONResponse:
    """The refusal the external-import routes give a guest.

    Carries both the shape the brief asked for (`ok`/`error`/`message`) and
    the `detail` every other refusal in this API carries, so an older client
    reading `detail` still gets a sentence.
    """
    return JSONResponse(status_code=401, content={
        "ok": False, "error": "account_required",
        "message": ACCOUNT_REQUIRED_MESSAGE, "detail": ACCOUNT_REQUIRED_MESSAGE,
    })


def _account_or_none(request: Request):
    identity = identity_of(request)
    return None if is_guest(identity) else identity


def _require_account(request: Request) -> str:
    identity = identity_of(request)
    if is_guest(identity):
        raise HTTPException(
            status_code=401,
            detail="Create an account to build an improvement profile - it is built from "
                   "your games over time, and a guest's games are tied to this browser.",
        )
    return identity


# python-chess fills the seven tag-roster headers whether or not the file had
# them, so a PGN with no date arrives as the literal "????.??.??" and one with
# no players as "?". Those are placeholders, not values, and rendering them
# puts "you played white - 12 plies - 0-1 - ????.??.??" in a list somebody is
# meant to recognise their own games in. Absent is absent.
_PLACEHOLDERS = frozenset({"?", "??", "????", "????.??.??", "*", "-"})


def _clean(value, limit: int):
    text = (value or "").strip()
    if not text or text in _PLACEHOLDERS:
        return None
    return text[:limit]


def _headers_of(game: chess.pgn.Game) -> dict:
    h = game.headers
    return {
        "white": _clean(h.get("White"), 120),
        "black": _clean(h.get("Black"), 120),
        "result": _clean(h.get("Result"), 12),
        "date": _clean(h.get("Date"), 24),
        "event": _clean(h.get("Event"), 160),
    }


def _colour_for(requested: str, headers: dict, username: str | None) -> str:
    """
    Which side the account played.

    `auto` matches the username against the two name headers, case-insensitively.
    When neither matches - a PGN from a site with a different handle, or one
    with no names at all - it falls back to white and says so by returning it
    plainly, because the alternative is refusing an import over a header that
    is not required to exist.

    A wrong colour is visible and fixable: the game is listed with the side it
    was filed under, and the fix is to remove it and re-add it with the colour
    stated. A wrong colour that was *guessed silently from a name match* is the
    worse failure - it would file an opponent's blunders as the person's own -
    which is why the match has to be exact rather than fuzzy.
    """
    requested = (requested or "auto").lower()
    if requested in ("white", "black"):
        return requested
    if username:
        lowered = username.strip().lower()
        if (headers.get("white") or "").strip().lower() == lowered:
            return "white"
        if (headers.get("black") or "").strip().lower() == lowered:
            return "black"
    return "white"


@router.post("/games", dependencies=[Depends(limit_import)])
def import_games(payload: ImportRequest, request: Request):
    """
    Add one or more games to the library. They queue for analysis.

    A file with several games in it is normal - every site exports a season
    that way - so the body is split rather than refused, and each game is
    validated on its own. One bad game in a file of thirty does not lose the
    other twenty-nine: it is reported by name and skipped.

    THE ANSWER ACCOUNTS FOR EVERY GAME IN THE BODY
    ----------------------------------------------
    `added + duplicates + skipped + ignored == games in the request`, always.
    This used to stop at the limit and return `added: 50, skipped: 0` for a
    body of 51, and the fifty-first game simply ceased to exist - a 200 that
    says everything went fine while discarding somebody's data. Whatever this
    endpoint could not take, it names.

    * **duplicates** - already in this library, by fingerprint. Not an error.
    * **skipped** - could not be read, or had no moves.
    * **ignored** - past the per-request limit. Nothing was done with these,
      and the caller is told to send them again rather than left to notice.
    """
    owner = _require_account(request)
    text = payload.pgn or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="There is no PGN in that.")
    if len(text.encode("utf-8", errors="ignore")) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="That is too much PGN for one request.")

    username = _username_of(owner)
    stream = io.StringIO(text)
    added, skipped, duplicates = [], [], []
    ignored = 0
    seen_in_request = set()

    while True:
        try:
            game = chess.pgn.read_game(stream)
        except Exception:
            break
        if game is None:
            break

        # Past the limit: counted and named, never silently dropped. The read
        # continues to the end of the body so the count is the real number of
        # games left over rather than "at least one".
        if len(added) >= MAX_GAMES_PER_REQUEST:
            ignored += 1
            continue

        try:
            single = str(game)
            # Through the same door Review uses. Validating the re-serialised
            # single game rather than the original text is deliberate: it is
            # exactly the bytes that will be stored and later replayed.
            parse_pgn(single)
        except PgnError as e:
            skipped.append({"name": _name_of(game), "reason": str(e)})
            continue
        except Exception:
            skipped.append({"name": _name_of(game), "reason": "That game could not be read."})
            continue

        headers = _headers_of(game)
        colour = _colour_for(payload.player_color, headers, username)
        moves = [node.move.uci() for node in game.mainline() if node.move is not None]
        ply_count = len(moves)
        if ply_count == 0:
            skipped.append({"name": _name_of(game), "reason": "That game has no moves in it."})
            continue

        # The game's identity: where it started and what was played. The
        # in-request set catches a body that repeats a game inside itself,
        # which the database's unique index would also catch one row later -
        # doing it here keeps the reason reported as "duplicate" rather than
        # arriving as a conflict on the insert.
        marker = profile_service.fingerprint(game.board().fen(), moves)
        if marker in seen_in_request:
            duplicates.append({"name": _name_of(game), "reason": "That game is already in this upload."})
            continue
        seen_in_request.add(marker)

        try:
            game_id = profile_service.add_game(
                owner, single, headers, colour, ply_count,
                source_name=payload.source_name or "import.pgn",
                game_fingerprint=marker,
            )
        except profile_service.ProfileError as e:
            raise HTTPException(status_code=409, detail=str(e))
        if game_id is None:
            duplicates.append({"name": _name_of(game), "reason": "That game is already in your library."})
            continue
        added.append(game_id)

    if not added and skipped and not duplicates and not ignored:
        raise HTTPException(
            status_code=400,
            detail=skipped[0]["reason"] if len(skipped) == 1
            else f"None of those {len(skipped)} games could be read.",
        )

    return create_success_response(
        _import_message(len(added), len(duplicates), len(skipped), ignored),
        {
            "added": len(added),
            "game_ids": added,
            "duplicates": duplicates,
            "skipped": skipped,
            # How many games in the body were past the per-request limit and
            # were not looked at. Sending them again works.
            "ignored": ignored,
            "limit": MAX_GAMES_PER_REQUEST,
            "progress": profile_service.progress(owner),
        },
    )


def _import_message(added: int, duplicates: int, skipped: int, ignored: int) -> str:
    """One sentence saying what happened to every game in the request.

    Written here rather than in the frontend because the count of things the
    server did is the server's to describe, and a UI that assembles this from
    four numbers will eventually describe a case nobody thought about.
    """
    def games(n):
        return f"{n} game" + ("" if n == 1 else "s")

    parts = [f"Added {games(added)}"]
    if duplicates:
        parts.append(f"{games(duplicates)} already in your library")
    if skipped:
        parts.append(f"{games(skipped)} could not be read")
    if ignored:
        parts.append(
            f"{games(ignored)} left out - only {MAX_GAMES_PER_REQUEST} can be sent at once, "
            "so send those again"
        )
    if len(parts) == 1:
        return parts[0]
    return parts[0] + ", " + ", ".join(parts[1:])


@router.get("/games", dependencies=[Depends(limit_read)])
def list_games(request: Request):
    owner = _require_account(request)
    return create_success_response("Library", {
        "games": profile_service.list_games(owner),
        "progress": profile_service.progress(owner),
    })


@router.delete("/games/{game_id}")
def remove_game(game_id: int, request: Request):
    owner = _require_account(request)
    if not profile_service.remove_game(owner, game_id):
        # 404 and not 403 for a game belonging to somebody else. Telling the
        # caller a row exists but is not theirs confirms it exists.
        raise HTTPException(status_code=404, detail="That game is not in your library.")
    return create_success_response("Removed", {
        "removed": game_id, "progress": profile_service.progress(owner),
    })


@router.get("/progress", dependencies=[Depends(limit_read)])
def get_progress(request: Request):
    """Cheap enough to poll while a scan runs, which is what the UI does."""
    owner = _require_account(request)
    return create_success_response("Progress", profile_service.progress(owner))


@router.get("", dependencies=[Depends(limit_read)])
def get_profile(request: Request):
    owner = _require_account(request)
    return create_success_response("Improvement profile", profile_service.build(owner))


# ---------------------------------------------------------------------------
# Chess.com / Lichess - public games by username, account holders only
# ---------------------------------------------------------------------------


class ExternalSearchRequest(BaseModel):
    source: str
    username: str
    max_games: int | None = None
    # Accepted for API shape; neither site is filtered by it in this version.
    perf_type: str | None = None


class ExternalImportRequest(BaseModel):
    source: str
    username: str
    max_games: int | None = None
    # Which of the searched games to keep, by the site's own id. Empty or
    # absent means every supported game the search returned. The PGN is never
    # taken from the browser: the games are fetched again (from the one-minute
    # cache, normally) so what is stored under "chesscom" really came from
    # Chess.com.
    external_ids: list[str] | None = None


def _external_error(exc: external_games.ExternalError, status: int = 502) -> JSONResponse:
    status = {
        "invalid_source": 400, "invalid_username": 400, "too_many": 400,
        "username_not_found": 404, "no_games": 404, "rate_limited": 429,
        "unsupported_variant": 400,
    }.get(exc.category, status)
    return JSONResponse(status_code=status, content={
        "ok": False, "error": exc.category, "message": exc.message, "detail": exc.message,
    })


def _public_game(g: dict) -> dict:
    """The search row as the browser sees it. Everything but nothing extra."""
    return {k: g.get(k) for k in (
        "external_id", "source", "white", "black", "result", "date", "played_at",
        "time_control", "rated", "variant", "supported", "opening", "eco",
        "white_elo", "black_elo", "move_count", "pgn",
    )}


@router.post("/external/search", dependencies=[Depends(limit_external)])
def external_search(payload: ExternalSearchRequest, request: Request):
    """Recent public games for a username, not yet stored."""
    owner = _account_or_none(request)
    if owner is None:
        return _account_required_response()
    started = time.monotonic()
    source = (payload.source or "").strip().lower()
    learning_events.emit(
        "external_import_search_started", owner, import_source=source,
        max_games=payload.max_games, source_mode="Profile",
    )
    try:
        games = external_games.fetch_recent(payload.source, payload.username, payload.max_games)
    except external_games.ExternalError as exc:
        learning_events.emit(
            "external_import_search_failed", owner, import_source=source,
            error_category=exc.category, source_mode="Profile",
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return _external_error(exc)
    learning_events.emit(
        "external_import_search_completed", owner, import_source=source,
        games_returned=len(games), source_mode="Profile",
        latency_ms=round((time.monotonic() - started) * 1000),
    )
    return {
        "ok": True, "source": source,
        "username": external_games.validate_username(source, payload.username),
        "games": [_public_game(g) for g in games],
        "note": "We only fetch public games for the username you enter. "
                "No Chess.com or Lichess password is required.",
    }


@router.post("/external/import", dependencies=[Depends(limit_external)])
def external_import(payload: ExternalImportRequest, request: Request):
    """Store the chosen searched games under this account, by source.

    Every game in the answer is accounted for: `imported + duplicates +
    skipped == games considered`. A duplicate - same site id, or same moves -
    is refused by the database's unique indexes and reported, never stored
    twice as a second row.
    """
    owner = _account_or_none(request)
    if owner is None:
        return _account_required_response()
    started = time.monotonic()
    try:
        source = external_games.validate_source(payload.source)
        username = external_games.validate_username(source, payload.username)
        games = external_games.fetch_recent(source, username, payload.max_games)
    except external_games.ExternalError as exc:
        return _external_error(exc)

    wanted = {str(x)[:80] for x in (payload.external_ids or []) if x}
    if wanted:
        games = [g for g in games if g.get("external_id") in wanted]
        if not games:
            return JSONResponse(status_code=400, content={
                "ok": False, "error": "not_in_results",
                "message": "None of those games were in the latest search. Search again.",
                "detail": "None of those games were in the latest search. Search again.",
            })

    imported, duplicates, skipped = [], [], []
    for g in games:
        name = f"{g.get('white') or '?'} vs {g.get('black') or '?'}"
        if not g.get("supported"):
            skipped.append({"external_id": g.get("external_id"), "name": name,
                            "reason": "Unsupported variant."})
            continue
        try:
            parsed = chess.pgn.read_game(io.StringIO(g["pgn"]))
            single = str(parsed)
            parse_pgn(single)
        except PgnError as e:
            skipped.append({"external_id": g.get("external_id"), "name": name, "reason": str(e)})
            continue
        except Exception:
            skipped.append({"external_id": g.get("external_id"), "name": name,
                            "reason": "Could not parse this PGN."})
            continue
        headers = _headers_of(parsed)
        moves = [n.move.uci() for n in parsed.mainline() if n.move is not None]
        marker = profile_service.fingerprint(parsed.board().fen(), moves)
        # The account's side is the seat carrying the username the games were
        # fetched under - exact, case-insensitive - which both sites write.
        colour = _colour_for("auto", headers, username)
        try:
            game_id = profile_service.add_game(
                owner, single, headers, colour, len(moves),
                source_name=f"{external_games.SOURCE_LABELS[source]}: {username}",
                game_fingerprint=marker, source=source, source_username=username,
                external_id=g.get("external_id"), time_control=g.get("time_control"),
                rated=g.get("rated"), variant=g.get("variant"), opening=g.get("opening"),
            )
        except profile_service.ProfileError as e:
            return JSONResponse(status_code=409, content={
                "ok": False, "error": "library_full", "message": str(e), "detail": str(e),
            })
        if game_id is None:
            duplicates.append({"external_id": g.get("external_id"), "name": name,
                               "reason": "Already in your library."})
            continue
        imported.append({"id": game_id, "external_id": g.get("external_id"), "name": name})
        learning_events.emit(
            "external_import_game_imported", owner, import_source=source, source_mode="Profile",
            time_control=g.get("time_control"), move_count=g.get("move_count"),
        )

    return {
        "ok": True, "source": source, "username": username,
        "imported": imported, "duplicates": duplicates, "skipped": skipped,
        "imported_count": len(imported), "duplicate_count": len(duplicates),
        "skipped_count": len(skipped),
        "message": _external_message(len(imported), len(duplicates), len(skipped)),
        "progress": profile_service.progress(owner),
        "latency_ms": round((time.monotonic() - started) * 1000),
    }


def _external_message(imported: int, duplicates: int, skipped: int) -> str:
    def games(n):
        return f"{n} game" + ("" if n == 1 else "s")
    parts = [f"Imported {games(imported)}"]
    if duplicates:
        parts.append(f"{games(duplicates)} already in your library")
    if skipped:
        parts.append(f"{games(skipped)} could not be imported")
    return parts[0] if len(parts) == 1 else parts[0] + ", " + ", ".join(parts[1:])


@router.get("/games/{game_id}", dependencies=[Depends(limit_read)])
def get_imported_game(game_id: int, request: Request):
    """One game with its PGN - what "View PGN" and "Copy PGN" read."""
    owner = _require_account(request)
    row = profile_service.get_game(owner, game_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That game is not in your library.")
    return create_success_response("Imported game", {"game": row})


@router.post("/games/{game_id}/review", dependencies=[Depends(limit_import)])
async def review_imported_game(game_id: int, request: Request):
    """
    Open an imported game in Review.

    The same path as "Review this game" from Play (CLAUDE.md §32): the stored
    PGN goes to the one Post-Mortem store, the whole-game scan is started
    here, and the answer is the review state the browser already knows how to
    show. The review remembers where the game came from so a correction made
    in it can be filed back against the library row.
    """
    owner = _require_account(request)
    row = profile_service.get_game(owner, game_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That game is not in your library.")
    import postmortem_api
    started = time.monotonic()
    try:
        game = postmortem_state.postmortem_games.create(
            pgn=row["pgn"], source_name=row.get("source_name") or "Imported game", owner=owner,
        )
    except (PgnError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"That game could not be replayed: {exc}")
    game.origin = "imported"
    game.player_color = row.get("player_color")
    game.import_source = row.get("source") or profile_service.SOURCE_MANUAL
    game.source_username = row.get("source_username")
    game.imported_game_id = int(game_id)
    profile_service.mark_reviewed(owner, game_id)
    learning_events.emit(
        "external_import_game_selected_for_review", owner, game_id=game.id,
        import_source=game.import_source, source_mode="Profile",
        move_count=row.get("ply_count"), time_control=row.get("time_control"),
        latency_ms=round((time.monotonic() - started) * 1000),
    )
    await postmortem_api.start_scan(game.id, request)
    await asyncio.sleep(0)
    return postmortem_api.review_state(game)


@router.get("/evidence", dependencies=[Depends(limit_read)])
def get_evidence(request: Request):
    """Source-tagged evidence from the imported library - counts only."""
    owner = _require_account(request)
    return create_success_response("Imported-game evidence", profile_service.source_evidence(owner))


def _username_of(identity: str):
    """The account's username, for matching PGN headers. None if unavailable.

    Imported lazily and defensively: a failure to look up a name must not fail
    an import, because the only thing it costs is the colour guess.
    """
    try:
        import auth_service
        from identity import account_id_of
        account = account_id_of(identity)
        if account is None:
            return None
        user = auth_service.auth_service.get_user(account)
        return user["username"] if user else None
    except Exception:
        return None


def _name_of(game) -> str:
    h = game.headers
    white = h.get("White") or "?"
    black = h.get("Black") or "?"
    return f"{white} vs {black}"


__all__ = ["router"]

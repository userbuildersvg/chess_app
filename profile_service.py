"""
The imported library, and the profile aggregated out of it.

WHAT THIS OWNS
--------------
Rows in `imported_games` and `game_findings`, and the one query that turns the
second into a set of claims about a person. It knows nothing about Stockfish -
`profile_worker.py` does the analysing and hands findings here - and nothing
about HTTP.

WHY THE PROFILE IS NOT A TABLE
------------------------------
There is no stored profile. It is aggregated on read, every time, from
`game_findings`.

A cached profile would need invalidating every time a game finished analysing,
every time one was deleted, and every time the taxonomy changed - three chances
to serve a claim that the evidence underneath no longer supports. The
aggregation is one grouped query over an indexed column, and this is not a
feature anyone loads in a loop.

THE THRESHOLD IS THE PRODUCT
----------------------------
The whole point is saying "you consistently" rather than "in game 7". So a
theme is not a pattern until the evidence says it is, and the gate is
deliberately about GAMES rather than findings: one catastrophic game can supply
a dozen findings of the same theme, and a dozen findings from one game is not a
pattern, it is a bad afternoon.
"""

from __future__ import annotations

import hashlib
import logging
import time

import db
import pattern_detectors

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# When a theme becomes a claim
# ---------------------------------------------------------------------------

# Below this many analysed games the profile says nothing at all, however
# lopsided the evidence looks. Roughly the 10-15 the product asks for, at the
# bottom of that range because a person who has waited for ten games to be
# scanned has earned an answer.
MIN_GAMES_FOR_PROFILE = 10

# And a theme has to show up across several of them.
MIN_GAMES_PER_THEME = 3

# Confidence bands, by how many of the person's games carry the theme.
HIGH_CONFIDENCE_GAMES = 8
MEDIUM_CONFIDENCE_GAMES = 5

# How many worked examples to carry back with a claim. Three is enough to make
# "here, here and here" convincing and few enough to read.
REPRESENTATIVE_LIMIT = 3

# A person can only import so much. The cap is on the library rather than on
# one upload, because the cost being bounded is engine time, and the engine
# does not care how many requests the work arrived in.
MAX_GAMES_PER_OWNER = 300

STATE_PENDING = "pending"
STATE_ANALYSING = "analysing"
STATE_DONE = "done"
STATE_FAILED = "failed"


class ProfileError(RuntimeError):
    """Something the caller can be told about in a sentence."""


def fingerprint(start_fen: str, uci_moves) -> str:
    """
    A deterministic identity for a game: where it started and what was played.

    Headers are deliberately not in it. The same game exported from two sites
    differs in Event, Site, Date and the spelling of both players' names, and
    none of that makes it a different game - so hashing the file would let a
    re-export slip past as new.

    This exists because every claim the profile makes is a COUNT. Importing one
    PGN five times used to turn one game into five games' worth of evidence and
    push a theme from low confidence to high without a single new move being
    played, which is the feature lying rather than the feature being empty.

    SHA-256 because it needs to be stable across processes and versions, not
    because anything here is a secret; the truncation is to keep the column
    small, and 32 hex characters is 128 bits, which no library of chess games
    is going to collide in.
    """
    body = start_fen.strip() + "|" + " ".join(uci_moves)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------

def add_game(owner: str, pgn: str, headers: dict, player_color: str,
             ply_count: int, source_name: str = "game.pgn",
             game_fingerprint: str | None = None, connect=None):
    """
    Store one validated PGN as pending and return its id, or None if this
    owner already has that exact game.

    The duplicate check is `ON CONFLICT DO NOTHING` against the unique index on
    `(owner, fingerprint)` rather than a SELECT first, because a select-then-
    insert is two statements that can disagree - two requests arriving together
    would both see nothing and both insert. The database decides, once.

    None rather than an exception: a duplicate inside a fifty-game upload is a
    normal thing to happen and the caller reports it alongside everything else
    it could not add. It is not an error, and raising would make the ordinary
    case of "I re-uploaded my season" look like a failure.
    """
    opener = connect or db.connection
    with opener() as conn:
        count = conn.execute(
            "SELECT count(*) FROM imported_games WHERE owner = %s", (owner,)
        ).fetchone()[0]
        if count >= MAX_GAMES_PER_OWNER:
            raise ProfileError(
                f"That is more than {MAX_GAMES_PER_OWNER} games. Remove some before adding more."
            )
        row = conn.execute(
            "INSERT INTO imported_games"
            " (owner, pgn, white, black, result, played_on, event, player_color,"
            "  ply_count, source_name, created_at, state, fingerprint)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (owner, fingerprint) WHERE fingerprint IS NOT NULL"
            " DO NOTHING RETURNING id",
            (owner, pgn, headers.get("white"), headers.get("black"),
             headers.get("result"), headers.get("date"), headers.get("event"),
             player_color, ply_count, source_name[:120], time.time(), STATE_PENDING,
             game_fingerprint),
        ).fetchone()
        return int(row[0]) if row is not None else None


def list_games(owner: str, connect=None) -> list:
    """This owner's library, newest first, with how much of it is analysed."""
    opener = connect or db.connection
    with opener() as conn:
        rows = conn.execute(
            "SELECT g.id, g.white, g.black, g.result, g.played_on, g.event,"
            "       g.player_color, g.ply_count, g.source_name, g.created_at,"
            "       g.state, g.error,"
            "       (SELECT count(*) FROM game_findings f WHERE f.game_id = g.id)"
            " FROM imported_games g WHERE g.owner = %s"
            " ORDER BY g.created_at DESC, g.id DESC",
            (owner,),
        ).fetchall()
    return [
        {
            "id": r[0], "white": r[1], "black": r[2], "result": r[3],
            "played_on": r[4], "event": r[5], "player_color": r[6],
            "ply_count": r[7], "source_name": r[8], "created_at": r[9],
            "state": r[10], "error": r[11], "findings": r[12],
        }
        for r in rows
    ]


def remove_game(owner: str, game_id: int, connect=None) -> bool:
    """Delete one game. Findings follow through the cascade.

    Scoped to the owner in the WHERE clause rather than checked first: a
    check-then-delete is two statements that can disagree, and the id comes
    from a URL.
    """
    opener = connect or db.connection
    with opener() as conn:
        return conn.execute(
            "DELETE FROM imported_games WHERE id = %s AND owner = %s",
            (game_id, owner),
        ).rowcount > 0


def progress(owner: str, connect=None) -> dict:
    """How far through the library the analysis is."""
    opener = connect or db.connection
    with opener() as conn:
        rows = conn.execute(
            "SELECT state, count(*) FROM imported_games WHERE owner = %s GROUP BY state",
            (owner,),
        ).fetchall()
    by_state = {r[0]: int(r[1]) for r in rows}
    total = sum(by_state.values())
    return {
        "total": total,
        "pending": by_state.get(STATE_PENDING, 0),
        "analysing": by_state.get(STATE_ANALYSING, 0),
        "done": by_state.get(STATE_DONE, 0),
        "failed": by_state.get(STATE_FAILED, 0),
        "analysed": by_state.get(STATE_DONE, 0),
        "remaining": by_state.get(STATE_PENDING, 0) + by_state.get(STATE_ANALYSING, 0),
    }


# ---------------------------------------------------------------------------
# The worker's side of the table
# ---------------------------------------------------------------------------

def claim_next_pending(connect=None):
    """
    Take the oldest pending game and mark it analysing, atomically.

    `FOR UPDATE SKIP LOCKED` rather than select-then-update: two workers - or
    one worker and a restart that left the old one briefly alive - would
    otherwise both claim the same game and analyse it twice. There is one
    worker today; the query costs nothing and stops that being a thing anyone
    has to remember.
    """
    opener = connect or db.connection
    with opener() as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT id, owner, pgn, player_color FROM imported_games"
                " WHERE state = %s ORDER BY created_at, id LIMIT 1"
                " FOR UPDATE SKIP LOCKED",
                (STATE_PENDING,),
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE imported_games SET state = %s WHERE id = %s",
                         (STATE_ANALYSING, row[0]))
    return {"id": int(row[0]), "owner": row[1], "pgn": row[2], "player_color": row[3]}


def record_findings(game_id: int, owner: str, findings: list, connect=None) -> int:
    """Write a game's findings and mark it done, in one transaction.

    Together, so a game is never `done` with half its evidence: the profile
    counts rows, and a partially-recorded game would quietly understate a
    theme with nothing to show that it had.
    """
    opener = connect or db.connection
    now = time.time()
    with opener() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM game_findings WHERE game_id = %s", (game_id,))
            for f in findings:
                conn.execute(
                    "INSERT INTO game_findings"
                    " (game_id, owner, ply, theme, severity, cpl, fen_before,"
                    "  move_san, best_san, phase, created_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (game_id, owner, f["ply"], f["theme"], f["severity"], f.get("cpl"),
                     f["fen_before"], f["move_san"], f.get("best_san"), f["phase"], now),
                )
            conn.execute(
                "UPDATE imported_games SET state = %s, analysed_at = %s, error = NULL"
                " WHERE id = %s",
                (STATE_DONE, now, game_id),
            )
    return len(findings)


def mark_failed(game_id: int, reason: str, connect=None) -> None:
    opener = connect or db.connection
    with opener() as conn:
        conn.execute(
            "UPDATE imported_games SET state = %s, error = %s, analysed_at = %s WHERE id = %s",
            (STATE_FAILED, str(reason)[:300], time.time(), game_id),
        )


def requeue_stuck(connect=None) -> int:
    """
    Put every `analysing` row back to `pending`.

    Run once at boot, and it is what makes the free instance's idle spin-down a
    non-event rather than a permanently stuck job: a row in `analysing` belongs
    to a process that no longer exists, because there is one worker and it has
    only just started.
    """
    opener = connect or db.connection
    with opener() as conn:
        moved = conn.execute(
            "UPDATE imported_games SET state = %s WHERE state = %s",
            (STATE_PENDING, STATE_ANALYSING),
        ).rowcount
    if moved:
        logger.info(f"🧩 Requeued {moved} game(s) left mid-analysis by a previous process")
    return moved


# ---------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------

def _confidence(games_with_theme: int, analysed_games: int) -> str:
    if games_with_theme >= HIGH_CONFIDENCE_GAMES and games_with_theme >= analysed_games * 0.4:
        return "high"
    if games_with_theme >= MEDIUM_CONFIDENCE_GAMES:
        return "medium"
    return "low"


def _trend(early_rate: float, late_rate: float) -> str:
    """
    Which way it is going, or `stable` when there is not enough to say.

    A 20% band around no change, because two halves of a small library differ
    by that much for no reason at all, and a trend arrow that flickers between
    visits is worse than no arrow.
    """
    if early_rate <= 0 and late_rate <= 0:
        return "stable"
    if early_rate <= 0:
        return "worsening"
    change = (late_rate - early_rate) / early_rate
    if change <= -0.2:
        return "improving"
    if change >= 0.2:
        return "worsening"
    return "stable"


def build(owner: str, connect=None) -> dict:
    """
    Everything the profile can honestly say about this owner.

    Returns the shape the UI renders whether or not there is enough evidence -
    `ready` says which, and `games_needed` says how far off it is. An empty
    findings list with no explanation is indistinguishable from a bug.
    """
    opener = connect or db.connection
    with opener() as conn:
        analysed = conn.execute(
            "SELECT id, created_at FROM imported_games"
            " WHERE owner = %s AND state = %s ORDER BY created_at, id",
            (owner, STATE_DONE),
        ).fetchall()
        rows = conn.execute(
            "SELECT theme, game_id, severity, cpl, ply, move_san, best_san, phase,"
            "       fen_before, created_at"
            " FROM game_findings WHERE owner = %s",
            (owner,),
        ).fetchall()

    analysed_ids = [int(r[0]) for r in analysed]
    analysed_count = len(analysed_ids)
    # Chronological halves, by import order. `//2` puts the odd game in the
    # later half, which is the half a trend claim is about.
    half = analysed_count // 2
    early_ids = set(analysed_ids[:half])
    late_ids = set(analysed_ids[half:])

    order = {gid: i for i, gid in enumerate(analysed_ids)}

    by_theme: dict[str, dict] = {}
    for theme, game_id, severity, cpl, ply, san, best_san, phase, fen, created in rows:
        bucket = by_theme.setdefault(theme, {"rows": [], "games": set()})
        bucket["rows"].append({
            "game_id": int(game_id), "severity": severity, "cpl": cpl, "ply": ply,
            "move_san": san, "best_san": best_san, "phase": phase, "fen_before": fen,
            "created_at": created,
        })
        bucket["games"].add(int(game_id))

    findings = []
    for theme, bucket in by_theme.items():
        games = bucket["games"] & set(analysed_ids)
        if len(games) < MIN_GAMES_PER_THEME:
            continue
        items = [r for r in bucket["rows"] if r["game_id"] in games]
        if not items:
            continue

        early_hits = len({r["game_id"] for r in items if r["game_id"] in early_ids})
        late_hits = len({r["game_id"] for r in items if r["game_id"] in late_ids})
        early_rate = early_hits / len(early_ids) if early_ids else 0.0
        late_rate = late_hits / len(late_ids) if late_ids else 0.0

        worst = sorted(items, key=lambda r: -(r["cpl"] or 0))[:REPRESENTATIVE_LIMIT]
        seen = sorted(items, key=lambda r: order.get(r["game_id"], 0))

        findings.append({
            "theme": theme,
            "label": pattern_detectors.theme_label(theme),
            "claim": pattern_detectors.theme_claim(theme),
            "description": pattern_detectors.THEMES.get(theme, {}).get("description"),
            "check": pattern_detectors.THEMES.get(theme, {}).get("check"),
            "evidence_count": len(items),
            "games_count": len(games),
            "confidence": _confidence(len(games), analysed_count),
            # Only claimed once BOTH halves have games in them. A trend across
            # a library of three is arithmetic, not a finding.
            "trend": _trend(early_rate, late_rate) if (early_ids and late_ids) else "stable",
            "first_seen_game": seen[0]["game_id"],
            "last_seen_game": seen[-1]["game_id"],
            "representative": [
                {
                    "game_id": r["game_id"], "ply": r["ply"], "move_san": r["move_san"],
                    "best_san": r["best_san"], "cpl": r["cpl"], "phase": r["phase"],
                    "severity": r["severity"], "fen_before": r["fen_before"],
                }
                for r in worst
            ],
        })

    # Strongest first: the number of games it shows up in is the thing that
    # makes a claim worth reading, not the raw count of moves.
    findings.sort(key=lambda f: (-f["games_count"], -f["evidence_count"]))

    ready = analysed_count >= MIN_GAMES_FOR_PROFILE
    return {
        "ready": ready,
        "analysed_games": analysed_count,
        "games_needed": max(0, MIN_GAMES_FOR_PROFILE - analysed_count),
        "minimum_games": MIN_GAMES_FOR_PROFILE,
        # Withheld rather than shown weakly. A pattern offered before the
        # evidence supports it is the one thing that would make the whole
        # feature untrustworthy, and "not yet" is a real answer.
        "findings": findings if ready else [],
        "progress": progress(owner, connect=connect),
    }


__all__ = [
    "MAX_GAMES_PER_OWNER",
    "fingerprint",
    "MIN_GAMES_FOR_PROFILE",
    "MIN_GAMES_PER_THEME",
    "ProfileError",
    "STATE_ANALYSING",
    "STATE_DONE",
    "STATE_FAILED",
    "STATE_PENDING",
    "add_game",
    "build",
    "claim_next_pending",
    "list_games",
    "mark_failed",
    "progress",
    "record_findings",
    "remove_game",
    "requeue_stuck",
]

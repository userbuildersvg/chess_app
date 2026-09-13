"""
Canonical state for Post-Mortem: an imported game, its analysis, and the
branches explored off it.

Pure, in exactly the sense sandbox_state.py is pure: python-chess and nothing
else. No FastAPI, no Stockfish, no Gemini, no SQLite. That is what makes the
replay rules testable without an engine (test_postmortem_state.py) and what
keeps the one thing this module is responsible for - *what actually happened
in the game* - free of anything that could make it approximate.

Why it reuses the sandbox's MoveTree instead of storing a list of plies
----------------------------------------------------------------------

A finished game looks like a flat list of moves, and the first instinct is to
store one. But the headline interaction here is "play a different move": the
user stops at move 17, plays something else, and explores what would have
happened. That is a second continuation from one position, which is precisely
what a flat list cannot represent - and precisely what `MoveTree` was built
for when Learner Mode needed the same thing.

So an imported game *is* a MoveTree whose root is the game's starting position
and whose first line of descendants is the game as played. The tree never
deletes anything, so branching cannot damage the import: the original nodes
stay exactly where they were, and `mainline` - the ordered node ids recorded
at import time - is what says which of them are the real game. Everything the
UI needs to answer "am I on the game or on a what-if?" is a set membership
test against that list.

`MoveTree.play()` also reuses an existing child rather than duplicating it,
which gives a property worth stating: playing the move that was actually
played just walks the game forward. Only a *different* move branches. The user
cannot accidentally create a phantom variation by replaying the game by hand.

What is deliberately NOT here
-----------------------------

* No engine. Analysis results are written in by the analysis layer and stored
  as opaque dicts; this module never computes one.
* No learning-service handle, exactly as a SandboxSession has none. Reviewing
  a game must not write to the player's cross-game history - not yet, and not
  by accident. When longitudinal analytics does arrive it will be an explicit
  write from the API layer, not a side effect of opening a PGN.
* No persistence. `PostMortemStore` is in-memory with the same TTL sweep and
  hard cap the sandbox uses. The API boundary is shaped so that swapping this
  for a table is a change to the store, not to everything above it.
"""

import io
import time
from typing import Optional

import chess
import chess.pgn

import pgn_text

from sandbox_state import (
    DEFAULT_SESSION_TTL_SECONDS,
    MoveTree,
    SOURCE_AI,
    SOURCE_HUMAN,
    SessionStore,
    _new_id,
)

# A PGN is user-supplied text arriving over the network, so both of these are
# doorman checks rather than guesses about what people will upload. 512 KB is
# far more than any single game (a long one is a few KB) and small enough that
# parsing it cannot become the request that eats the instance's memory; 800
# plies is a 400-move game, past the longest ever recorded.
MAX_PGN_BYTES = 512 * 1024
MAX_PLIES = 800

# Where a move sits in the game. Used for prioritising what to analyse and for
# the phase labels the coach is given - not for grading, which is the engine's
# job. Ply counts, not move numbers.
OPENING_PLIES = 20
MIDDLEGAME_PLIES = 60

# A node's `source` says where its move came from. The tree already defines
# "setup" (the root), "ai" and "human"; an imported game needs its own, because
# the whole UI turns on the difference between a move that was really played
# and one the user is trying out. The mainline list is what the backend tests
# against - this is what the frontend can key a style off without consulting it.
SOURCE_GAME = "game"

SCAN_IDLE = "idle"
SCAN_RUNNING = "running"
SCAN_DONE = "done"
SCAN_FAILED = "failed"

# PGN header tags worth keeping. Everything else in the file is dropped rather
# than passed through, because these are shown on screen and handed to the
# coach, and an imported file can carry arbitrary attacker-chosen tags.
KEPT_HEADERS = (
    "Event", "Site", "Date", "Round", "White", "Black", "Result",
    "WhiteElo", "BlackElo", "ECO", "Opening", "TimeControl", "Termination",
)
# One header value is at most this long once stored. Long enough for a real
# event name, short enough that nothing downstream has to reckon with a
# megabyte in the "Site" tag.
MAX_HEADER_CHARS = 120


class PgnError(ValueError):
    """
    A PGN we will not accept, carrying a message meant for a chess player.

    The API turns this straight into a 400 body, so the text has to read like
    "that file has no moves in it" rather than a parser diagnostic. Post-Mortem
    is the one place in the app where the user hands us a file, which makes it
    the one place where a bad input is normal rather than exceptional.
    """


def _clean_header(value: str) -> Optional[str]:
    """A header value fit to store, or None if it says nothing."""
    text = (value or "").strip()
    # "?" is PGN's own "unknown", and "*" an unfinished result. Both are
    # noise on screen, so they become an absent field rather than a literal.
    if not text or text in {"?", "??", "-"}:
        return None
    return text[:MAX_HEADER_CHARS]


def phase_of(ply: int) -> str:
    """Which phase a ply belongs to. Coarse on purpose - see OPENING_PLIES."""
    if ply <= OPENING_PLIES:
        return "opening"
    if ply <= MIDDLEGAME_PLIES:
        return "middlegame"
    return "endgame"


# Figurine algebraic notation. Several European tools and most copy-pasted
# articles write "1. ♘f3" rather than "1. Nf3", and python-chess does not
# reject those: it drops the symbol it does not understand and reads
# "♘f3" as the PAWN move f3. The file imports "successfully" as a
# DIFFERENT GAME, with no error to notice - the exact outcome the validation
# below exists to prevent, arriving by the one route that validation cannot
# see, because game.errors is empty.
#
# Mapped rather than rejected: the game is perfectly good, it is only spelled
# in symbols. Both colours map to the same letter because SAN does not encode
# the colour of the mover, and the pawn symbols map to nothing because SAN
# omits the letter for a pawn.
# Figurine handling moved to pgn_text.py when Learn grew a pasted-PGN path of
# its own (§27) and needed the same treatment. Re-exported under the names this
# module has always used, so every call site here - and the tests that reach
# for `_defigurine` directly - go on working against one implementation.
FIGURINE = pgn_text.FIGURINE
_defigurine = pgn_text.defigurine


def parse_pgn(text: str) -> tuple[chess.pgn.Game, int]:
    """
    First game in `text`, validated, plus how many games the file held.

    Every rejection is a PgnError with a sentence a player can act on. The
    validation here is not politeness: `MoveTree` hands FENs to Stockfish, and
    move_quality.py documents that the engine *segfaults* on a position that
    cannot occur in a real game rather than rejecting it - taking down the
    process the live game shares. A PGN is the only path in this application
    by which a stranger's bytes reach that engine, so it is checked at the
    door and not afterwards.
    """
    if text is None or not text.strip():
        raise PgnError("That file is empty - there is no game in it to review.")
    if len(text.encode("utf-8", errors="ignore")) > MAX_PGN_BYTES:
        raise PgnError("That file is too large to be a single chess game.")

    stream = io.StringIO(_defigurine(text))
    try:
        game = chess.pgn.read_game(stream)
    except Exception as exc:  # a parser crash is still just a bad file
        raise PgnError("That file could not be read as PGN.") from exc
    if game is None:
        raise PgnError("That file could not be read as PGN.")

    # python-chess collects recoverable problems rather than raising: an
    # illegal move mid-game leaves the rest of the line silently truncated.
    # A game we cannot replay exactly is a game we must not pretend to have
    # imported, because every later claim - the eval curve, the grades, the
    # coaching - would describe a position the player never had.
    if game.errors:
        # python-chess's own wording, kept because it names the move and the
        # position - but framed for the person who exported the file, not for
        # a parser author. An ambiguous move is a different problem from an
        # illegal one: the first says the file is written in a way that could
        # mean two pieces, and blaming its legality would send someone looking
        # for a bug in a game they played correctly.
        first = str(game.errors[0])
        if "ambiguous" in first.lower():
            raise PgnError(
                "That PGN has a move that could mean two different pieces, so the game "
                f"cannot be replayed exactly ({first}). Re-exporting it from where you "
                "played usually fixes this."
            )
        raise PgnError(f"That PGN contains a move that is not legal in the game ({first}).")

    board = game.board()
    if not board.is_valid():
        raise PgnError("That PGN starts from a position that cannot occur in a real game.")

    moves = list(game.mainline_moves())
    if not moves:
        raise PgnError("That PGN has headers but no moves, so there is nothing to step through.")
    if len(moves) > MAX_PLIES:
        raise PgnError("That game is longer than this reviewer can handle.")

    # How many games the file holds, so the UI can say "the first of 12" rather
    # than silently dropping the rest. Counted by reading headers only.
    extra = 0
    try:
        while chess.pgn.read_headers(stream) is not None:
            extra += 1
            if extra > 500:  # a database dump, not a game file
                break
    except Exception:
        pass

    return game, extra + 1


class PostMortemGame:
    """
    One imported game plus everything the user has done with it.

    Constructed by `game_from_pgn`, not directly, because a PostMortemGame
    without a replayed mainline is not a meaningful object - and the store
    calls `PostMortemGame(id, **kwargs)`, which is why the PGN arrives as a
    keyword argument rather than as a parsed tree.
    """

    def __init__(
        self,
        game_id: str,
        pgn: str = "",
        source_name: str = "game.pgn",
        owner: Optional[str] = None,
    ):
        parsed, game_count = parse_pgn(pgn)

        self.id = game_id
        # The identity that imported it, as the same opaque string the real
        # game and sandbox sessions are keyed on (identity.py). Nothing here
        # parses it; it is only ever compared. None means unowned, which is
        # what the pure tests build and what keeps this module free of any
        # opinion about where identities come from.
        self.owner = owner
        self.source_name = source_name[:120] if source_name else "game.pgn"
        # Where the game came from. "pgn" for a dropped file; "play" when the
        # real game handed itself over at the end (app.py's /from-play), in
        # which case `player_color` says which seat was the human's so the
        # review can orient the board and name the seats. Set by the caller
        # after construction; the parser knows nothing about it.
        self.origin = "pgn"
        self.player_color = None
        # Set when the review was opened from the account's imported library
        # (profile_api /games/{id}/review): "chesscom" | "lichess" | "manual",
        # the username the games were fetched under, and the library row, so
        # a correction made here can be filed back against that game.
        self.import_source = None
        self.source_username = None
        self.imported_game_id = None
        self.game_count = game_count
        # The PGN exactly as it arrived. Kept because it is the only
        # authoritative record of what was imported: everything else in this
        # object is derived from it, and a persistence layer added later
        # should store this rather than our reconstruction of it.
        self.pgn = pgn

        self.headers = {
            key: value
            for key in KEPT_HEADERS
            if (value := _clean_header(parsed.headers.get(key, ""))) is not None
        }

        board = parsed.board()
        self.start_fen = board.fen()
        # A game that started from a set position rather than the initial
        # array - a study, a puzzle, an adjourned position. Worth knowing:
        # opening-book grading and phase labels mean nothing for one.
        self.from_setup = bool(parsed.headers.get("FEN"))
        self.tree = MoveTree(self.start_fen)

        # The game as played, root first. This list is the definition of "the
        # original game": it is written once here and never mutated, so no
        # amount of branching can change what the import was.
        self.mainline: list[str] = [self.tree.root_id]
        for move in parsed.mainline_moves():
            node = self.tree.play(move.uci(), source=SOURCE_GAME)
            self.mainline.append(node.id)
        self._mainline_set = set(self.mainline)
        # Back to the start: a review opens on move 1, not on the final
        # position, because the point is to walk the game.
        self.tree.goto(self.tree.root_id)

        final = self.tree.board_at(self.mainline[-1])
        self.result = self.headers.get("Result") or "*"
        self.termination = _termination_of(final, self.result)

        # node id -> the analysis layer's evidence dict for the move that
        # created that node. Written by postmortem_analysis via `record`;
        # never computed here.
        self.analysis: dict[str, dict] = {}
        # The evaluation of the *starting* position, which no move produced
        # and which the eval curve therefore needs separately.
        self.start_eval: Optional[dict] = None
        self.scan = {
            "status": SCAN_IDLE,
            "analysed": 0,
            "total": len(self.mainline) - 1,
            "depth": None,
            "error": None,
        }
        self.summary: Optional[dict] = None

        # The coach transcript, on the game rather than at module level, for
        # the reason every other transcript in this app is: two people
        # reviewing two games must not see each other's conversation, and
        # neither may touch the real game's own.
        self.chat_history: list[dict] = []

        self.created_at = time.time()
        self.last_active = self.created_at

    # -- housekeeping ----------------------------------------------------

    def touch(self) -> None:
        self.last_active = time.time()

    # -- where are we ----------------------------------------------------

    def is_mainline(self, node_id: str) -> bool:
        """Is this node part of the game as actually played?"""
        return node_id in self._mainline_set

    def ply_of(self, node_id: str) -> int:
        """
        How many half-moves from the start. Works on branch nodes too, since
        it counts the path rather than looking the node up in the mainline.
        """
        return len(self.tree.path_to(node_id)) - 1

    def branch_point(self, node_id: Optional[str] = None) -> Optional[str]:
        """
        The last real-game position on the way to `node_id`, or None if the
        node is itself on the game.

        This is what "return to the game" navigates to, and what tells the
        coach which actual decision the what-if is a departure from. Walking
        up from the node rather than remembering where the branch started
        means it stays correct through any number of nested branches.
        """
        node_id = node_id or self.tree.current_id
        if self.is_mainline(node_id):
            return None
        for node in reversed(self.tree.path_to(node_id)):
            if self.is_mainline(node.id):
                return node.id
        # Unreachable: the root is always on the mainline.
        return self.mainline[0]

    def next_mainline_id(self, node_id: Optional[str] = None) -> Optional[str]:
        """
        The game's own next move from here, or None at the last move.

        Distinct from `MoveTree.forward()`, which follows the most recently
        created child - after a branch that is the what-if, which is right for
        the sandbox and wrong here: stepping forward through a game should
        step through the game.
        """
        node_id = node_id or self.tree.current_id
        if not self.is_mainline(node_id):
            return None
        index = self.mainline.index(node_id)
        return self.mainline[index + 1] if index + 1 < len(self.mainline) else None

    # -- analysis ---------------------------------------------------------

    def record(self, node_id: str, evidence: Optional[dict]) -> bool:
        """
        Attach an analysis result to the node whose move it describes.

        Returns False rather than raising when the node is gone, for the same
        reason `MoveTree.attach_narration` does: the caller is a background
        scan whose result may simply no longer be wanted. Node ids are minted
        once and never reused, so a late result either finds its own move or
        finds nothing - it can never land on a different one.
        """
        if node_id not in self.tree.nodes or evidence is None:
            return False
        self.analysis[node_id] = evidence
        return True

    def analysis_of(self, node_id: str) -> Optional[dict]:
        return self.analysis.get(node_id)

    def eval_curve(self) -> list[dict]:
        """
        The game's evaluation ply by ply, White's point of view throughout.

        One frame per position including the start, so a chart can be drawn
        straight from it. Entries whose analysis has not arrived yet carry a
        null score rather than being omitted - a curve that changes length as
        the scan progresses would rescale its own axis on every poll.
        """
        curve = [{
            "ply": 0,
            "node_id": self.mainline[0],
            "san": None,
            **(self.start_eval or {"score": None, "mate_in": None}),
        }]
        for ply, node_id in enumerate(self.mainline[1:], start=1):
            entry = self.analysis.get(node_id) or {}
            curve.append({
                "ply": ply,
                "node_id": node_id,
                "san": self.tree.get(node_id).san,
                "score": entry.get("eval_after", {}).get("score"),
                "mate_in": entry.get("eval_after", {}).get("mate_in"),
            })
        return curve

    # -- serialization ----------------------------------------------------

    def move_rows(self) -> list[dict]:
        """
        The move list, one row per ply of the real game.

        Deliberately built from the tree rather than from the PGN text: the
        SANs here are the ones python-chess produced while replaying, so what
        the list shows and what the board does cannot disagree.
        """
        rows = []
        for ply, node_id in enumerate(self.mainline[1:], start=1):
            node = self.tree.get(node_id)
            quality = (self.analysis.get(node_id) or {}).get("quality")
            rows.append({
                "ply": ply,
                # Standard chess numbering: ply 1 is White's move 1.
                "move_number": (ply + 1) // 2,
                "color": node.mover,
                "san": node.san,
                "node_id": node_id,
                "fen": node.fen,
                # The grade, when the scan has reached this move. The frontend
                # keys its colours off `label`, the same contract the real
                # game's move list already uses (move_quality.QUALITY_META).
                "quality": quality,
                # Whether a what-if has been explored INSTEAD of this move -
                # i.e. the position before it has more than one continuation.
                # The marker belongs on the move that was not taken, because
                # that is the decision the alternative is an answer to.
                "has_branch": len(self.tree.get(self.mainline[ply - 1]).children) > 1,
            })
        return rows

    def to_dict(self) -> dict:
        node = self.tree.current
        board = self.tree.board_at()
        branch_point = self.branch_point()
        return {
            "game_id": self.id,
            "source_name": self.source_name,
            "origin": self.origin,
            "player_color": self.player_color,
            "import_source": self.import_source,
            "source_username": self.source_username,
            "imported_game_id": self.imported_game_id,
            "game_count": self.game_count,
            "headers": self.headers,
            "result": self.result,
            "termination": self.termination,
            "start_fen": self.start_fen,
            "from_setup": self.from_setup,
            "total_plies": len(self.mainline) - 1,

            # Where the board is now.
            "fen": node.fen,
            "turn": node.turn,
            "current_id": node.id,
            "ply": self.ply_of(node.id),
            "legal_moves": [m.uci() for m in board.legal_moves],
            "line_san": self.tree.line_san(),
            "status": _status_of(board),

            # Original game or what-if, and where the what-if left.
            "on_mainline": self.is_mainline(node.id),
            "branch_point": branch_point,
            "branch_ply": self.ply_of(branch_point) if branch_point else None,
            "branch_line_san": [
                n.san for n in self.tree.path_to(node.id)[self.ply_of(branch_point) + 1:]
            ] if branch_point else [],

            "scan": dict(self.scan),
            "summary": self.summary,
            "created_at": self.created_at,
            "last_active": self.last_active,
        }


def _status_of(board: chess.Board) -> dict:
    """
    What the board is saying right now, in the same vocabulary the real game
    and the sandbox use, so one alert strip can serve all three.
    """
    if board.is_checkmate():
        return {"state": "checkmate", "winner": "black" if board.turn else "white"}
    if board.is_stalemate():
        return {"state": "stalemate", "winner": None}
    if board.is_insufficient_material():
        return {"state": "draw", "winner": None, "reason": "insufficient material"}
    if board.is_seventyfive_moves():
        return {"state": "draw", "winner": None, "reason": "seventy-five move rule"}
    if board.is_fivefold_repetition():
        return {"state": "draw", "winner": None, "reason": "fivefold repetition"}
    if board.is_check():
        return {"state": "check", "winner": None}
    return {"state": "playing", "winner": None}


def _termination_of(final: chess.Board, result: str) -> dict:
    """
    How the game ended, reconciling the board with the PGN's own claim.

    The two can disagree, and the disagreement is informative rather than an
    error: a game whose final position is not mate but whose result is 1-0
    ended by resignation, timeout or agreement. The board is authoritative
    about the position; the header is the only record of what happened away
    from it. Both are reported, and neither is invented.
    """
    on_board = _status_of(final)
    if on_board["state"] in {"checkmate", "stalemate", "draw"}:
        return {"kind": on_board["state"], "result": result, "detail": on_board.get("reason")}
    if result in {"1-0", "0-1"}:
        return {"kind": "decisive", "result": result, "detail": "the game ended away from the board"}
    if result == "1/2-1/2":
        return {"kind": "draw", "result": result, "detail": "agreed or claimed"}
    return {"kind": "unfinished", "result": result, "detail": None}


def game_from_pgn(pgn: str, source_name: str = "game.pgn", owner: Optional[str] = None) -> PostMortemGame:
    """A standalone imported game, outside any store. Used by the pure tests."""
    return PostMortemGame(_new_id(), pgn=pgn, source_name=source_name, owner=owner)


# The process-wide store of imported games.
#
# Same sweep-and-cap semantics as the sandbox's, and for the same reason: this
# is reachable by anyone who can open the app, and an imported game holds a
# whole tree plus its analysis. Capped lower than the sandbox's 50 because one
# of these is a great deal larger than one sandbox session.
postmortem_games = SessionStore(
    ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
    max_sessions=20,
    session_class=PostMortemGame,
)

__all__ = [
    "MAX_PGN_BYTES",
    "MAX_PLIES",
    "PgnError",
    "PostMortemGame",
    "SCAN_DONE",
    "SCAN_FAILED",
    "SCAN_IDLE",
    "SCAN_RUNNING",
    "SOURCE_AI",
    "SOURCE_GAME",
    "SOURCE_HUMAN",
    "game_from_pgn",
    "parse_pgn",
    "phase_of",
    "postmortem_games",
]

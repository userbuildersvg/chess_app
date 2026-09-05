"""
scenario_service.py

Natural-language scenario generation for Sandbox Learner Mode: turn "give me
a hard endgame as white" or "black has a queen and a rook, white has two
pawns, make it winnable" into a legal, validated starting position.

The one rule this module exists to enforce
------------------------------------------

**Gemini never emits a FEN.** Asked for one directly it will happily produce
positions with two black kings, pawns on the first rank, or the side not to
move sitting in check. That last one is not a cosmetic problem: move_quality.py
documents that Stockfish *segfaults* on an unreachable position rather than
rejecting it, taking the shared engine process down with it - and that engine
is shared with the real game.

So the split is:

  Gemini  ->  structured constraints (what material, which side, what theme)
  Python  ->  the actual position, built and validated with python-chess

Every position this module returns has been through `board.is_valid()`, plus
a few extra checks that `is_valid()` does not make (that the game isn't
already over, that neither side starts in check). A position that fails is
retried or the request fails honestly - it is never handed onward.

Three ways a position gets built
--------------------------------

1. **A named opening.** move_quality.py already carries ~40 named openings as
   SAN move sequences, each parsed and verified at import. Replaying one of
   those is legal by construction and needs no validation at all, so a request
   for "the Sicilian" resolves against that book rather than against the
   model's memory. Gemini's job here is only to *name* which opening is meant.
2. **An explicit move sequence**, for a line the book doesn't have. Each SAN
   move is parsed by python-chess against the running board, so an illegal or
   hallucinated move is caught on the move that introduces it.
3. **Constructed material**, for endgames and custom scenarios. Pieces are
   placed onto an empty board under real constraints (kings never adjacent,
   pawns never on the back ranks) and the result is validated.

Position construction is deliberately pure - no Gemini, no Stockfish - so it
can be tested exhaustively and cheaply. The optional "is this actually
winnable for the side I asked about" check is layered on top via an injected
evaluator, so the pure part stays pure.
"""

import json
import logging
import os
import random
import re
import time
from typing import Optional

import chess
import httpx

from move_quality import OPENING_LINES

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# A fourth chain, led by a model none of the other three lead with (moves:
# gemini-3.5-flash, chat: gemini-3.7-flash, narration: gemini-3.1-flash-lite).
# Same reasoning as always in this project - one shared key, and leads that
# collide compete for the same quota. gemini-3.6-flash is last everywhere
# because it hangs rather than refusing.
GEMINI_SCENARIO_MODELS = [
    m.strip() for m in os.environ.get("GEMINI_SCENARIO_MODELS", "").split(",") if m.strip()
] or [
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
]
# The user is watching a spinner while this runs, so it gets a chat-like
# timeout rather than narration's leisurely one.
REQUEST_TIMEOUT = float(os.environ.get("GEMINI_SCENARIO_TIMEOUT", "15"))

# How many independent placements to try before giving up on a material
# request. Placement is cheap (no engine, no network); the retries exist
# because random placement can produce an immediate checkmate or an invalid
# position, not because it is expected to fail often.
PLACEMENT_ATTEMPTS = 300
# How many *validated* candidate positions to generate when the request also
# asks for a particular side to be winning. Each one costs an engine
# evaluation, so this stays small.
FAVOR_CANDIDATES = 6
# Centipawns from White's point of view above which a position counts as
# "winning for White" (and the negative, for Black).
FAVOR_MARGIN = 150

PIECE_WORDS = {
    "k": chess.KING, "king": chess.KING,
    "q": chess.QUEEN, "queen": chess.QUEEN,
    "r": chess.ROOK, "rook": chess.ROOK, "castle": chess.ROOK,
    "b": chess.BISHOP, "bishop": chess.BISHOP,
    "n": chess.KNIGHT, "knight": chess.KNIGHT, "horse": chess.KNIGHT,
    "p": chess.PAWN, "pawn": chess.PAWN,
}
# Per-side sanity ceilings. A request for nine queens is a misparse, not a
# scenario, and letting it through just produces a position no engine or
# student can make sense of.
MAX_PER_SIDE = 16
MAX_PAWNS = 8


class ScenarioError(Exception):
    """A scenario request that cannot be turned into a legal position."""


# The deepest mate a request may ask for. Each extra move multiplies the
# search, and a student asked to find a mate in six is not being taught a
# pattern any more - they are being asked to calculate. Requests beyond this
# are refused with a message that says so rather than silently handing back
# something else, which is the bug this limit exists to prevent.
#
# Three, and measured rather than guessed: from king-queen-rook against a lone
# king, mate in 1 and 2 land in about 20ms each and mate in 3 in about 1.5s.
# Mate in 4 took 20-26s and then FAILED, because material that overwhelming
# mates in one to three and a mate in exactly four barely exists in it - so
# the search pays full price on hundreds of candidates to find nothing. A
# limit that refuses in a sentence beats one that thinks for half a minute
# and then refuses anyway.
MAX_MATE_IN = 3
# Wall clock for the whole build-and-verify loop when a mate was requested.
# The node budget bounds any ONE position; this bounds the run of them, so a
# request that is merely very unlucky still answers rather than hanging.
MATE_BUILD_SECONDS = 5.0
# A ceiling on nodes visited while proving one position's mate distance, so a
# pathological placement (two queens and a rook against a lone king, where
# every move is nearly mating) cannot stall the request. Hitting it means
# "could not prove it here", and placement simply moves on to the next
# candidate - there are hundreds.
MATE_SEARCH_NODES = 40000


class _NodeBudget:
    """Raises when a single position's search has cost too much."""

    class Exceeded(Exception):
        pass

    def __init__(self, limit: int = MATE_SEARCH_NODES):
        self.left = limit

    def spend(self) -> None:
        self.left -= 1
        if self.left <= 0:
            raise _NodeBudget.Exceeded()


def _can_force_mate(board: chess.Board, moves_left: int, budget: "_NodeBudget") -> bool:
    """
    Can the side to move force checkmate within `moves_left` of its own moves?

    A plain minimax over mates only, which is all this needs: no evaluation,
    no engine, no network. scenario_service deliberately never touches the
    shared engine (see generate_scenario's `evaluator` argument, which is
    injected for exactly that reason), and keeping this pure is also what lets
    test_scenario.py run on a machine with no Stockfish.
    """
    if moves_left <= 0:
        return False
    for move in board.legal_moves:
        budget.spend()
        board.push(move)
        try:
            if board.is_checkmate():
                return True
            if moves_left == 1:
                continue
            # A draw is not a mate, however forced it looks.
            if board.is_stalemate() or board.is_insufficient_material():
                continue
            if _every_reply_still_loses(board, moves_left - 1, budget):
                return True
        finally:
            board.pop()
    return False


def _every_reply_still_loses(board: chess.Board, moves_left: int, budget: "_NodeBudget") -> bool:
    """
    With the DEFENDER to move: does every legal reply still allow mate within
    `moves_left`? A defender with no legal move at all is stalemated (mate was
    already caught by the caller), so that is a draw, not a win.
    """
    replies = list(board.legal_moves)
    if not replies:
        return False
    for move in replies:
        budget.spend()
        board.push(move)
        try:
            if not _can_force_mate(board, moves_left, budget):
                return False
        finally:
            board.pop()
    return True


def mate_distance(board: chess.Board, limit: int = MAX_MATE_IN) -> Optional[int]:
    """
    The exact number of moves in which the side to move can force mate, or
    None if it cannot within `limit` (or if proving it costs too much).

    Exact, not "at most": a request for a mate in two that is answered with a
    mate in one is still the wrong scenario, so the caller needs to be able to
    tell those apart.
    """
    if board.is_game_over():
        return None
    try:
        for n in range(1, limit + 1):
            if _can_force_mate(board, n, _NodeBudget()):
                return n
    except _NodeBudget.Exceeded:
        return None
    return None


# --------------------------------------------------------------------------
# Position construction - pure python-chess, no network, no engine
# --------------------------------------------------------------------------

def normalize_pieces(raw) -> list:
    """
    Turn whatever the model said into a list of piece types.

    Accepts "queen", "Q", "q", "rook", and shorthand like "2 pawns" or
    "pawn x3", because models are inconsistent about this and a strict
    parser here would fail requests that were perfectly clear.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        raw = re.split(r"[,\s]+", raw)
    out = []
    for item in raw:
        if not isinstance(item, str):
            continue
        token = item.strip().lower()
        if not token:
            continue
        # "2 pawns", "3x rook", "pawn x2"
        count = 1
        match = re.match(r"^(\d+)\s*x?\s*(.+)$", token) or re.match(r"^(.+?)\s*x\s*(\d+)$", token)
        if match:
            a, b = match.group(1), match.group(2)
            if a.isdigit():
                count, token = int(a), b
            elif b.isdigit():
                count, token = int(b), a
        token = token.rstrip("s") if token.rstrip("s") in PIECE_WORDS else token
        piece = PIECE_WORDS.get(token)
        if piece is None:
            logger.debug(f"scenario: ignoring unrecognised piece token {item!r}")
            continue
        out.extend([piece] * max(1, min(count, MAX_PER_SIDE)))
    return out


def _prepare_side(pieces: list) -> list:
    """Enforce exactly one king per side, plus the sanity ceilings."""
    non_kings = [p for p in pieces if p != chess.KING]
    pawns = [p for p in non_kings if p == chess.PAWN][:MAX_PAWNS]
    others = [p for p in non_kings if p != chess.PAWN]
    body = (others + pawns)[: MAX_PER_SIDE - 1]
    # The king is not optional and is not the model's to omit; every legal
    # position has exactly one per side.
    return [chess.KING] + body


def build_material_position(
    white_pieces: list,
    black_pieces: list,
    side_to_move: str = "white",
    rng: Optional[random.Random] = None,
    attempts: int = PLACEMENT_ATTEMPTS,
    mate_in: Optional[int] = None,
) -> chess.Board:
    """
    Place the requested material onto an empty board, legally.

    Constraints enforced during placement (cheaper than generating garbage
    and rejecting it afterwards):
      * exactly one king per side, never on adjacent squares
      * pawns only on ranks 2-7, since a pawn on 1 or 8 is not a position
        any game could reach and `is_valid()` rejects it
    Then the finished board is checked with `is_valid()`, and additionally
    rejected if the game is already over or either side starts in check -
    neither makes a usable teaching scenario.

    Castling rights are cleared: a constructed position has no move history
    to justify them, and bogus castling rights are one of the things
    `is_valid()` refuses.

    `mate_in` additionally requires the side to move to have a forced mate in
    exactly that many moves. Placement is random, so this is a filter rather
    than a construction: keep drawing until one of the draws happens to be the
    mate that was asked for. That sounds wasteful and is not - roughly one in
    eight random king-queen-rook-against-king placements is already a mate in
    one - and it has the property that matters here, which is that the
    position handed back is *checked*, not asserted.
    """
    rng = rng or random.Random()
    white = _prepare_side(normalize_pieces(white_pieces))
    black = _prepare_side(normalize_pieces(black_pieces))
    turn = chess.WHITE if str(side_to_move).lower().startswith("w") else chess.BLACK

    if mate_in is not None:
        if not isinstance(mate_in, int) or mate_in < 1:
            raise ScenarioError("a mate has to be in a whole number of moves")
        if mate_in > MAX_MATE_IN:
            raise ScenarioError(
                f"mate in {mate_in} is deeper than this can construct - "
                f"ask for mate in {MAX_MATE_IN} or fewer"
            )

    if len(white) + len(black) > 32:
        raise ScenarioError("that is more pieces than fit on a board")
    if len(white) == 1 and len(black) == 1:
        # Two bare kings. Every placement of these is an immediate draw by
        # insufficient material, so retrying cannot help - say so now
        # instead of burning the whole attempt budget on it.
        raise ScenarioError("a scenario needs some material beyond the two kings")

    pawn_squares = [s for s in chess.SQUARES if 1 <= chess.square_rank(s) <= 6]
    # Some material can only ever be a draw no matter where it lands (king
    # and knight against a bare king, say). That is a fact about what was
    # asked for, not about this particular placement, so once enough
    # attempts have been rejected for exactly that reason, stop and report
    # it properly rather than exhausting the budget and reporting nothing.
    drawn_by_material = 0
    # Counted so an exhausted budget can say WHICH requirement it could not
    # meet: legal placement, or the requested mate.
    mate_misses = 0
    deadline = (time.monotonic() + MATE_BUILD_SECONDS) if mate_in is not None else None

    for _ in range(attempts):
        if deadline is not None and time.monotonic() > deadline:
            break
        board = chess.Board(None)
        free = set(chess.SQUARES)

        wk = rng.choice(list(free))
        board.set_piece_at(wk, chess.Piece(chess.KING, chess.WHITE))
        free.discard(wk)
        # Kings may never stand next to each other - that position cannot
        # arise and is_valid() rejects it.
        bk_options = [s for s in free if chess.square_distance(s, wk) >= 2]
        if not bk_options:
            continue
        bk = rng.choice(bk_options)
        board.set_piece_at(bk, chess.Piece(chess.KING, chess.BLACK))
        free.discard(bk)

        ok = True
        for pieces, color in ((white, chess.WHITE), (black, chess.BLACK)):
            for piece_type in pieces:
                if piece_type == chess.KING:
                    continue
                options = [s for s in free if piece_type != chess.PAWN or s in pawn_squares]
                if not options:
                    ok = False
                    break
                square = rng.choice(options)
                board.set_piece_at(square, chess.Piece(piece_type, color))
                free.discard(square)
            if not ok:
                break
        if not ok:
            continue

        board.turn = turn
        board.castling_rights = chess.BB_EMPTY
        board.ep_square = None
        board.halfmove_clock = 0
        board.fullmove_number = 1

        if not board.is_valid():
            continue
        if board.is_insufficient_material():
            drawn_by_material += 1
            if drawn_by_material >= 25:
                raise ScenarioError(
                    "that material can only ever be a draw - add a pawn or a piece"
                )
            continue
        # A scenario that opens in check, checkmate or stalemate is not a
        # scenario. is_valid() permits all three, so they're screened here.
        if board.is_check() or board.is_game_over():
            continue
        if mate_in is not None:
            # limit=mate_in, so this never searches deeper than it has to and
            # returns None the moment the position is not the mate asked for.
            if mate_distance(board, limit=mate_in) != mate_in:
                mate_misses += 1
                continue
        return board

    if mate_in is not None and mate_misses:
        # The material was placeable; it just never happened to be the mate
        # that was asked for. Say which of the two failed, because they need
        # different things from the user.
        raise ScenarioError(
            f"couldn't build a mate in {mate_in} from that material - "
            "try different pieces, or a different number of moves"
        )
    raise ScenarioError("couldn't place that material into a legal position")


# Words that appear across many opening names and so identify none of them.
_GENERIC_OPENING_WORDS = {
    "gambit", "opening", "defense", "defence", "attack", "system",
    "variation", "game", "line", "main", "chess", "accepted", "declined",
    "classical", "modern", "closed", "open",
}


def _find_opening(name: str) -> Optional[tuple]:
    """
    Match a requested opening against the book in move_quality.py.

    Exact match first, then substring both ways, so "Sicilian" finds
    "Sicilian Najdorf" and "the Najdorf" finds it too. Returns
    (name, san_line) or None.
    """
    if not name:
        return None
    want = name.strip().lower()
    want = re.sub(r"^(the|a|an)\s+", "", want)
    want = re.sub(r"\s+(opening|defen[cs]e|attack|system|variation)$", "", want).strip()
    if not want:
        return None

    for entry_name, line in OPENING_LINES:
        if entry_name.lower() == want:
            return entry_name, line
    matches = [
        (entry_name, line) for entry_name, line in OPENING_LINES
        if want in entry_name.lower() or entry_name.lower().startswith(want)
    ]
    if matches:
        return matches[0]
    # Last resort: a single distinctive word in common, so "najdorf" finds
    # "Sicilian Najdorf". Generic chess vocabulary is excluded - matching on
    # "gambit" would resolve any invented name ("Zugzwang Gambit") to
    # whichever real gambit happens to be listed first, which is worse than
    # admitting we don't know it.
    for token in sorted(want.split(), key=len, reverse=True):
        if len(token) < 4 or token in _GENERIC_OPENING_WORDS:
            continue
        for entry_name, line in OPENING_LINES:
            if token in entry_name.lower():
                return entry_name, line
    return None


def build_from_moves(moves_san, start_fen: Optional[str] = None) -> chess.Board:
    """
    Replay a SAN move list onto a board.

    Legal by construction: python-chess parses each move against the
    position it is actually played from, so a hallucinated or illegal move
    raises here rather than producing a corrupt position downstream.
    """
    board = chess.Board(start_fen) if start_fen else chess.Board()
    if isinstance(moves_san, str):
        moves_san = moves_san.split()
    for san in moves_san or []:
        token = str(san).strip()
        # Tolerate "1." / "1..." move numbers mixed into the list.
        token = re.sub(r"^\d+\.+", "", token).strip()
        if not token:
            continue
        try:
            board.push_san(token)
        except ValueError as exc:
            raise ScenarioError(f"illegal move '{token}' in the requested line") from exc
    return board



def _mate_in_of(constraints: dict) -> Optional[int]:
    """
    The requested mate distance, or None.

    Tolerant about the shape because it is coming from a language model:
    absent, null, "" and 0 all mean "no mate was asked for", while "2" means
    two. A value that is present but unusable is an error rather than a
    silent None - silently dropping it is exactly the bug this field exists
    to fix.
    """
    raw = constraints.get("mate_in")
    if raw is None or raw == "" or raw is False:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ScenarioError(f"'{raw}' is not a number of moves to mate in")
    if value <= 0:
        return None
    return value


def build_position(constraints: dict, rng: Optional[random.Random] = None) -> tuple:
    """
    Constraints -> (board, notes). Pure; no network, no engine.

    `notes` records what actually happened, because it frequently differs
    from what was asked for - the named opening that was matched, or the
    fact that a line was replayed rather than material placed. The endpoint
    surfaces it so the user is told what they got rather than assuming.
    """
    kind = (constraints.get("kind") or "").strip().lower()

    if kind == "opening" or constraints.get("opening_name"):
        found = _find_opening(constraints.get("opening_name", ""))
        if found:
            name, line = found
            plies = constraints.get("opening_plies")
            moves = line.split()
            if isinstance(plies, int) and 0 < plies < len(moves):
                moves = moves[:plies]
            return build_from_moves(moves), f"Book line: {name}"
        # Not in the book - fall through to the model's own move list, which
        # gets validated move by move.
        if constraints.get("moves_san"):
            return (build_from_moves(constraints["moves_san"]),
                    f"Replayed the requested line for {constraints.get('opening_name')}")
        raise ScenarioError(
            f"'{constraints.get('opening_name')}' isn't an opening this app knows, "
            "and no move sequence was supplied"
        )

    if kind == "moves" or constraints.get("moves_san"):
        return build_from_moves(constraints["moves_san"]), "Replayed the requested line"

    if kind == "start" or not (constraints.get("white_pieces") or constraints.get("black_pieces")):
        return chess.Board(), "Standard starting position"

    mate_in = _mate_in_of(constraints)
    board = build_material_position(
        constraints.get("white_pieces"),
        constraints.get("black_pieces"),
        constraints.get("side_to_move", "white"),
        rng=rng,
        mate_in=mate_in,
    )
    if mate_in is not None:
        side = "White" if board.turn else "Black"
        return board, f"Constructed and verified: {side} mates in {mate_in}"
    return board, "Constructed from the requested material"


# --------------------------------------------------------------------------
# Turning a natural-language request into those constraints
# --------------------------------------------------------------------------

SCHEMA_PROMPT = """You turn a chess student's request into structured constraints.

You MUST NOT output a FEN. FENs you write are frequently illegal, and an
illegal position crashes the analysis engine. Describe the position instead;
the server builds and validates it.

Reply with JSON only, matching this shape:

{
  "kind": "opening" | "material" | "start",
  "opening_name": "<name of the opening, if kind is opening>",
  "opening_plies": <how many half-moves of the opening to play out, 4-12>,
  "white_pieces": ["queen", "pawn", "pawn"],
  "black_pieces": ["rook"],
  "side_to_move": "white" | "black",
  "favors": "white" | "black" | "balanced",
  "mate_in": <1-3, or null if the student did not ask for a mate>,
  "difficulty": <1-20, 20 is strongest play>,
  "title": "<short name for this scenario, 2-5 words>",
  "description": "<one sentence on what the student should be looking for>"
}

Rules:
- Kings are implied. Never list a king in white_pieces or black_pieces.
- For "kind": "opening", give opening_name and leave the piece lists empty.
- For "kind": "material", give both piece lists. This is the right choice for
  any endgame or "X has a queen, Y has two pawns" style request.
- "favors" is who should be better. If the student asks for something
  "winnable" as a colour, favors is that colour.
- "mate_in" is how many moves the mate should take, when the student asks for
  a mate: "mate in 2", "M2", "a two-mover", "white to play and mate in one".
  Set side_to_move to the side delivering it and favors to that side. The
  server VERIFIES this against the position it builds, so do not use it for
  requests that are merely winning ("a won endgame") - only for an actual
  forced mate in a stated number of moves. Use null otherwise. Only 1, 2 and
  3 can be built; for anything deeper, use null and say so in description.
- For a mate, give material that can plausibly mate: a queen, or a rook, or
  two rooks, against a bare king or a king with a pawn.
- If the request names no material and no opening, use "kind": "start".
- difficulty: casual/easy requests near 6, "hard"/"tough" near 18, else 12.

Request: """


def _extract_json(text: str) -> dict:
    """
    Pull a JSON object out of a model reply.

    Even asked for JSON, models wrap it in ```json fences often enough that
    parsing has to tolerate it.
    """
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ScenarioError("couldn't read the model's reply as JSON")


class ScenarioService:
    def __init__(self, api_key: str = GEMINI_API_KEY, models: Optional[list] = None):
        self.api_key = api_key
        self.models = list(models) if models is not None else list(GEMINI_SCENARIO_MODELS)
        self._last_good_model: Optional[str] = None

    @property
    def available(self) -> bool:
        return bool(self.api_key) and bool(self.models)

    def _model_order(self) -> list:
        if self._last_good_model and self._last_good_model in self.models:
            return [self._last_good_model] + [m for m in self.models if m != self._last_good_model]
        return list(self.models)

    async def parse_request(self, prompt: str) -> dict:
        """
        Natural language -> constraints dict. Raises ScenarioError on failure.
        """
        if not self.available:
            raise ScenarioError("Scenario generation needs a GEMINI_API_KEY.")

        payload = {
            "contents": [{"role": "user", "parts": [{"text": SCHEMA_PROMPT + prompt}]}],
            # Ask for JSON directly. Not every model honours it, hence
            # _extract_json still tolerating fences and prose.
            "generationConfig": {"responseMimeType": "application/json"},
        }
        last_error = "no models tried"
        for model in self._model_order():
            url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={self.api_key}"
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    response = await client.post(url, json=payload)
            except Exception as e:
                reason = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                logger.warning(f"⚠️ Scenario request to {model} failed ({reason})")
                last_error = f"couldn't reach Gemini ({model}): {reason}"
                continue
            if response.status_code != 200:
                logger.warning(
                    f"⚠️ Scenario HTTP {response.status_code} for {model}: {response.text[:200]}"
                )
                last_error = f"HTTP {response.status_code} for {model}"
                continue
            try:
                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    block = data.get("promptFeedback", {}).get("blockReason")
                    if block:
                        raise ScenarioError(f"Gemini declined that request ({block}).")
                    last_error = f"empty response from {model}"
                    continue
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                constraints = _extract_json(text)
            except ScenarioError:
                raise
            except Exception as e:
                logger.warning(f"⚠️ Couldn't parse scenario reply from {model}: {e}")
                last_error = f"couldn't parse response from {model}: {e}"
                continue

            if self._last_good_model != model:
                logger.info(f"ℹ️ Preferring {model} for subsequent scenario generation")
                self._last_good_model = model
            return constraints

        raise ScenarioError(f"All Gemini models are currently unavailable ({last_error}).")


scenario_service = ScenarioService()


async def generate_scenario(
    prompt: str,
    evaluator=None,
    rng: Optional[random.Random] = None,
    service: Optional[ScenarioService] = None,
) -> dict:
    """
    Full path: natural language -> validated legal position.

    `evaluator` is an optional `async (fen) -> centipawns from White's point
    of view`. When supplied and the request asked for one side to be better,
    several candidate positions are built and the closest match is kept.
    Injected rather than imported so this module never has to touch the
    shared engine directly, and so tests can run without one.

    Returns a dict with fen, title, description, notes and the constraints
    that produced it. Raises ScenarioError with a message fit to show the
    user.
    """
    service = service or scenario_service
    constraints = await service.parse_request(prompt)
    if not isinstance(constraints, dict):
        raise ScenarioError("the model's reply wasn't a constraints object")

    favors = str(constraints.get("favors") or "balanced").lower()
    is_material = (constraints.get("kind") or "").lower() == "material" or bool(
        constraints.get("white_pieces") or constraints.get("black_pieces")
    )

    board, notes = build_position(constraints, rng=rng)

    # A verified mate is not re-rolled. The favour loop keeps whichever
    # candidate scores best in CENTIPAWNS, and a forced mate scores about
    # 9999 whether it is mate in one or mate in eight - so it cannot tell them
    # apart, and re-rolling could only replace a position that is exactly what
    # was asked for with one that merely wins. This is the bug that produced a
    # mate in seven from a request for a mate in one; build_position has
    # already proved the mate, so there is nothing left to look for.
    mate_in = _mate_in_of(constraints)

    # Only material scenarios can be re-rolled: an opening line is what it
    # is, and re-rolling it would just return the same position.
    if mate_in is None and evaluator is not None and is_material and favors in ("white", "black"):
        want_sign = 1 if favors == "white" else -1
        best, best_board, best_notes = None, board, notes
        for _ in range(FAVOR_CANDIDATES):
            try:
                score = await evaluator(board.fen())
            except Exception as e:
                logger.warning(f"⚠️ Scenario evaluation failed, keeping position as built: {e}")
                break
            signed = score * want_sign
            if best is None or signed > best:
                best, best_board, best_notes = signed, board, notes
            if signed >= FAVOR_MARGIN:
                best_notes = f"{notes}; verified winning for {favors}"
                best_board = board
                break
            try:
                board, notes = build_position(constraints, rng=rng)
            except ScenarioError:
                break
        board, notes = best_board, best_notes
        if best is not None and best < FAVOR_MARGIN:
            notes = f"{notes}; closest available was {best:+d}cp for {favors}"

    if not board.is_valid():
        # Belt and braces. build_position already guarantees this; if it ever
        # stops being true, fail here rather than handing the position to the
        # shared engine.
        raise ScenarioError("generated position failed validation")

    difficulty = constraints.get("difficulty")
    try:
        difficulty = max(1, min(20, int(difficulty)))
    except (TypeError, ValueError):
        difficulty = 12

    return {
        "fen": board.fen(),
        # Verified, not requested: whatever is here was proved against the
        # position being returned. The coach is handed this rather than the
        # description the model wrote before the position existed.
        "mate_in": mate_in,
        "title": str(constraints.get("title") or "Custom scenario")[:80],
        "description": str(constraints.get("description") or "")[:400],
        "difficulty": difficulty,
        "side_to_move": "white" if board.turn else "black",
        "notes": notes,
        "constraints": constraints,
    }

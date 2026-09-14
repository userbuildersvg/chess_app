"""
Session-scoped state and a branching move tree for Sandbox Learner Mode.

This module is the foundation the rest of the sandbox stacks on. It is
deliberately pure: no FastAPI, no Stockfish, no Gemini, no SQLite. It knows
about `chess.Board` and nothing else, which is what makes it cheap to test
(see test_sandbox_state.py) and safe to reason about.

Two things it exists to solve
----------------------------

1. **Branching.** The normal game keeps `game.game_history`, a flat list of
   plies. That is fine when there is exactly one line of play, but the
   sandbox's whole point is exploring alternatives: the user takes over the
   board mid-demonstration, plays something else, and that becomes a
   permanent branch off the point where they intervened. A flat list cannot
   represent two continuations from the same position, so this module
   stores moves as a tree of nodes with a parent and ordered children.

2. **Isolation.** Backend state for the real game is module-level globals
   (`game`, `game_mode`, `player_color`, `opponent_profile`, `current_game_id`,
   `game_epoch`). Sandbox sessions must not stomp any of it - a sandbox
   demonstration is not a real game and must never reach the cross-game
   learning DB. Every piece of sandbox state therefore lives inside a
   `SandboxSession`, addressed by session id, and several sessions coexist.

Why nodes carry their own FEN
-----------------------------

Each node stores the full FEN of the position *after* its move. That makes
`board_at()` an O(1) `chess.Board(fen)` instead of a replay from the root,
and it means a node is self-describing: an async job handed a node id needs
no other context to know what position it is talking about.

Why results are addressed by node id, never by ply index
--------------------------------------------------------

The real game grades moves asynchronously and addresses the result by ply
index. That needed a `game_epoch` guard, because a grade that finished after
a reset would otherwise be written onto whatever move now occupied that
index. Ply indices are positions in a list; they get reused.

Node ids are minted once and never reused, so a late async result - the
narration that the user chose to run *in parallel* with move selection -
either finds its node or finds nothing. There is no window in which it can
land on the wrong move, and so no epoch counter is needed here. This is the
property that makes parallel narration safe; keep it.
"""

import threading
import time
import uuid
from typing import Optional

from opponent_profiles import DEFAULT_PROFILE_ID, get_profile
import chess

# A sandbox session is a live object holding a move tree; a public deploy
# must not accumulate them forever. Sessions idle longer than this are
# swept on the next store operation.
DEFAULT_SESSION_TTL_SECONDS = 60 * 60  # 1 hour
# Hard ceiling on concurrent sessions, so a burst of traffic cannot exhaust
# memory even if none of them have gone idle yet. Oldest-idle is evicted
# first when the cap is hit.
DEFAULT_MAX_SESSIONS = 50

# Sources for a node's move, kept as plain strings so they serialize
# straight to JSON for the frontend.
SOURCE_SETUP = "setup"    # the root: a starting position, not a move
SOURCE_AI = "ai"          # played by the AI during a demonstration
SOURCE_HUMAN = "human"    # played by the user after taking over the board

NARRATION_NONE = "none"
NARRATION_PENDING = "pending"
NARRATION_READY = "ready"
NARRATION_FAILED = "failed"


def _new_id() -> str:
    """Short, unique, non-reused node/session id."""
    return uuid.uuid4().hex[:12]


def _color_name(turn: bool) -> str:
    """python-chess uses True for White; the API speaks in color names."""
    return "white" if turn else "black"


class MoveNode:
    """
    One position in the tree, plus the move that led to it.

    The root node has `move is None` and represents the scenario's starting
    position. Every other node represents exactly one half-move.
    """

    __slots__ = (
        "id", "parent_id", "children", "move", "san", "fen", "turn",
        "mover", "source", "explanation", "narration", "narration_status",
        "evaluation", "created_at",
    )

    def __init__(
        self,
        node_id: str,
        parent_id: Optional[str],
        fen: str,
        move: Optional[str] = None,
        san: Optional[str] = None,
        mover: Optional[str] = None,
        source: str = SOURCE_SETUP,
        explanation: Optional[str] = None,
    ):
        self.id = node_id
        self.parent_id = parent_id
        self.children: list[str] = []
        self.move = move
        self.san = san
        self.fen = fen
        # Whose turn it is *at* this position (i.e. who moves next).
        self.turn = _color_name(chess.Board(fen).turn)
        # Who played the move that produced this position (None at the root).
        self.mover = mover
        self.source = source
        # The AI's own justification for choosing this move, as returned by
        # the existing Gemini move selection. Distinct from narration.
        self.explanation = explanation
        # Teaching narration, attached later - possibly by a job that
        # started in parallel with move selection and finished after it.
        self.narration: Optional[str] = None
        self.narration_status = NARRATION_NONE
        self.evaluation: Optional[dict] = None
        self.created_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "children": list(self.children),
            "move": self.move,
            "san": self.san,
            "fen": self.fen,
            "turn": self.turn,
            "mover": self.mover,
            "source": self.source,
            "explanation": self.explanation,
            "narration": self.narration,
            "narration_status": self.narration_status,
            "evaluation": self.evaluation,
        }


class IllegalSandboxMove(ValueError):
    """Raised when a move is not legal in the position it was played from."""


class MoveTree:
    """
    A tree of positions rooted at a starting FEN.

    `current_id` is where the board "is" right now. Playing a move from
    there either follows an existing child (re-walking a line already
    explored) or creates a new one (branching). Nothing is ever deleted, so
    a branch the user abandoned is still there to rewind into.
    """

    def __init__(self, start_fen: Optional[str] = None):
        board = chess.Board(start_fen) if start_fen else chess.Board()
        if not board.is_valid():
            raise ValueError(f"Not a valid chess position: {board.fen()}")
        root = MoveNode(_new_id(), None, board.fen(), source=SOURCE_SETUP)
        self.nodes: dict[str, MoveNode] = {root.id: root}
        self.root_id = root.id
        self.current_id = root.id

    # -- lookups ---------------------------------------------------------

    def get(self, node_id: str) -> MoveNode:
        node = self.nodes.get(node_id)
        if node is None:
            raise KeyError(f"No such node: {node_id}")
        return node

    @property
    def current(self) -> MoveNode:
        return self.nodes[self.current_id]

    def board_at(self, node_id: Optional[str] = None) -> chess.Board:
        """A fresh board for a node's position. O(1) - no replay needed."""
        return chess.Board(self.get(node_id or self.current_id).fen)

    def path_to(self, node_id: Optional[str] = None) -> list[MoveNode]:
        """Root-first list of nodes from the root down to `node_id`."""
        node = self.get(node_id or self.current_id)
        chain = [node]
        while node.parent_id is not None:
            node = self.get(node.parent_id)
            chain.append(node)
        chain.reverse()
        return chain

    def line_san(self, node_id: Optional[str] = None) -> list[str]:
        """The SAN moves from the root to `node_id` - the line as played."""
        return [n.san for n in self.path_to(node_id) if n.san]

    def children_of(self, node_id: Optional[str] = None) -> list[MoveNode]:
        return [self.get(c) for c in self.get(node_id or self.current_id).children]

    def siblings_of(self, node_id: Optional[str] = None) -> list[MoveNode]:
        """
        The other moves tried from the same position.

        This is what answers "why didn't you play X instead?" - the
        alternatives already explored from that exact position.
        """
        node = self.get(node_id or self.current_id)
        if node.parent_id is None:
            return []
        return [self.get(c) for c in self.get(node.parent_id).children if c != node.id]

    # -- mutation --------------------------------------------------------

    def play(
        self,
        uci: str,
        source: str = SOURCE_AI,
        explanation: Optional[str] = None,
    ) -> MoveNode:
        """
        Play `uci` from the current node and make the result current.

        If that move has already been played from here, its existing node is
        reused rather than duplicated - replaying a known line should not
        grow the tree. Any *other* legal move creates a new child, which is
        exactly how taking over the board branches permanently.
        """
        board = self.board_at()
        try:
            move = chess.Move.from_uci(uci)
        except ValueError as exc:
            raise IllegalSandboxMove(f"Not a valid UCI move: {uci!r}") from exc
        if move not in board.legal_moves:
            raise IllegalSandboxMove(f"{uci} is not legal in {board.fen()}")

        for child in self.children_of():
            if child.move == uci:
                self.current_id = child.id
                return child

        mover = _color_name(board.turn)
        san = board.san(move)
        board.push(move)
        node = MoveNode(
            _new_id(), self.current_id, board.fen(),
            move=uci, san=san, mover=mover, source=source,
            explanation=explanation,
        )
        self.nodes[node.id] = node
        self.nodes[self.current_id].children.append(node.id)
        self.current_id = node.id
        return node

    def goto(self, node_id: str) -> MoveNode:
        """Rewind or fast-forward to any node already in the tree."""
        node = self.get(node_id)
        self.current_id = node.id
        return node

    def back(self) -> MoveNode:
        """Step one half-move back. No-op at the root."""
        node = self.current
        if node.parent_id is not None:
            self.current_id = node.parent_id
        return self.current

    def forward(self) -> MoveNode:
        """
        Step forward along the most recently created child.

        Most recent rather than first: after the user branches, "forward"
        should follow the line they just made, not the one they left.
        """
        children = self.children_of()
        if children:
            self.current_id = children[-1].id
        return self.current

    def attach_narration(self, node_id: str, text: Optional[str], status: str) -> bool:
        """
        Attach a narration result to a node.

        Returns False if the node is gone rather than raising, because the
        caller is typically a background job whose result is simply no
        longer wanted. See the module docstring on why this cannot land on
        the wrong move.
        """
        node = self.nodes.get(node_id)
        if node is None:
            return False
        node.narration = text
        node.narration_status = status
        return True

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "root_id": self.root_id,
            "current_id": self.current_id,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "line": [n.id for n in self.path_to()],
        }


class SandboxSession:
    """
    Everything one sandbox user is doing, isolated from the real game.

    Note what is *not* here: no `current_game_id`, no learning-service
    handle, no reference to the module-level `game`. A sandbox session
    cannot write to the cross-game learning DB because it holds nothing
    that could.
    """

    def __init__(
        self,
        session_id: str,
        start_fen: Optional[str] = None,
        profile: str = DEFAULT_PROFILE_ID,
        narration_enabled: bool = True,
        title: str = "Sandbox",
        owner: Optional[str] = None,
    ):
        self.id = session_id
        # Which identity opened this session, as the same opaque string the
        # real game is keyed on (identity.py). Sessions were already isolated
        # from each other - one cannot reach another's tree - but they were
        # not OWNED: anyone who knew or guessed an id could drive it. That gap
        # only mattered once this went public, which is decision 5 in
        # CLAUDE.md section 6. None means unowned, which is what the pure
        # tests construct and what keeps this module free of any opinion about
        # where identities come from.
        self.owner = owner
        self.tree = MoveTree(start_fen)
        # Opponent profile id (opponent_profiles.py); unknown ids are club.
        self.profile = get_profile(profile).id
        self.narration_enabled = narration_enabled
        self.title = title
        self.created_at = time.time()
        self.last_active = self.created_at
        # Per-color last move, mirroring the real game's
        # `last_ai_move_by_color`, so the sandbox gets the same
        # anti-shuffling behaviour without sharing its state.
        self.last_move_by_color: dict[str, Optional[str]] = {"white": None, "black": None}
        # Coach chat transcript, oldest first, as
        # [{"role": "user" | "model", "text": str}].
        #
        # Held on the session, not at module level, for the same reason
        # everything else here is: two people in two sandboxes must not see
        # each other's conversation, and neither may touch the real game's
        # own transcript. It is cleared when a new scenario is generated -
        # a new position is a new subject - but survives moves, rewinds and
        # branching, which are all still about the position under discussion.
        self.chat_history: list[dict] = []
        # What the student asked for, when the session came from a natural
        # language scenario. Given to the coach so it knows the brief.
        self.scenario_description: Optional[str] = None
        # Set when the session was opened from an Improvement Profile theme
        # (profile_api.py): what the student is practising and where the
        # position came from. The engine's answer is NOT in here - it stays
        # in `practice_answer` until the first move has been graded.
        self.practice: Optional[dict] = None
        self.practice_answer: Optional[dict] = None

    def touch(self) -> None:
        self.last_active = time.time()

    def reset(self, start_fen: Optional[str] = None) -> None:
        """Start a new position, discarding the tree."""
        self.tree = MoveTree(start_fen)
        self.last_move_by_color = {"white": None, "black": None}
        self.touch()

    def to_dict(self) -> dict:
        node = self.tree.current
        return {
            "session_id": self.id,
            "title": self.title,
            # Included so a session read back with GET (a reload resuming where
            # it left off) still knows what it was built for. Only the
            # /scenario response used to carry this, which meant the
            # description survived exactly as long as the page did.
            "scenario_description": self.scenario_description,
            "practice": self.practice,
            "opponent_profile": self.profile,
            "approx_elo": get_profile(self.profile).approx_elo,
            "narration_enabled": self.narration_enabled,
            "fen": node.fen,
            "turn": node.turn,
            "current_id": node.id,
            "legal_moves": [m.uci() for m in self.tree.board_at().legal_moves],
            "line_san": self.tree.line_san(),
            "created_at": self.created_at,
            "last_active": self.last_active,
        }


class SessionStore:
    """
    Thread-safe registry of live sandbox sessions.

    Guarded by a plain `threading.Lock` rather than an asyncio one because
    FastAPI runs sync endpoints in a worker thread pool, so this is reached
    from real threads as well as from the event loop. The lock only ever
    covers dict bookkeeping - never a Stockfish search or an HTTP call - so
    it is never held long enough to matter.
    """

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        session_class=None,
    ):
        self._sessions: dict = {}
        self._lock = threading.Lock()
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        # What `create` builds. Defaults to SandboxSession, so nothing that
        # existed before this argument changes. Post-Mortem keeps its games in
        # a store with exactly these sweep-and-cap semantics (postmortem_state
        # .py), and the alternative was a second copy of this bookkeeping that
        # would drift from this one the first time either was fixed. The store
        # only ever calls `_new_id()`, `.last_active` and the constructor, so
        # anything with those three fits.
        self.session_class = session_class or SandboxSession

    def _sweep_unlocked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for sid in [s for s, sess in self._sessions.items() if sess.last_active < cutoff]:
            del self._sessions[sid]
        # Still over the cap after expiry (a burst of active sessions):
        # drop the least recently used until we are back under it.
        while len(self._sessions) >= self.max_sessions:
            oldest = min(self._sessions.items(), key=lambda kv: kv[1].last_active)[0]
            del self._sessions[oldest]

    def create(self, **kwargs) -> SandboxSession:
        with self._lock:
            self._sweep_unlocked()
            session = self.session_class(_new_id(), **kwargs)
            self._sessions[session.id] = session
            return session

    def get(self, session_id: str) -> Optional[SandboxSession]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.touch()
            return session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions)


# The process-wide store. Separate from every global the real game uses.
sandbox_sessions = SessionStore()

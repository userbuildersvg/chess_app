"""
One player's game, instead of the whole server's.

WHY THIS EXISTS
---------------
`app.py` kept the real game in eleven module-level globals - the board, whose
colour the human is, the mode, the AI-vs-AI flag, the current learning row, the
difficulty, the chat transcript, and so on. That is fine for one person on one
laptop and wrong for anything else: two browser tabs already share one board
today, which CLAUDE.md records as a known issue, and it is the reason accounts
cannot simply be bolted on. Signing in would give every user their own name and
the same chess game.

So the globals move here, into an object one player owns, and `app.py` reaches
for the caller's instance instead of for module state.

This deliberately mirrors `sandbox_state.py`. Learner Mode was built with this
separation from the start - a `SandboxSession` holds no module-level reference
and therefore *cannot* reach another session's data - and it has 41 tests
saying so. The real game is being brought up to the standard the sandbox
already meets, using the same shapes so there is one pattern to learn rather
than two.

PURE, LIKE sandbox_state
------------------------
No FastAPI, no Stockfish, no Gemini, no SQLite. A session holds a board and
some flags. `current_game_id` is a plain integer this module never interprets -
`app.py` gets it from learning_service and hands it over - which keeps the
learning DB out of here and keeps this file testable without one.

WHAT THIS IS NOT, YET
---------------------
It is memory. Sessions die with the process, exactly as sandbox sessions do.
That is a real limit and the right next step is persistence: once games live in
Postgres, this store becomes a cache in front of it and `PlayerStore.load` gets
a database behind it rather than a fresh board. The seam is deliberately narrow
so that change is confined to this file.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Optional

from game_logic import ChessGame

# A player's game outlives a sandbox session by a long way. Someone can open a
# game, walk away for lunch and come back to it, and losing the board because a
# TTL expired would be a worse bug than the one this file is fixing. Sandbox
# sessions sweep after an hour because they are scratch space; a game is not.
DEFAULT_SESSION_TTL_SECONDS = 24 * 60 * 60

# Above the sandbox's 50, because every visitor has one of these whereas only
# people who open Learner Mode get a sandbox. Still a cap: this is memory, and
# an unbounded dict keyed on anything a caller controls is how a server falls
# over. Least-recently-used goes first.
DEFAULT_MAX_SESSIONS = 500


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class PlayerSession:
    """
    Everything the real game used to keep in module scope, for one player.

    Each field below was a global in app.py. They are listed in the same order
    they appeared there, so the mapping is checkable rather than trusted.
    """

    def __init__(self, session_id: str, identity: str, difficulty: int = 20):
        self.id = session_id
        # Who this belongs to. An opaque string: a signed-in user's id once
        # there is auth, an anonymous cookie value before then. This module
        # never parses it and must not start - that is what lets the same
        # storage serve both, and what lets sign-in be added without touching
        # anything in here.
        self.identity = identity

        self.game = ChessGame()
        self.player_color: str = "white"
        self.game_mode: str = "human_vs_ai"
        self.ai_vs_ai_running: bool = False
        # Which move each side's AI played last, for the "don't repeat
        # yourself" nudge. Reset with the board, never across sides.
        self.last_ai_move_by_color: dict = {"white": None, "black": None}
        # The learning DB row for the game in progress. Set by app.py, which
        # owns learning_service; None until it does.
        self.current_game_id = None
        self.game_finalized: bool = False
        self.move_quality_enabled: bool = True
        # Bumped on every reset. Move grading runs in the background against a
        # ply INDEX, and indices are recycled by a new game - so a grade that
        # lands late could otherwise be written onto whichever move now sits at
        # that index. Consumers compare the epoch they started with.
        self.game_epoch: int = 0
        self.ai_difficulty: int = difficulty
        self.chat_history: list = []

        # Cached evaluation of this player's position, from White's
        # perspective, for their eval bar. Per-player for the same reason the
        # board is: one shared value meant a second tab's position overwrote
        # the first tab's eval bar every time either side moved.
        self.current_eval: dict = {"score": None, "mate_in": None}
        # Progress of a full-game re-grade for THIS player. `total` of 0 means
        # idle. Was module state, so one player's re-grade rendered a progress
        # bar in everybody's Review panel.
        self.regrade_progress: dict = {"running": False, "done": 0, "total": 0}
        # Serialises AI move generation for this player only. Previously one
        # process-wide lock, which was correct for one board and wrong the
        # moment there were several: one player thinking blocked every other
        # player's move, and "AI is already thinking" could be somebody else's
        # AI entirely. Created lazily by `lock()` below.
        self._ai_move_lock: Optional[asyncio.Lock] = None
        # This player's cross-game learning handle, set by app.py, which owns
        # the learning services - a guest gets an in-memory one that saves
        # nothing (guest_learning.py), an account gets the shared database.
        # Held here rather than imported here so this module stays pure: it
        # never calls into it and never looks inside it.
        self.learning = None

        self.created_at = time.time()
        self.last_active = self.created_at

    def touch(self) -> None:
        self.last_active = time.time()

    def ai_move_lock(self) -> asyncio.Lock:
        """
        This player's AI-move lock, created on first use.

        Lazily, because a session can be created from a worker thread with no
        running event loop, and binding a lock to the wrong loop is the kind
        of failure that shows up much later as a hang rather than an error.
        """
        if self._ai_move_lock is None:
            self._ai_move_lock = asyncio.Lock()
        return self._ai_move_lock

    def reset_board(self, player_color: Optional[str] = None) -> None:
        """
        Start a new game for this player, leaving the account alone.

        Everything per-GAME is cleared and everything per-PLAYER is kept: the
        difficulty they chose and whether they want move grading survive a new
        game, because they are preferences rather than position. app.py made
        the same distinction across its globals; it is written down here.
        """
        self.game = ChessGame()
        if player_color is not None:
            self.player_color = player_color
        self.game_mode = "human_vs_ai"
        self.ai_vs_ai_running = False
        self.last_ai_move_by_color = {"white": None, "black": None}
        self.current_game_id = None
        self.game_finalized = False
        self.game_epoch += 1
        self.chat_history = []
        self.regrade_progress = {"running": False, "done": 0, "total": 0}
        self.touch()

    def to_dict(self) -> dict:
        """Summary for diagnostics. Never the chat transcript or the history."""
        return {
            "session_id": self.id,
            "player_color": self.player_color,
            "game_mode": self.game_mode,
            "ai_vs_ai_running": self.ai_vs_ai_running,
            "difficulty": self.ai_difficulty,
            "move_quality_enabled": self.move_quality_enabled,
            "game_epoch": self.game_epoch,
            "move_count": len(self.game.game_history),
        }


class PlayerStore:
    """
    Live player sessions, keyed by identity.

    Guarded by a plain `threading.Lock` for the same reason SessionStore is:
    FastAPI runs sync endpoints in a worker thread pool, so this is reached
    from real threads as well as from the event loop. The lock only ever covers
    dict bookkeeping - never a Stockfish search or an HTTP call - so it is
    never held long enough to matter.

    Unlike the sandbox's store, sessions are addressed by IDENTITY rather than
    by a minted id. A sandbox session is a thing you open; a player's game is a
    thing you return to, and it has to still be there when you come back with
    the same cookie or the same account.
    """

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
    ):
        self._by_identity: dict[str, PlayerSession] = {}
        self._lock = threading.Lock()
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions

    @staticmethod
    def _release(session: "PlayerSession") -> None:
        """
        Let go of anything the session was holding open.

        Historically this mattered a great deal: a guest's learning layer was
        an in-memory database kept alive by an open connection, and dropping
        the session without closing it leaked that database for the life of
        the process. Guests now share the real store, so the current learning
        handle holds nothing to release and this is a no-op.

        Kept anyway, and still duck-typed. It costs one `getattr`, this module
        deliberately knows nothing about what the handle is, and the next
        thing to hang off a session that DOES hold a resource gets released
        for free instead of leaking until someone notices.
        """
        closer = getattr(session.learning, "close", None)
        if closer is not None:
            try:
                closer()
            except Exception:
                # Reclaiming memory must never be able to fail a request.
                pass

    def _sweep_unlocked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for key in [k for k, s in self._by_identity.items() if s.last_active < cutoff]:
            self._release(self._by_identity.pop(key))
        # Still over the cap after expiry: drop the least recently used until
        # we are back under it.
        while len(self._by_identity) >= self.max_sessions:
            oldest = min(self._by_identity.items(), key=lambda kv: kv[1].last_active)[0]
            self._release(self._by_identity.pop(oldest))

    def for_identity(self, identity: str, difficulty: int = 20) -> PlayerSession:
        """
        This identity's game, creating it on first sight.

        The create-on-miss is what makes an anonymous visitor work exactly as
        they do today: they arrive with a cookie, get a board, and never see a
        sign-in prompt. Signing in changes which identity is passed in and
        nothing else.
        """
        with self._lock:
            session = self._by_identity.get(identity)
            if session is None:
                self._sweep_unlocked()
                session = PlayerSession(_new_id(), identity, difficulty=difficulty)
                self._by_identity[identity] = session
            session.touch()
            return session

    def peek(self, identity: str) -> Optional[PlayerSession]:
        """This identity's game if it has one, without creating it."""
        with self._lock:
            return self._by_identity.get(identity)

    def drop(self, identity: str) -> bool:
        with self._lock:
            session = self._by_identity.pop(identity, None)
            if session is None:
                return False
            self._release(session)
            return True

    def count(self) -> int:
        with self._lock:
            return len(self._by_identity)

    def identities(self) -> list[str]:
        with self._lock:
            return list(self._by_identity)


# The process-wide store. Separate from the sandbox's, and separate from every
# global the real game used to keep - a sandbox session still cannot reach a
# player's game, and now a player cannot reach another player's either.
player_sessions = PlayerStore()

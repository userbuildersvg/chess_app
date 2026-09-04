"""
The learning layer for a guest: same behaviour, nothing on disk.

THE REQUIREMENT
---------------
Guest mode has to work exactly like the app works today - you can do
everything - while saving nothing. Those two pull against each other in
precisely one place, and it is `learning_service.py`: the cross-game learning
layer writes every move to `data/learning.db` and reads it back to reweight
the AI's candidate list and to fill the Learning panel.

Two obvious answers are both wrong:

  * Keep writing to the shared database. Then a guest's play is saved, and
    worse, every anonymous visitor reads and writes ONE pool - so strangers
    silently bias each other's AI and see each other's statistics.
  * Switch learning off for guests (`use_learning=False`, no writes). Nothing
    is saved, but the Learning panel reads zeros forever and the AI stops
    adapting. That is a visible change from how the build works now, which is
    the thing guest mode was supposed not to be.

So: give each guest their own complete learning database that exists only in
memory and dies with them.

HOW
---
By subclassing `LearningService` and changing only where it connects. Every
query, every schema statement, the reweighting rule, the summary maths - all of
it is the real implementation, unmodified. A guest's learning therefore cannot
drift from a signed-in user's, because there is no second implementation to
drift.

The trick is the connection string. `LearningService._connect()` opens a new
connection per call, and a plain `:memory:` database is private to one
connection - so every call would get a fresh, empty database and nothing would
ever appear to be recorded. SQLite's shared-cache in-memory mode
(`file:<name>?mode=memory&cache=shared`) gives every connection with the same
name the same database instead.

That database lives as long as at least one connection to it is open, which is
why `_anchor` exists and is never closed until `close()`. Without it, the
moment a `with self._connect()` block exits with no other connection open, the
entire database is freed - moves would vanish between requests and it would
look like a bug in the endpoint rather than in the storage.

OWNERSHIP
---------
One of these per guest identity, held by `PlayerSession`. When the session is
swept (24h idle) or the process restarts, it goes with it. There is no file to
delete because there was never a file.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
import threading

from learning_service import LearningService

logger = logging.getLogger(__name__)


class GuestLearningService(LearningService):
    """A `LearningService` whose database is memory and whose lifetime is a session."""

    def __init__(self):
        # Unguessable rather than sequential: shared-cache in-memory databases
        # are namespaced per process, so two guests colliding on a name would
        # share a learning pool - the exact leak this class exists to prevent.
        self._name = f"zw_guest_{secrets.token_hex(8)}"
        self._uri = f"file:{self._name}?mode=memory&cache=shared"
        self._closed = False
        # Holds the database alive. Opened before the base class runs its
        # schema creation, because that runs through _connect() and would
        # otherwise create the tables in a database that is freed on the way
        # out of the very same statement.
        self._anchor = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
        # Shared cache means real cross-thread contention, and FastAPI runs
        # sync endpoints on a worker pool. Serialise: these are tiny queries
        # against a database with one game in it, so the lock is never held
        # long enough to be worth measuring.
        self._lock = threading.Lock()
        super().__init__(db_path=self._uri)

    def _connect(self):
        if self._closed:
            raise RuntimeError("This guest's learning session has been closed")
        conn = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def close(self) -> None:
        """Drop this guest's learning data. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._anchor.close()
            except Exception as e:
                logger.debug(f"Guest learning close: {e}")


# One base-class behaviour worth knowing about: `LearningService.__init__`
# calls `os.makedirs(os.path.dirname(db_path) or ".")`. `os.path.dirname` of
# our URI is the empty string, so that resolves to the current directory and
# is a harmless no-op with `exist_ok=True`. If that line ever changes shape,
# this is what breaks first.

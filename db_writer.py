"""
One background thread that writes the rows nobody is waiting for.

Product events (`learning_events`) and move-grade audits (`move_grade_audit`)
are instrumentation: they must land in Postgres, and nothing the player sees
depends on when. They used to be written inline - a synchronous round trip to
Neon (~145ms from the dev box) on whichever thread emitted them, which was
usually the event loop. That put 33 blocking writes inside the Review scan
(5.6s of a 6.7s scan was waiting on the database, not the engine), six or
seven of them inside `/diagnose`, and two on every AI move between the move
landing and the eval refreshing. Measured in CLAUDE.md §36.

So they queue here and one daemon thread drains the queue in order. In order
matters: `link_correction` UPDATEs the row `record` INSERTed a moment earlier,
and a single FIFO worker is the simplest thing that keeps that true.

What this deliberately does not do:

* It does not swallow failures silently. A write that raises is logged at
  WARNING with the reason, once per failure - the same visibility the inline
  version had, minus the stall.
* It does not buffer without bound. Past `MAX_QUEUED` a new write is dropped
  and counted, and the drop is logged. A database that is unreachable for a
  minute should cost the log a line, not the process its memory.
* It is not used for the game record itself. `learning_service.record_move`
  and friends are the data the profile and the AI's reweighting read back,
  and those stay synchronous (off the event loop, via `asyncio.to_thread`) so
  a read that follows a write sees it.

`flush()` blocks until everything queued so far has been attempted. Tests
that read the tables back call it; shutdown calls it so the last events of a
session are not lost to process exit.
"""
import logging
import queue
import threading
from typing import Callable

logger = logging.getLogger(__name__)

MAX_QUEUED = 2000


class BackgroundWriter:
    def __init__(self, name: str = "db-writer", max_queued: int = MAX_QUEUED):
        self._q: "queue.Queue[tuple]" = queue.Queue(maxsize=max_queued)
        self._name = name
        self._thread = None
        self._lock = threading.Lock()
        self.dropped = 0
        self.failed = 0
        self.written = 0

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._drain, name=self._name, daemon=True)
            self._thread.start()

    def _drain(self) -> None:
        while True:
            fn, args, label = self._q.get()
            try:
                fn(*args)
                if label != "flush":
                    self.written += 1
            except Exception as exc:
                self.failed += 1
                logger.warning(f"⚠️ Background write failed ({label}): {exc}")
            finally:
                self._q.task_done()

    def submit(self, fn: Callable, *args, label: str = "") -> bool:
        """Queue `fn(*args)`. False if the queue is full and the write was dropped."""
        self._ensure_thread()
        try:
            self._q.put_nowait((fn, args, label or getattr(fn, "__name__", "write")))
            return True
        except queue.Full:
            self.dropped += 1
            logger.warning(f"⚠️ Background write dropped ({label}): {self._q.maxsize} already queued")
            return False

    def flush(self, timeout: float = 30.0) -> bool:
        """
        Wait until every write queued before this call has been attempted.
        True if the queue drained in time.
        """
        if self._thread is None or not self._thread.is_alive():
            return self._q.empty()
        done = threading.Event()
        try:
            self._q.put((lambda: done.set(), (), "flush"), timeout=timeout)
        except queue.Full:
            return False
        return done.wait(timeout)

    def pending(self) -> int:
        return self._q.qsize()


writer = BackgroundWriter()


def submit(fn: Callable, *args, label: str = "") -> bool:
    return writer.submit(fn, *args, label=label)


def flush(timeout: float = 30.0) -> bool:
    return writer.flush(timeout)

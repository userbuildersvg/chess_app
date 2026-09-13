"""
The latency pass (CLAUDE.md §36), as properties. Real Stockfish, faked Gemini,
no key, no DATABASE_URL.

Three things it holds in place:

1. The engine gate: a priority caller goes ahead of queued non-priority
   callers, nothing slips between two searches held in one priority section,
   the gate is re-entrant, and a thread that does not hold it cannot release
   it.
2. The background writer: writes run in order on one thread, a failing write
   is logged rather than raised into the caller, and flush() waits for what
   was queued before it.
3. /api/move answers before the eval refresh, and a second move played the
   instant the AI has replied is answered by a second AI move - not silently
   skipped because the previous move's bookkeeping still held the lock.
"""
import asyncio
import logging
import os
import sys
import threading
import time

os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ.setdefault("DISABLE_LANGFLOW", "true")

import httpx
from fastapi.testclient import TestClient

import db_writer
import gemini_http
import stockfish_service as sf_module
from stockfish_service import _EngineGate

results = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if not ok and detail else ""))
    results.append(ok)


# ---------------------------------------------------------------------------
print("\n--- 1. the engine gate ---")
# ---------------------------------------------------------------------------
gate = _EngineGate()
order = []

# Hold the gate; queue two non-priority callers and then one priority caller.
# When the holder releases, the priority caller must run first.
gate.acquire()
started = threading.Barrier(3)


def worker(label, priority, barrier=True):
    if barrier:
        started.wait()
    with gate.held(priority=priority):
        order.append(label)
        time.sleep(0.02)


threads = [
    threading.Thread(target=worker, args=("plain-1", False)),
    threading.Thread(target=worker, args=("plain-2", False)),
]
for t in threads:
    t.start()
started.wait()          # all three workers are at the gate together...
time.sleep(0.05)        # ...and the two plain ones have been waiting a while
prio = threading.Thread(target=worker, args=("priority", True, False))
prio.start()
time.sleep(0.05)
gate.release()
for t in threads + [prio]:
    t.join(timeout=5)
check("a priority caller goes ahead of non-priority callers already waiting",
      order[0] == "priority", str(order))
check("the non-priority callers still both ran", sorted(order[1:]) == ["plain-1", "plain-2"], str(order))

# Re-entrancy: a section that holds the gate can acquire it again.
with gate.held(priority=True):
    with gate.held():
        reentered = True
check("the gate is re-entrant", reentered)

# A thread that does not hold the gate cannot release it.
gate.acquire()
err = None


def bad_release():
    global err
    try:
        gate.release()
    except RuntimeError as e:
        err = e


t = threading.Thread(target=bad_release)
t.start(); t.join()
gate.release()
check("a non-owner release raises", isinstance(err, RuntimeError))

# Nothing slips between two searches held in one priority section: while a
# priority holder runs two "searches", a non-priority caller queued between
# them still runs after both.
order = []
seq_gate = _EngineGate()
inner_started = threading.Event()
plain_done = threading.Event()


def two_searches():
    with seq_gate.held(priority=True):
        order.append("search-1")
        inner_started.set()
        time.sleep(0.1)
        order.append("search-2")


def plain():
    inner_started.wait()
    with seq_gate.held():
        order.append("plain")
    plain_done.set()


a = threading.Thread(target=two_searches); b = threading.Thread(target=plain)
a.start(); b.start(); a.join(5); plain_done.wait(5)
check("a non-priority search cannot run between two searches of one priority section",
      order == ["search-1", "search-2", "plain"], str(order))

# The service exposes it: stockfish_service.priority() is the section.
check("StockfishService.priority is a context manager over the same gate",
      hasattr(sf_module.stockfish_service, "priority") and isinstance(sf_module.stockfish_service._lock, _EngineGate))


# ---------------------------------------------------------------------------
print("\n--- 2. the background writer ---")
# ---------------------------------------------------------------------------
w = db_writer.BackgroundWriter(name="test-writer", max_queued=500)
log = []
w.submit(lambda: log.append(1))
w.submit(lambda: log.append(2))
w.submit(lambda: (_ for _ in ()).throw(RuntimeError("boom")), label="boom")
w.submit(lambda: log.append(3))
check("flush waits for everything queued before it", w.flush(timeout=5) and log == [1, 2, 3], str(log))
check("a failing write is counted and does not stop the queue", w.failed == 1 and w.written == 3, f"failed={w.failed} written={w.written}")

# The failure was logged, not swallowed.
class Capture(logging.Handler):
    def __init__(self):
        super().__init__(); self.records = []
    def emit(self, record):
        self.records.append(record.getMessage())


cap = Capture()
logging.getLogger("db_writer").addHandler(cap)
logging.getLogger("db_writer").setLevel(logging.WARNING)
w.submit(lambda: (_ for _ in ()).throw(ValueError("nope")), label="labelled")
w.flush(5)
logging.getLogger("db_writer").removeHandler(cap)
check("a failing write is logged at WARNING with its label and reason",
      any("labelled" in m and "nope" in m for m in cap.records), str(cap.records))

# Order is preserved across many writes.
log.clear()
for i in range(200):
    w.submit(log.append, i)
w.flush(10)
check("200 writes land in submission order", log == list(range(200)))

# A full queue drops and counts rather than blocking the caller.
slow = db_writer.BackgroundWriter(name="slow", max_queued=2)
block = threading.Event()
running = threading.Event()
slow.submit(lambda: (running.set(), block.wait()))
running.wait(5)          # the first write is on the thread, the queue is empty
slow.submit(lambda: None)
slow.submit(lambda: None)
t0 = time.monotonic()
accepted = slow.submit(lambda: None)
check("submitting to a full queue returns immediately and reports the drop",
      accepted is False and slow.dropped == 1 and time.monotonic() - t0 < 0.5)
block.set()

# The two instrumentation modules actually route through it.
import learning_events
import move_grade_audit
check("learning_events persists through db_writer", "db_writer.submit" in open("learning_events.py", encoding="utf-8").read())
check("move_grade_audit persists through db_writer", "db_writer.submit" in open("move_grade_audit.py", encoding="utf-8").read())
check("has_prior answers from the in-memory buffer before touching the database",
      learning_events.EventSink.has_prior.__code__.co_consts and
      "self.recent(MAX_BUFFERED)" in open("learning_events.py", encoding="utf-8").read().split("def has_prior", 1)[1].split("if db.configured()", 1)[0])


# ---------------------------------------------------------------------------
print("\n--- 3. /api/move answers first and the AI never skips a turn ---")
# ---------------------------------------------------------------------------
import app  # noqa: E402  (after the environment above)


class FakeClient:
    """Gemini that picks the first shortlisted move, slowly enough to be a model."""
    is_closed = False

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **kwargs):
        text = json["contents"][0]["parts"][0]["text"]
        moves = [ln.split(". ", 1)[1].split(" ")[0]
                 for ln in text.splitlines() if ln[:1].isdigit() and ". " in ln]
        await asyncio.sleep(0.3)
        body = {"candidates": [{"content": {"parts": [{"text": f"{moves[0]}\nA sound developing move."}]}}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))


app.gemini_move_service.api_key = "fake-key"
httpx.AsyncClient = FakeClient()
gemini_http.reset()

try:
    with TestClient(app.app) as c:
        c.post("/api/reset")
        c.post("/api/difficulty", json={"profile": "master"})

        def wait_turn(color, tries=150):
            for _ in range(tries):
                st = c.get("/api/status").json()
                if st["status"]["turn"] == color:
                    return st
                time.sleep(0.05)
            return st

        t0 = time.monotonic()
        r = c.post("/api/move", json={"move": "e2e4", "guided": False}).json()
        ack_ms = (time.monotonic() - t0) * 1000
        check("the move is accepted", r.get("success") and (r.get("model_move") or {}).get("ai_scheduled"), str(r)[:200])
        check("the response does not wait for the eval refresh (well under one engine search)",
              ack_ms < 150, f"{ack_ms:.0f}ms")
        st = wait_turn("white")
        check("the AI replied", len(st["history"]) == 2, str([h.get("move") for h in st["history"]]))

        # The instant the reply lands, play again - the previous AI task is
        # still writing its record. This used to be skipped.
        legal = st["status"]["legal_moves"]
        second = "g1f3" if "g1f3" in legal else legal[0]
        r = c.post("/api/move", json={"move": second, "guided": False}).json()
        check("a second move played immediately is accepted", r.get("success"), str(r)[:200])
        st = wait_turn("white")
        check("the AI answered the second move as well (no silent skip)",
              len(st["history"]) == 4, str([h.get("move") for h in st["history"]]))

        # Give the detached eval and record a moment, then the bar should be
        # for the current position: an eval exists and it is not the start.
        time.sleep(1.0)
        st = c.get("/api/status").json()
        check("the eval bar has a value after the detached refresh",
              isinstance(st.get("eval"), dict) and ("score" in st["eval"]), str(st.get("eval")))
        check("both of the AI's coach turns are on the transcript",
              len([t for t in st["chat_history"] if t.get("move")]) == 2, str(st["chat_history"])[:300])
finally:
    app.stockfish_service.close()

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

"""
Latency profile of the running dev backend, one flow at a time.

Drives the real API on :8081 (real Stockfish, real Gemini) and prints what
each stage cost, so "the AI is slow" is a set of numbers rather than a
feeling. Nothing here is a test: it asserts nothing, it measures.

    /tmp/chessapp/bin/python tools/latency_probe.py            # everything
    ... --flows play,guided                                    # a subset
    ... --moves 6 --base http://localhost:8081

Flows: play, guided, aivai, review, correction, practice, learn_nl, learn_fen.

The backend's own half is in /tmp/backend.log as `⏱` lines - `ai_move` for
the stage split of a move (queued / stage1 / stage2 / gemini), the hedge
lines from the Gemini services - and this script reads those back to print
the client- and server-side numbers side by side.
"""
import argparse
import os
import re
import statistics
import subprocess
import sys
import time

import httpx

STANDARD_OPENING_WHITE = ["e2e4", "g1f3", "f1c4", "d2d3", "e1g1", "c2c3", "b1d2", "f1e1"]

SAMPLE_PGN = """[Event "Probe"]
[Site "?"]
[Date "2026.09.13"]
[Round "-"]
[White "Probe White"]
[Black "Probe Black"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7 8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7 14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
"""


def ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


class Probe:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=120.0)
        self.rows: list[tuple[str, str, int]] = []

    def note(self, flow: str, stage: str, dur_ms: int):
        self.rows.append((flow, stage, dur_ms))
        print(f"  {flow:11s} {stage:44s} {dur_ms:6d} ms", flush=True)

    # ----- helpers -----------------------------------------------------------
    def status(self) -> dict:
        return self.c.get("/api/status").json()

    def wait_turn(self, color: str, deadline: float = 90.0, every: float = 0.05) -> tuple[int, dict]:
        """Poll /api/status until it is `color`'s turn again. Returns (ms, status)."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < deadline:
            d = self.status()
            if d.get("success") and d["status"]["turn"] == color:
                return ms(t0), d
            time.sleep(every)
        return ms(t0), self.status()

    def wait_grade(self, ply_index: int, deadline: float = 20.0) -> int:
        t0 = time.monotonic()
        while time.monotonic() - t0 < deadline:
            h = self.status().get("history") or []
            if len(h) > ply_index and h[ply_index].get("quality"):
                return ms(t0)
            time.sleep(0.05)
        return -1

    # ----- flows ---------------------------------------------------------------
    def flow_play(self, moves: int, guided: bool):
        flow = "guided" if guided else "play"
        self.c.post("/api/reset")
        self.c.post("/api/difficulty", json={"profile": "casual"})
        explanations = []
        for i, mv in enumerate(STANDARD_OPENING_WHITE[:moves]):
            st = self.status()
            if st["status"]["turn"] != "white" or st["status"]["is_game_over"]:
                break
            legal = st["status"]["legal_moves"]
            if mv not in legal:
                mv = legal[0]
            t0 = time.monotonic()
            r = self.c.post("/api/move", json={"move": mv, "guided": guided}).json()
            self.note(flow, f"/api/move #{i+1} (server ack)", ms(t0))
            if not r.get("success"):
                print("   move refused:", r)
                break
            ply = len(self.status()["history"]) - 1
            grade_ms = self.wait_grade(ply)
            self.note(flow, f"  player grade visible after ack", grade_ms)
            wait_ms, d = self.wait_turn("white")
            self.note(flow, f"  AI reply seen (from ack)", wait_ms)
            self.note(flow, f"  move → AI reply total", ms(t0))
            hist = d.get("chat_history") or []
            if hist:
                last = hist[-1]
                text = last.get("content") or last.get("text") or ""
                explanations.append(len(text))
            ai_ply = len(d["history"]) - 1
            self.note(flow, f"  AI move grade visible", self.wait_grade(ai_ply))
        if explanations:
            print(f"   explanation lengths: {explanations}")

    def flow_aivai(self, moves: int):
        self.c.post("/api/reset")
        t0 = time.monotonic()
        self.c.post("/api/ai-vs-ai/start")
        seen = len(self.status()["history"])
        last = time.monotonic()
        stamps = []
        deadline = time.monotonic() + 120
        while len(stamps) < moves and time.monotonic() < deadline:
            n = len(self.status()["history"])
            if n > seen:
                now = time.monotonic()
                stamps.append(int((now - last) * 1000))
                last = now
                seen = n
            time.sleep(0.05)
        self.c.post("/api/ai-vs-ai/exit")
        for i, s in enumerate(stamps):
            self.note("aivai", f"half-move {i+1} interval", s)
        if stamps:
            self.note("aivai", "median half-move interval", int(statistics.median(stamps)))

    def flow_review(self) -> str | None:
        t0 = time.monotonic()
        r = self.c.post("/api/postmortem/import", json={"pgn": SAMPLE_PGN, "source_name": "probe.pgn"})
        self.note("review", "/import (replay)", ms(t0))
        if r.status_code != 200:
            print("   import failed:", r.text[:200])
            return None
        gid = r.json()["game_id"]
        t0 = time.monotonic()
        self.c.post(f"/api/postmortem/game/{gid}/analyse")
        self.note("review", "/analyse (scan start ack)", ms(t0))
        first_row = None
        while True:
            a = self.c.get(f"/api/postmortem/game/{gid}/analysis").json()
            done = a["scan"].get("done") or a["scan"].get("analysed") or 0
            if first_row is None and done:
                first_row = ms(t0)
                self.note("review", "  first scanned ply visible", first_row)
            if a["scan"]["status"] in ("done", "failed", "error"):
                break
            if ms(t0) > 240_000:
                break
            time.sleep(0.1)
        self.note("review", f"  whole-game scan ({a['scan'].get('total')} plies)", ms(t0))
        # One node at full depth, the way the UI asks when you stop on a move.
        state = self.c.get(f"/api/postmortem/game/{gid}").json()
        nodes = state.get("moves") or []
        if len(nodes) > 20:
            nid = nodes[20]["node_id"]
            t0 = time.monotonic()
            self.c.get(f"/api/postmortem/game/{gid}/analysis/{nid}")
            self.note("review", "  one-node full-depth packet (cached by scan?)", ms(t0))
        return gid

    def flow_correction(self, gid: str):
        state = self.c.get(f"/api/postmortem/game/{gid}").json()
        a = self.c.get(f"/api/postmortem/game/{gid}/analysis").json()
        # pick the worst white move by cpl
        rows = [m for m in a.get("moves", []) if m.get("quality") and m["quality"].get("cpl")]
        rows.sort(key=lambda m: -(m["quality"]["cpl"] or 0))
        if not rows:
            print("   no graded rows to diagnose")
            return None
        node_id = rows[0].get("node_id") or rows[0].get("id")
        t0 = time.monotonic()
        r = self.c.post("/api/learning-loop/diagnose", json={
            "game_id": gid, "node_id": node_id,
            "intent": "I wanted to attack the king and keep the initiative",
            "intent_preset": None,
        })
        self.note("correction", "/diagnose (evidence + Gemini card)", ms(t0))
        if r.status_code != 200:
            print("   diagnose failed:", r.text[:300])
            return None
        body = r.json()
        corr = body.get("correction") or body
        return corr.get("id") or corr.get("correction_id")

    def flow_practice(self, correction_id: str):
        t0 = time.monotonic()
        r = self.c.post("/api/learning-loop/practice/start", json={"correction_id": correction_id})
        self.note("practice", "/practice/start (fresh position)", ms(t0))
        if r.status_code != 200:
            print("   practice failed:", r.text[:300])

    def flow_learn(self):
        t0 = time.monotonic()
        r = self.c.post("/api/sandbox/scenario", json={
            "prompt": "a rook endgame where white has an extra pawn", "profile": "club", "narration_enabled": True})
        self.note("learn_nl", "/scenario (Gemini → legal position)", ms(t0))
        if r.status_code != 200:
            print("   scenario failed:", r.text[:300])
        t0 = time.monotonic()
        r = self.c.post("/api/sandbox/session", json={
            "start_fen": "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
            "profile": "club", "narration_enabled": True, "title": "Sandbox"})
        self.note("learn_fen", "/session from FEN", ms(t0))
        if r.status_code == 200:
            sid = r.json()["session_id"] if "session_id" in r.json() else r.json().get("id")
            t0 = time.monotonic()
            self.c.post(f"/api/sandbox/session/{sid}/ai-move")
            self.note("learn_fen", "  sandbox /ai-move (move + narration start)", ms(t0))

    def summary(self):
        print("\n== summary (median per stage) ==")
        by: dict[tuple[str, str], list[int]] = {}
        for flow, stage, d in self.rows:
            key = (flow, re.sub(r"#\d+", "#n", stage))
            by.setdefault(key, []).append(d)
        for (flow, stage), ds in by.items():
            print(f"  {flow:11s} {stage:44s} median {int(statistics.median(ds)):6d} ms  n={len(ds)}  max {max(ds)}")


def backend_timings(since_line: int):
    try:
        out = subprocess.run(["grep", "-a", "⏱", "/tmp/backend.log"], capture_output=True, text=True).stdout
    except Exception:
        return
    lines = out.splitlines()[since_line:]
    print("\n== backend ⏱ lines during this run ==")
    for ln in lines:
        print("  " + ln.split("INFO:", 1)[-1].strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8081")
    ap.add_argument("--flows", default="play,guided,aivai,review,correction,practice,learn_nl,learn_fen")
    ap.add_argument("--moves", type=int, default=5)
    args = ap.parse_args()
    flows = set(args.flows.split(","))
    # This probe exists to time the REAL thing: every flow below spends
    # Gemini quota, several of them once per move. That is the point when
    # somebody is measuring latency, and pure waste when it runs as part of a
    # routine sweep - so it is opt-in, like the browser tools that call the
    # coach (tools/verify/live.mjs).
    if os.environ.get("LIVE_GEMINI") != "1":
        print("SKIP  the latency probe drives real Stockfish AND real Gemini.")
        print("      Set LIVE_GEMINI=1 to run it:")
        print("      LIVE_GEMINI=1 python tools/latency_probe.py")
        return
    try:
        before = len(subprocess.run(["grep", "-a", "⏱", "/tmp/backend.log"], capture_output=True, text=True).stdout.splitlines())
    except Exception:
        before = 0
    p = Probe(args.base)
    p.c.get("/api/status")  # mint the guest identity once, outside any timing
    if "play" in flows:
        print("\n-- Play, Guided Play off --"); p.flow_play(args.moves, guided=False)
    if "guided" in flows:
        print("\n-- Play, Guided Play on --"); p.flow_play(args.moves, guided=True)
    if "aivai" in flows:
        print("\n-- AI vs AI --"); p.flow_aivai(args.moves)
    gid = None
    if "review" in flows:
        print("\n-- Review import + scan --"); gid = p.flow_review()
    cid = None
    if "correction" in flows and gid:
        print("\n-- Correction --"); cid = p.flow_correction(gid)
    if "practice" in flows and cid:
        print("\n-- Fresh-position practice --"); p.flow_practice(cid)
    if "learn_nl" in flows or "learn_fen" in flows:
        print("\n-- Learn --"); p.flow_learn()
    p.summary()
    backend_timings(before)


if __name__ == "__main__":
    sys.exit(main())

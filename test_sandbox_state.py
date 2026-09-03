"""
Tests for the Sandbox Learner Mode foundation: the branching move tree and
the session store.

Run:
    /tmp/chessapp/bin/python test_sandbox_state.py

No Stockfish, no Gemini, no network - sandbox_state.py is pure by design, so
these are fast and deterministic. The engine/LLM path is covered separately
by test_sandbox_api.py.
"""

import chess

from sandbox_state import (
    IllegalSandboxMove,
    MoveTree,
    SandboxSession,
    SessionStore,
    SOURCE_AI,
    SOURCE_HUMAN,
    NARRATION_READY,
)

PASSED = 0
FAILED = 0


def check(label, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f" - {detail}" if detail else ""))


# --- the tree records a line -------------------------------------------

tree = MoveTree()
n1 = tree.play("e2e4", source=SOURCE_AI, explanation="Controls the centre.")
n2 = tree.play("e7e5", source=SOURCE_AI)
check("plays a line and records SAN", tree.line_san() == ["e4", "e5"], tree.line_san())
check("node knows who moved", (n1.mover, n2.mover) == ("white", "black"))
check("node knows whose turn it now is", (n1.turn, n2.turn) == ("black", "white"))
check("AI explanation is kept on the node", n1.explanation == "Controls the centre.")
check("board_at reconstructs the position",
      tree.board_at().fen() == chess.Board(n2.fen).fen())


# --- illegal moves are refused -----------------------------------------

try:
    tree.play("e2e4")
    check("illegal move is rejected", False, "no exception raised")
except IllegalSandboxMove:
    check("illegal move is rejected", True)

try:
    tree.play("not-a-move")
    check("malformed UCI is rejected", False, "no exception raised")
except IllegalSandboxMove:
    check("malformed UCI is rejected", True)


# --- branching: the whole point of the tree -----------------------------

tree.goto(n1.id)                      # rewind to just after 1. e4
alt = tree.play("c7c5", source=SOURCE_HUMAN)   # user takes over, plays the Sicilian
check("branching creates a sibling, not an overwrite",
      len(tree.children_of(n1.id)) == 2, len(tree.children_of(n1.id)))
check("the AI's original line still exists", n2.id in tree.nodes)
check("the new branch is current", tree.current_id == alt.id)
check("the branch has its own line", tree.line_san() == ["e4", "c5"], tree.line_san())
check("the old line is still walkable",
      tree.line_san(n2.id) == ["e4", "e5"], tree.line_san(n2.id))
check("source records who played it", alt.source == SOURCE_HUMAN)


# --- replaying a known move reuses its node -----------------------------

before = len(tree.nodes)
tree.goto(n1.id)
again = tree.play("c7c5")
check("replaying an explored move does not duplicate it",
      again.id == alt.id and len(tree.nodes) == before)


# --- alternatives at a node --------------------------------------------

sibs = tree.siblings_of(n2.id)
check("siblings expose the alternatives tried from the same position",
      [s.san for s in sibs] == ["c5"], [s.san for s in sibs])
check("the root has no siblings", tree.siblings_of(tree.root_id) == [])


# --- navigation ---------------------------------------------------------

tree.goto(alt.id)
tree.back()
check("back steps one half-move", tree.current_id == n1.id)
tree.forward()
check("forward follows the most recent branch", tree.current_id == alt.id)
tree.goto(tree.root_id)
tree.back()
check("back at the root is a no-op", tree.current_id == tree.root_id)


# --- narration is addressed by node id, and survives rewinding ----------

ok = tree.attach_narration(n2.id, "Black mirrors in the centre.", NARRATION_READY)
check("narration attaches to its node", ok and tree.get(n2.id).narration_status == NARRATION_READY)
check("narration landed on the right node, not the current one",
      tree.get(n2.id).narration == "Black mirrors in the centre."
      and tree.get(alt.id).narration is None)
check("narration for a vanished node is dropped, not raised",
      tree.attach_narration("nosuchnode", "x", NARRATION_READY) is False)


# --- custom start positions --------------------------------------------

endgame = "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"
t2 = MoveTree(endgame)
check("custom start FEN is accepted", t2.board_at().fen() == chess.Board(endgame).fen())
check("root is a setup node with no move", t2.current.move is None)

try:
    MoveTree("8/8/8/8/8/8/8/8 w - - 0 1")   # no kings
    check("invalid start FEN is rejected", False, "no exception raised")
except ValueError:
    check("invalid start FEN is rejected", True)


# --- sessions are isolated from each other -----------------------------

store = SessionStore()
a = store.create(title="A")
b = store.create(title="B")
a.tree.play("d2d4")
check("two sessions get different ids", a.id != b.id)
check("one session's move does not touch the other",
      a.tree.line_san() == ["d4"] and b.tree.line_san() == [])
check("sessions are retrievable by id", store.get(a.id) is a and store.get(b.id) is b)
check("an unknown session id returns None", store.get("nope") is None)
check("deleting removes only that session",
      store.delete(a.id) and store.get(a.id) is None and store.get(b.id) is b)
check("deleting twice reports the second as absent", store.delete(a.id) is False)


# --- sessions hold nothing that could reach the learning DB -------------

session = SandboxSession("sid", difficulty=12)
leaky = [attr for attr in vars(session)
         if any(word in attr.lower() for word in ("game_id", "learning", "epoch"))]
check("a session holds no real-game / learning-DB handles", leaky == [], leaky)
check("session carries its own difficulty", session.difficulty == 12)
check("session tracks last move per colour independently",
      session.last_move_by_color == {"white": None, "black": None})


# --- session eviction, so a public deploy cannot leak memory ------------

small = SessionStore(max_sessions=3)
kept = [small.create() for _ in range(5)]
check("session count is capped at max_sessions", small.count() <= 3, small.count())
check("the most recent session survives eviction", small.get(kept[-1].id) is not None)

expiring = SessionStore(ttl_seconds=0)
old = expiring.create()
expiring.create()          # any store operation sweeps
check("idle sessions past their TTL are swept", expiring.get(old.id) is None)


# --- serialization shape the frontend will consume ----------------------

payload = a.tree.to_dict()
check("tree serializes with root/current/nodes/line",
      set(payload) == {"root_id", "current_id", "nodes", "line"}, set(payload))
check("every node id maps to itself in the serialized nodes",
      all(nid == n["id"] for nid, n in payload["nodes"].items()))
check("the serialized line runs root-first to current",
      payload["line"][0] == payload["root_id"] and payload["line"][-1] == payload["current_id"])

summary = b.to_dict()
check("session summary carries fen/turn/legal_moves",
      {"fen", "turn", "legal_moves", "session_id"} <= set(summary), set(summary))


print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

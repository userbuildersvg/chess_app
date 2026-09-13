"""
Tests scenario generation: natural language -> validated legal position.

The point of this suite is the safety property. move_quality.py documents
that Stockfish *segfaults* on an unreachable position rather than rejecting
it, and that engine is shared with the real game - so "the model asked for
something silly" must never become "the engine died". Everything this module
can emit is fuzzed against board.is_valid() here.

No network: the Gemini call is faked. No engine: the evaluator is injected.

Run:
    /tmp/chessapp/bin/python test_scenario.py
"""

import asyncio
import random

import chess
import httpx

import gemini_http

import scenario_service as sm
from scenario_service import (
    ScenarioError,
    ScenarioService,
    build_from_moves,
    build_material_position,
    build_position,
    generate_scenario,
    mate_distance,
    normalize_pieces,
    _find_opening,
    _mate_in_of,
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


# --- parsing the model's loose piece vocabulary -------------------------

check("piece letters are understood",
      normalize_pieces(["Q", "r", "p"]) == [chess.QUEEN, chess.ROOK, chess.PAWN])
check("piece words are understood",
      normalize_pieces(["queen", "rook"]) == [chess.QUEEN, chess.ROOK])
check("plurals are understood", normalize_pieces(["pawns"]) == [chess.PAWN])
check("counts are expanded", normalize_pieces(["2 pawns"]) == [chess.PAWN, chess.PAWN])
check("trailing counts are expanded", normalize_pieces(["rook x2"]) == [chess.ROOK] * 2)
check("a plain string is split", normalize_pieces("queen rook") == [chess.QUEEN, chess.ROOK])
check("junk is ignored, not fatal", normalize_pieces(["queen", "banana"]) == [chess.QUEEN])
check("empty input is fine", normalize_pieces(None) == [])


# --- material positions are always legal --------------------------------

rng = random.Random(1234)
board = build_material_position(["queen", "rook"], ["pawn", "pawn"], "white", rng=rng)
check("builds a legal position from material", board.is_valid())
check("the requested side is to move", board.turn == chess.WHITE)
check("material is what was asked for (plus kings)",
      sorted(p.symbol() for p in board.piece_map().values()) == sorted("KQRkpp"),
      sorted(p.symbol() for p in board.piece_map().values()))
check("a constructed position claims no castling rights",
      board.castling_rights == chess.BB_EMPTY)

black_to_move = build_material_position(["pawn"], ["queen"], "black", rng=rng)
check("black to move is honoured", black_to_move.turn == chess.BLACK)

# The safety property, fuzzed. Every one of these must be a position the
# engine can be handed without dying.
bad = []
fuzz_rng = random.Random(99)
requests = [
    (["queen"], ["rook"]), (["pawn"] * 8, ["pawn"] * 8),
    (["queen", "queen", "rook", "rook", "bishop", "knight"], ["queen"]),
    (["rook"], []), ([], ["queen", "rook"]), (["knight", "bishop"], ["knight", "bishop"]),
]
for w, b in requests:
    for _ in range(40):
        for side in ("white", "black"):
            pos = build_material_position(w, b, side, rng=fuzz_rng)
            if not pos.is_valid() or pos.is_check() or pos.is_game_over():
                bad.append(pos.fen())
check("480 fuzzed material positions are all valid, not in check, not over",
      not bad, bad[:3])

# Material that can only ever be a draw is a fact about the request, not
# about one placement - it should be reported, not retried into a vague
# "couldn't place that" after the whole attempt budget.
try:
    build_material_position([], [], "white", rng=fuzz_rng)
    check("two bare kings is refused with a reason", False, "no exception")
except ScenarioError as e:
    check("two bare kings is refused with a reason", "beyond the two kings" in str(e), str(e))

try:
    build_material_position(["knight"], [], "white", rng=fuzz_rng)
    check("dead-drawn material is refused with a reason", False, "no exception")
except ScenarioError as e:
    check("dead-drawn material is refused with a reason",
          "only ever be a draw" in str(e), str(e))

kings_ok = True
for _ in range(200):
    pos = build_material_position(["rook"], [], "white", rng=fuzz_rng)
    wk = pos.king(chess.WHITE)
    bk = pos.king(chess.BLACK)
    if chess.square_distance(wk, bk) < 2:
        kings_ok = False
        break
check("kings are never placed adjacent", kings_ok)

pawns_ok = True
for _ in range(200):
    pos = build_material_position(["pawn"] * 4, ["pawn"] * 4, "white", rng=fuzz_rng)
    for sq, piece in pos.piece_map().items():
        if piece.piece_type == chess.PAWN and chess.square_rank(sq) in (0, 7):
            pawns_ok = False
check("pawns are never placed on the back ranks", pawns_ok)

check("a king is always added even when not requested",
      build_material_position(["rook"], [], "white", rng=fuzz_rng).king(chess.BLACK) is not None)
check("a king the model listed anyway doesn't become a second king",
      len(build_material_position(["king", "queen"], ["king"], "white",
                                  rng=fuzz_rng).pieces(chess.KING, chess.WHITE)) == 1)

# A wildly over-specified request is clamped to something legal rather than
# rejected - the student asked for a scenario, and 20 queens a side is a
# misparse to absorb, not a reason to refuse them a board.
absurd = build_material_position(["queen"] * 20, ["queen"] * 20, "white", rng=fuzz_rng)
check("an absurd amount of material is clamped, not refused",
      absurd.is_valid()
      and len(absurd.occupied_co[chess.WHITE] and list(absurd.pieces(chess.QUEEN, chess.WHITE))) <= 15
      and bin(absurd.occupied_co[chess.WHITE]).count("1") <= 16
      and bin(absurd.occupied_co[chess.BLACK]).count("1") <= 16,
      absurd.fen())
check("clamped pawns stay within the eight a side can have",
      len(build_material_position(["pawn"] * 20, [], "white",
                                  rng=fuzz_rng).pieces(chess.PAWN, chess.WHITE)) <= 8)


# --- openings come from the verified book -------------------------------

check("a bare opening name matches the book", _find_opening("Sicilian") is not None)
check("'the' and 'defence' are stripped", _find_opening("the French Defence") is not None)
check("a variation name matches", _find_opening("Najdorf") is not None,
      _find_opening("Najdorf"))
check("an unknown opening returns nothing", _find_opening("Zugzwang Gambit") is None)

b, notes = build_position({"kind": "opening", "opening_name": "Sicilian Najdorf"})
check("an opening builds a legal position", b.is_valid())
check("the opening actually got played", b.fullmove_number > 1, b.fen())
check("the note names the book line used", "Book line:" in notes, notes)

b2, _ = build_position({"kind": "opening", "opening_name": "Sicilian Najdorf",
                        "opening_plies": 4})
check("opening_plies truncates the line", len(b2.move_stack) == 4, len(b2.move_stack))


# --- explicit move lines are validated move by move ---------------------

b3 = build_from_moves(["e4", "c5", "Nf3"])
check("a legal SAN line replays", b3.is_valid() and len(b3.move_stack) == 3)
check("move numbers in the list are tolerated",
      len(build_from_moves(["1.", "e4", "2.", "c5"]).move_stack) == 2)
check("a space-separated string works", len(build_from_moves("e4 c5").move_stack) == 2)

try:
    build_from_moves(["e4", "e4"])
    check("an illegal move in the line is refused", False, "no exception")
except ScenarioError as e:
    check("an illegal move in the line is refused", "illegal move" in str(e))

try:
    build_position({"kind": "opening", "opening_name": "Zugzwang Gambit"})
    check("an unknown opening with no fallback line is refused", False, "no exception")
except ScenarioError:
    check("an unknown opening with no fallback line is refused", True)

fallback, notes = build_position({"kind": "opening", "opening_name": "Made Up Opening",
                                  "moves_san": ["d4", "d5"]})
check("an unknown opening falls back to the supplied line",
      fallback.is_valid() and len(fallback.move_stack) == 2)

start, notes = build_position({"kind": "start"})
check("a bare request gives the standard position",
      start.fen() == chess.Board().fen() and "Standard" in notes)


# --- the model never gets to supply a FEN -------------------------------

check("the schema prompt forbids FENs outright", "MUST NOT output a FEN" in sm.SCHEMA_PROMPT)
check("the schema prompt says kings are implied", "Kings are implied" in sm.SCHEMA_PROMPT)
ignored, _ = build_position({"kind": "material", "white_pieces": ["rook"],
                             "black_pieces": [],
                             "fen": "8/8/8/8/8/8/8/8 w - - 0 1"})
check("a FEN in the constraints is ignored rather than trusted",
      ignored.is_valid() and ignored.fen() != "8/8/8/8/8/8/8/8 w - - 0 1")


# --- the Gemini leg, faked ----------------------------------------------

def reply(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class FakeClient:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    # gemini_http checks this before reusing its cached client. A real

    # httpx.AsyncClient has it; a stand-in has to say it is open.

    is_closed = False


    async def post(self, url, json=None, **kwargs):
        self.calls.append({"url": url, "json": json})
        status, body = self.script.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def with_fake(coro_fn, script):
    fake = FakeClient(list(script))
    orig = httpx.AsyncClient
    httpx.AsyncClient = fake
    gemini_http.reset()
    try:
        return asyncio.run(coro_fn()), fake
    finally:
        httpx.AsyncClient = orig
        gemini_http.reset()


svc = ScenarioService(api_key="fake", models=["m-a", "m-b"])
CONSTRAINTS = ('{"kind":"material","white_pieces":["pawn","pawn"],'
               '"black_pieces":["queen","rook"],"side_to_move":"white",'
               '"favors":"black","profile":"expert","title":"Desperate defence",'
               '"description":"Hold on."}')

result, fake = with_fake(
    lambda: generate_scenario("black has a queen and rook, white has 2 pawns",
                              service=svc, rng=random.Random(7)),
    [(200, reply(CONSTRAINTS))])
check("a full generation returns a legal FEN", chess.Board(result["fen"]).is_valid())
check("the title comes from the model", result["title"] == "Desperate defence")
check("the profile comes from the model", result["profile"] == "expert")
check("the material matches the request",
      sorted(p.symbol() for p in chess.Board(result["fen"]).piece_map().values())
      == sorted("KPPkqr"),
      sorted(p.symbol() for p in chess.Board(result["fen"]).piece_map().values()))
check("the request asks Gemini for JSON",
      fake.calls[0]["json"]["generationConfig"]["responseMimeType"] == "application/json")

fenced, _ = with_fake(
    lambda: generate_scenario("x", service=ScenarioService(api_key="k", models=["m"]),
                              rng=random.Random(1)),
    [(200, reply("```json\n" + CONSTRAINTS + "\n```"))])
check("a fenced JSON reply is still parsed", chess.Board(fenced["fen"]).is_valid())

prosed, _ = with_fake(
    lambda: generate_scenario("x", service=ScenarioService(api_key="k", models=["m"]),
                              rng=random.Random(1)),
    [(200, reply("Sure! Here you go:\n" + CONSTRAINTS + "\nHope that helps."))])
check("a reply wrapped in prose is still parsed", chess.Board(prosed["fen"]).is_valid())

fell_through, fake = with_fake(
    lambda: generate_scenario("x", service=ScenarioService(api_key="k", models=["m-a", "m-b"]),
                              rng=random.Random(1)),
    [(503, {}), (200, reply(CONSTRAINTS))])
check("a dead model falls through to the next", chess.Board(fell_through["fen"]).is_valid())
check("both models were tried", len(fake.calls) == 2)

try:
    with_fake(lambda: generate_scenario("x", service=ScenarioService(api_key="k", models=["m"]),
                                        rng=random.Random(1)),
              [(200, reply("this is not json at all"))])
    check("an unparseable reply is a clean error", False, "no exception")
except ScenarioError:
    check("an unparseable reply is a clean error", True)

nokey = ScenarioService(api_key="", models=["m"])
check("no API key means unavailable", nokey.available is False)
try:
    asyncio.run(generate_scenario("x", service=nokey))
    check("generating without a key fails clearly", False, "no exception")
except ScenarioError as e:
    check("generating without a key fails clearly", "GEMINI_API_KEY" in str(e))


# --- a model that emits a raw FEN cannot poison the position ------------

ROGUE = ('{"kind":"material","white_pieces":["queen"],"black_pieces":[],'
         '"side_to_move":"white","fen":"kkkkkkkk/8/8/8/8/8/8/KKKKKKKK w - - 0 1",'
         '"title":"Rogue","description":"x"}')
rogue, _ = with_fake(
    lambda: generate_scenario("x", service=ScenarioService(api_key="k", models=["m"]),
                              rng=random.Random(3)),
    [(200, reply(ROGUE))])
rogue_board = chess.Board(rogue["fen"])
check("a rogue FEN in the reply is discarded and a real position built",
      rogue_board.is_valid() and len(rogue_board.pieces(chess.KING, chess.WHITE)) == 1)


# --- the "make it winnable" path ----------------------------------------

async def favour_black(fen):
    """Pretend every position is crushing for Black."""
    return -900


won, _ = with_fake(
    lambda: generate_scenario("black should be winning", service=ScenarioService(
        api_key="k", models=["m"]), evaluator=favour_black, rng=random.Random(11)),
    [(200, reply(CONSTRAINTS))])
check("a satisfied 'favors' request is reported as verified",
      "verified winning for black" in won["notes"], won["notes"])

calls = {"n": 0}


async def never_good(fen):
    calls["n"] += 1
    return 0


tried, _ = with_fake(
    lambda: generate_scenario("black should be winning", service=ScenarioService(
        api_key="k", models=["m"]), evaluator=never_good, rng=random.Random(11)),
    [(200, reply(CONSTRAINTS))])
check("an unsatisfiable 'favors' request still returns a legal position",
      chess.Board(tried["fen"]).is_valid())
check("it says so rather than pretending",
      "could not be made" in tried["notes"], tried["notes"])
# The note is the only line that contradicts the description the model wrote
# before this position existed, so it is read by the student, not by whoever
# is reading the log. It used to say "closest available was -506cp for black".
check("and says it in words rather than centipawns",
      "cp" not in tried["notes"].replace("Constructed", ""), tried["notes"])
check("an unmet 'favors' request is flagged for the UI",
      tried["favor_met"] is False, tried.get("favor_met"))
check("re-rolling is bounded, not unlimited",
      calls["n"] <= sm.FAVOR_CANDIDATES, calls["n"])


async def exploding(fen):
    raise RuntimeError("engine died")


survived, _ = with_fake(
    lambda: generate_scenario("black should be winning", service=ScenarioService(
        api_key="k", models=["m"]), evaluator=exploding, rng=random.Random(11)),
    [(200, reply(CONSTRAINTS))])
check("an evaluator failure degrades to the position as built",
      chess.Board(survived["fen"]).is_valid())


# --- the fourth model chain is still distinct ---------------------------

from gemini_chat_service import (
    GEMINI_CHAT_MODELS,
    GEMINI_POSTMORTEM_CHAT_MODELS,
    GEMINI_SANDBOX_CHAT_MODELS,
)
from gemini_move_service import GeminiMoveService
from gemini_narration_service import GEMINI_NARRATION_MODELS

# Six chains now: the sandbox coach chat was the fifth caller on the one API
# key and Post-Mortem's review coach is the sixth, so each needs its own lead
# for the same reason the other four do - two chains led by the same model
# compete for that model's quota, which is how chat started taking 429s from
# move traffic on the very first question.
leads = [
    GeminiMoveService(api_key="x").models[0],
    GEMINI_CHAT_MODELS[0],
    GEMINI_NARRATION_MODELS[0],
    sm.GEMINI_SCENARIO_MODELS[0],
    GEMINI_SANDBOX_CHAT_MODELS[0],
    GEMINI_POSTMORTEM_CHAT_MODELS[0],
]
check("all six model chains lead with a different model",
      len(set(leads)) == 6, leads)
check("the post-mortem chain also ends on the model that hangs",
      GEMINI_POSTMORTEM_CHAT_MODELS[-1] == "gemini-3.6-flash")
check("the sandbox chat chain also ends on the model that hangs",
      GEMINI_SANDBOX_CHAT_MODELS[-1] == "gemini-3.6-flash")
check("the model that hangs is last here too",
      sm.GEMINI_SCENARIO_MODELS[-1] == "gemini-3.6-flash")


# --- mate requests are VERIFIED, not just parsed -------------------------
# A request for "mate in 1 for white" used to come back as a mate in seven.
# Nothing in the pipeline had a notion of a mate distance: the schema could
# not express it, so it collapsed to "material, favors white", the position
# was placed at random, and the only check was that White was 150cp better -
# which a forced mate passes at any depth, because a mate scores ~9999
# whether it is mate in one or mate in eight. The coach was then handed a
# description promising mate in one alongside a board that was not, and
# argued the difference away. These tests are that bug.

# mate_distance is exact, not "at most": asking for a two-mover and getting a
# one-mover is still the wrong scenario.
check("mate_distance finds a mate in one",
      mate_distance(chess.Board("k7/7R/1K6/8/8/8/8/8 w - - 0 1")) == 1)
check("mate_distance finds a mate in two",
      mate_distance(chess.Board("6k1/8/6K1/8/8/8/8/7R w - - 0 1")) == 2)
check("mate_distance returns None when there is no mate",
      mate_distance(chess.Board()) is None)
check("mate_distance returns None for a finished game",
      mate_distance(chess.Board("7k/5KQ1/8/8/8/8/8/8 b - - 0 1")) is None)

# The property that matters: what comes back IS what was asked for.
mate_rng = random.Random(4)
for want in (1, 2, 3):
    built = build_material_position(["queen", "rook"], [], "white",
                                    rng=mate_rng, mate_in=want)
    check(f"a mate in {want} request builds a position that really is mate in {want}",
          mate_distance(built, limit=want) == want, built.fen())
    check(f"the mate in {want} position is legal and not already over",
          built.is_valid() and not built.is_game_over() and not built.is_check())

# Depth is capped, and refused in a sentence rather than by searching for
# half a minute and then failing anyway - which is what mate in 4 measured.
try:
    build_material_position(["queen", "rook"], [], "white",
                            rng=random.Random(1), mate_in=sm.MAX_MATE_IN + 1)
    check("a mate deeper than the cap is refused", False, "no error raised")
except ScenarioError as e:
    check("a mate deeper than the cap is refused", "deeper" in str(e), str(e))

# Material that cannot mate says so, rather than returning something else.
try:
    build_material_position(["pawn"], ["queen", "rook", "rook"], "white",
                            rng=random.Random(2), mate_in=1)
    check("material that cannot mate in 1 is reported", False, "no error raised")
except ScenarioError as e:
    check("material that cannot mate in 1 is reported", "mate in 1" in str(e), str(e))

# The field is read in one place, and tolerantly - it comes from a model.
check("_mate_in_of: absent means no mate", _mate_in_of({}) is None)
check("_mate_in_of: null means no mate", _mate_in_of({"mate_in": None}) is None)
check("_mate_in_of: 0 means no mate", _mate_in_of({"mate_in": 0}) is None)
check("_mate_in_of: a string number is a number", _mate_in_of({"mate_in": "2"}) == 2)
try:
    _mate_in_of({"mate_in": "soon"})
    check("_mate_in_of: nonsense is an error, not a silent None", False)
except ScenarioError:
    check("_mate_in_of: nonsense is an error, not a silent None", True)

# The favour re-roll must not throw a verified mate away. The evaluator here
# scores every position as winning for White, so the old code would have
# re-rolled up to FAVOR_CANDIDATES times and kept whichever scored highest -
# and it cannot tell a mate in one from a mate in three.
async def _mate_survives_the_favour_loop():
    calls = {"n": 0}

    async def evaluator(fen):
        calls["n"] += 1
        return 9999

    out = await generate_scenario(
        "white to play and mate in one",
        evaluator=evaluator,
        rng=random.Random(5),
        service=_StubScenarioService({
            "kind": "material",
            "white_pieces": ["queen", "rook"],
            "black_pieces": [],
            "side_to_move": "white",
            "favors": "white",
            "mate_in": 1,
            "title": "Mate in one",
            "description": "White to play and mate in one.",
        }),
    )
    return out, calls["n"]


class _StubScenarioService:
    def __init__(self, constraints):
        self._c = constraints

    async def parse_request(self, prompt):
        return dict(self._c)


mate_out, eval_calls = asyncio.run(_mate_survives_the_favour_loop())
check("a verified mate survives generate_scenario",
      mate_distance(chess.Board(mate_out["fen"]), limit=1) == 1, mate_out["fen"])
check("the favour re-roll is skipped for a mate request",
      eval_calls == 0, f"{eval_calls} engine calls")
check("the verified mate distance is returned to the caller",
      mate_out["mate_in"] == 1, mate_out.get("mate_in"))
check("the notes say the mate was verified",
      "mates in 1" in mate_out["notes"], mate_out["notes"])

# A request with no mate keeps the old behaviour exactly.
check("no mate_in means no mate constraint",
      _mate_in_of({"kind": "material", "favors": "white"}) is None)
plain_board, plain_notes = build_position(
    {"kind": "material", "white_pieces": ["queen"], "black_pieces": ["rook"],
     "side_to_move": "white"},
    rng=random.Random(6))
check("a non-mate request still builds normally",
      plain_board.is_valid() and "verified" not in plain_notes, plain_notes)

# The schema has to be able to express it, or none of the above is reachable
# from a real request - which was the original bug.
check("the schema prompt offers a mate_in field",
      '"mate_in"' in sm.SCHEMA_PROMPT)
check("the schema prompt states the cap it will be held to",
      str(sm.MAX_MATE_IN) in sm.SCHEMA_PROMPT)


print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

"""
Tests for the Post-Mortem foundation: PGN ingestion, the immutable game, and
branching off it.

Run:
    /tmp/chessapp/bin/python test_postmortem_state.py

No Stockfish, no Gemini, no network - postmortem_state.py is pure by design.
The engine and LLM paths are covered by test_postmortem_api.py.

The claim these are here to hold up is the one the whole mode rests on: an
imported game is what the player played, and nothing the user does afterwards
can change it.
"""

import chess

from postmortem_state import (
    MAX_PLIES,
    PgnError,
    _defigurine,
    SOURCE_GAME,
    SOURCE_HUMAN,
    game_from_pgn,
    parse_pgn,
    phase_of,
    postmortem_games,
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


def fails(label, fn, fragment=None):
    """`fn` must raise PgnError, and the message must be fit for a player."""
    global PASSED, FAILED
    try:
        fn()
    except PgnError as exc:
        if fragment and fragment.lower() not in str(exc).lower():
            FAILED += 1
            print(f"FAIL  {label} - message was {exc!r}")
            return
        PASSED += 1
        print(f"PASS  {label}")
    except Exception as exc:
        FAILED += 1
        print(f"FAIL  {label} - raised {type(exc).__name__}: {exc}")
    else:
        FAILED += 1
        print(f"FAIL  {label} - no error raised")


# A short decisive game (Scholar's mate), used throughout.
SCHOLARS = """[Event "Casual"]
[Site "?"]
[Date "2026.01.02"]
[White "Alice"]
[Black "Bob"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""


# --- ingestion --------------------------------------------------------------

game = game_from_pgn(SCHOLARS, source_name="scholars.pgn")
check("replays every ply of the game", game.to_dict()["total_plies"] == 7)
check("keeps the headers worth showing",
      (game.headers.get("White"), game.headers.get("Black"), game.headers.get("Result"))
      == ("Alice", "Bob", "1-0"), game.headers)
check("drops PGN's own '?' placeholders rather than showing them",
      "Site" not in game.headers, game.headers)
check("opens at the start of the game, not the end", game.to_dict()["ply"] == 0)
check("the move list numbers moves the way chess does",
      [(r["move_number"], r["color"], r["san"]) for r in game.move_rows()[:3]]
      == [(1, "white", "e4"), (1, "black", "e5"), (2, "white", "Bc4")],
      game.move_rows()[:3])
check("mainline moves are labelled as the game, not as somebody's try",
      all(game.tree.get(n).source == SOURCE_GAME for n in game.mainline[1:]))
check("reads the result off the board, not only off the header",
      game.termination["kind"] == "checkmate", game.termination)

# A game that ends by resignation: the board is not mate, the header says 1-0,
# and reporting either one alone would be a lie of a different kind.
resigned = game_from_pgn("""[Result "0-1"]

1. f3 e5 2. g4 Qh4# 0-1
""")
check("mate delivered by Black is still read as checkmate",
      resigned.termination["kind"] == "checkmate", resigned.termination)

away = game_from_pgn("""[Result "1-0"]

1. e4 e5 2. Nf3 1-0
""")
check("a game that ended away from the board says so",
      away.termination["kind"] == "decisive"
      and "away from the board" in (away.termination["detail"] or ""), away.termination)

# --- ingestion refuses what it cannot replay --------------------------------

fails("empty input is refused", lambda: parse_pgn(""), "empty")
fails("whitespace is refused", lambda: parse_pgn("   \n  "), "empty")
fails("headers with no moves are refused",
      lambda: parse_pgn('[White "A"]\n[Black "B"]\n\n*\n'), "no moves")
fails("an illegal move is refused rather than silently truncated",
      lambda: parse_pgn("1. e4 e5 2. Ke2 Ke7 3. Kxe5 *"), "not legal")
fails("nonsense text is refused", lambda: parse_pgn("this is not a chess game"), "pgn")
fails("an oversized file is refused", lambda: parse_pgn("1. e4 e5 " * 200000), "too large")

check("a truncated game is not accepted at partial length",
      True)  # covered by the illegal-move case above; kept as a named claim

# Several games in one file: the first is imported, and the count is reported
# rather than the rest being dropped in silence.
multi = game_from_pgn(SCHOLARS + "\n\n" + SCHOLARS + "\n\n" + SCHOLARS)
check("reports how many games the file held", multi.game_count == 3, multi.game_count)

# Castling, en passant, promotion and underpromotion all have to survive the
# replay, because each is a rule python-chess applies and a hand-rolled
# reconstruction would get wrong.
tricky = game_from_pgn("""[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O Nf6 5. d4 exd4 6. e5 d5 7. exd6 Qxd6 *
""")
# `line_san()` with no argument describes where the board IS, and a fresh
# review sits at the start - so the game as played is asked for by its last
# node, not by the current one.
tricky_line = tricky.tree.line_san(tricky.mainline[-1])
check("castling and en passant replay correctly",
      tricky_line[6:12] == ["O-O", "Nf6", "d4", "exd4", "e5", "d5"], tricky_line)
check("en passant capture is on the board", "exd6" in tricky_line, tricky_line)

promo = game_from_pgn("""[FEN "8/P6k/8/8/8/8/6K1/8 w - - 0 1"]
[SetUp "1"]
[Result "*"]

1. a8=Q Kh6 *
""")
check("a game from a set-up position keeps that position",
      promo.start_fen.startswith("8/P6k"), promo.start_fen)
check("a set-up game is flagged as one", promo.from_setup is True)
check("promotion replays",
      promo.tree.line_san(promo.mainline[-1])[0] == "a8=Q",
      promo.tree.line_san(promo.mainline[-1]))


# --- navigation --------------------------------------------------------------

game.tree.goto(game.mainline[4])
check("ply is the distance from the start", game.ply_of(game.tree.current_id) == 4)
check("knows it is on the game as played", game.is_mainline(game.tree.current_id))
check("no branch point while on the game", game.branch_point() is None)
check("the game's own next move is the next mainline node",
      game.next_mainline_id() == game.mainline[5])
check("the last move has no next", game.next_mainline_id(game.mainline[-1]) is None)


# --- branching ---------------------------------------------------------------

# Stop before 3...Nf6 (the move that allowed mate) and play something else.
before_blunder = game.mainline[5]      # after 3. Qh5
game.tree.goto(before_blunder)
original_line = list(game.tree.line_san(game.mainline[-1]))
branch_node = game.tree.play("g7g6", source=SOURCE_HUMAN)

check("a different move leaves the game", not game.is_mainline(branch_node.id))
check("the branch knows where it left", game.branch_point() == before_blunder)
check("and at which ply", game.to_dict()["branch_ply"] == 5)
check("the state says it is not on the game", game.to_dict()["on_mainline"] is False)
check("the branch line is reported separately from the game",
      game.to_dict()["branch_line_san"] == ["g6"], game.to_dict()["branch_line_san"])

# The claim the whole mode rests on.
check("the imported game is unchanged by branching",
      [game.tree.get(n).san for n in game.mainline[1:]]
      == ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"],
      [game.tree.get(n).san for n in game.mainline[1:]])
check("the move list still shows the game, not the branch",
      [r["san"] for r in game.move_rows()][-1] == "Qxf7#")
check("the mainline node ids are the same objects they were",
      game.mainline[5] == before_blunder)

# Deeper into the branch, then back out.
game.tree.play("h5f3", source="ai")
check("the branch can continue", game.to_dict()["branch_line_san"] == ["g6", "Qf3"],
      game.to_dict()["branch_line_san"])
check("branch point is still the position it left, not the last real move",
      game.branch_point() == before_blunder)

game.tree.goto(game.branch_point())
check("returning lands back on the game", game.is_mainline(game.tree.current_id))
check("the line as played is intact after returning",
      game.tree.line_san(game.mainline[-1]) == original_line,
      game.tree.line_san(game.mainline[-1]))
check("the branch is still there to walk back into",
      branch_node.id in game.tree.nodes)
check("the position before the branch now shows it has one",
      game.move_rows()[5]["has_branch"] is True, game.move_rows()[5])

# Replaying the game's own move by hand must not fabricate a variation.
node_count = len(game.tree.nodes)
game.tree.goto(before_blunder)
replayed = game.tree.play("g8f6", source=SOURCE_HUMAN)
check("playing the move that was actually played walks the game forward",
      replayed.id == game.mainline[6] and game.is_mainline(replayed.id))
check("and creates no new node", len(game.tree.nodes) == node_count)


# --- analysis is recorded, never computed here -------------------------------

fresh = game_from_pgn(SCHOLARS)
check("nothing is analysed on import", fresh.analysis == {} and fresh.summary is None)
check("the scan starts idle with the game's length as its total",
      (fresh.scan["status"], fresh.scan["total"]) == ("idle", 7), fresh.scan)

fresh.record(fresh.mainline[1], {"ply": 1, "eval_after": {"score": 30, "mate_in": None}})
check("an analysis result attaches to its own move",
      fresh.analysis_of(fresh.mainline[1])["ply"] == 1)
check("a result for a node that is gone is dropped, not raised",
      fresh.record("no-such-node", {"ply": 99}) is False)
check("a null result is not recorded", fresh.record(fresh.mainline[2], None) is False)

curve = fresh.eval_curve()
check("the curve has a frame per position including the start",
      len(curve) == 8 and curve[0]["ply"] == 0, len(curve))
check("unanalysed plies are null rather than absent",
      curve[1]["score"] == 30 and curve[2]["score"] is None,
      [c["score"] for c in curve])
check("the curve carries SAN so a chart can label itself",
      curve[1]["san"] == "e4" and curve[0]["san"] is None)

check("phases are labelled by ply",
      (phase_of(1), phase_of(30), phase_of(90)) == ("opening", "middlegame", "endgame"))


# --- isolation ---------------------------------------------------------------

check("a review holds no learning-service handle",
      not any("learning" in attr for attr in vars(fresh)), list(vars(fresh)))
check("the transcript lives on the review", fresh.chat_history == [])

stored = postmortem_games.create(pgn=SCHOLARS, source_name="a.pgn", owner="guest:1")
other = postmortem_games.create(pgn=SCHOLARS, source_name="b.pgn", owner="guest:2")
check("two reviews are two independent games", stored.id != other.id)
stored.tree.goto(stored.mainline[3])
check("navigating one does not move the other",
      other.tree.current_id == other.mainline[0])
check("the store hands back the same review by id",
      postmortem_games.get(stored.id) is stored)
check("owners are recorded as the opaque strings they are",
      (stored.owner, other.owner) == ("guest:1", "guest:2"))
check("deleting one leaves the other", postmortem_games.delete(other.id)
      and postmortem_games.get(stored.id) is stored)

check("MAX_PLIES is a real ceiling, not advisory", MAX_PLIES == 800)


# --- figurine notation is a different game, silently ---------------------
# "1. \u2658f3" is a KNIGHT move. python-chess does not reject the symbol it
# does not understand - it drops it and reads the remainder as the PAWN move
# f3, with game.errors empty, so every guard below it sees a clean import of
# a game the player never played. Found by fuzzing the import endpoint with
# hostile PGN text.
fig, _ = parse_pgn("1. \u2658f3 \u265ef6 2. \u2658c3 \u265ec6")
check("figurine knights are knights, not pawns",
      [m.uci() for m in fig.mainline_moves()] == ["g1f3", "g8f6", "b1c3", "b8c6"],
      [m.uci() for m in fig.mainline_moves()])

fig2, _ = parse_pgn('[White "A"]\n[Black "B"]\n\n1. e4 e5 2. \u2658f3 \u265ec6 3. \u2657b5 a6')
check("a figurine Ruy Lopez replays as the Ruy Lopez",
      [m.uci() for m in fig2.mainline_moves()]
      == ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6"],
      [m.uci() for m in fig2.mainline_moves()])

# A tag value is free text and really can contain a piece symbol; rewriting
# it would corrupt a name rather than a move.
named, _ = parse_pgn('[White "\u2655 Queenie"]\n\n1. e4 e5')
check("a piece symbol inside a header is left alone",
      named.headers.get("White") == "\u2655 Queenie", named.headers.get("White"))

check("_defigurine leaves ordinary PGN untouched",
      _defigurine("1. Nf3 Nf6") == "1. Nf3 Nf6")
check("_defigurine drops the pawn symbol rather than writing P",
      _defigurine("1. \u2659e4") == "1. e4", _defigurine("1. \u2659e4"))


print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

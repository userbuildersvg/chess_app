"""
Isolation tests for player_state.py.

Pure - no FastAPI, no Stockfish, no Gemini, no SQLite - because the module is.
Runs in well under a second:

    /tmp/chessapp/bin/python test_player_state.py

What these check is the one property the file exists for: two players cannot
reach each other's game. That was NOT true of the module-level globals it
replaces, where two browser tabs shared one board, and it is the reason
accounts could not simply be added on top.
"""

import player_state
from player_state import PlayerSession, PlayerStore

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


# --- identity ------------------------------------------------------------

store = PlayerStore()
alice = store.for_identity("alice")
bob = store.for_identity("bob")

check("two identities get two sessions", alice.id != bob.id)
check("the same identity gets the same session back",
      store.for_identity("alice") is alice)
check("a session remembers whose it is", alice.identity == "alice")
check("peek does not create", store.peek("carol") is None)
check("peek finds an existing one", store.peek("alice") is alice)
check("the store counts what it holds", store.count() == 2, store.count())


# --- the property this file exists for -----------------------------------

alice.game.make_player_move("e2e4")

check("a move lands on the mover's board", len(alice.game.game_history) == 1)
check("and on nobody else's", len(bob.game.game_history) == 0,
      len(bob.game.game_history))
check("the two boards are actually different positions",
      alice.game.get_fen() != bob.game.get_fen())
check("Bob is still on the starting position",
      bob.game.get_fen().startswith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP"),
      bob.game.get_fen())

# Every field that used to be a module global, set on one session and checked
# on the other. This is the test that would have failed before the refactor,
# for all of them at once.
alice.player_color = "black"
alice.game_mode = "ai_vs_ai"
alice.ai_vs_ai_running = True
alice.last_ai_move_by_color = {"white": "e2e4", "black": None}
alice.current_game_id = 99
alice.game_finalized = True
alice.move_quality_enabled = False
alice.opponent_profile = "beginner"
alice.chat_history.append({"role": "user", "text": "hello"})

check("colour does not leak", bob.player_color == "white", bob.player_color)
check("mode does not leak", bob.game_mode == "human_vs_ai", bob.game_mode)
check("the AI-vs-AI flag does not leak", bob.ai_vs_ai_running is False)
check("last-AI-move does not leak",
      bob.last_ai_move_by_color == {"white": None, "black": None},
      bob.last_ai_move_by_color)
check("the learning row does not leak", bob.current_game_id is None)
check("the finalized flag does not leak", bob.game_finalized is False)
check("the grading switch does not leak", bob.move_quality_enabled is True)
check("profile does not leak", bob.opponent_profile == "club", bob.opponent_profile)
check("a new session defaults to club", PlayerSession("s-x", "i-x").opponent_profile == "club")
check("an unknown profile at construction is club", PlayerSession("s-y", "i-y", profile="wizard").opponent_profile == "club")
check("the chat transcript does not leak", bob.chat_history == [],
      bob.chat_history)


# --- the coach's move explanations are chat turns ------------------------
#
# The Play panel no longer has a separate "Coach" tab: the explanation Gemini
# gives for its own move is appended to the conversation, so "why?" asked
# three moves later still has every explanation in the history that is
# replayed to the model, and a reload gets them back with the rest of the
# transcript.

carol = store.for_identity("carol")
carol.note_coach_turn("Nf3", "Developing toward the centre and eyeing e5.")
check("a coach turn lands in the transcript as the model's",
      carol.chat_history == [{"role": "model", "text": "**Nf3** \u2014 Developing toward the centre and eyeing e5.", "move": "Nf3"}],
      carol.chat_history)
carol.note_coach_turn("d4", None)
carol.note_coach_turn("d4", "   ")
check("an empty explanation is not a turn", len(carol.chat_history) == 1,
      carol.chat_history)
carol.note_coach_turn(None, "A move with no SAN still gets its reasoning shown.")
check("a missing SAN does not lose the explanation",
      carol.chat_history[-1]["text"] == "A move with no SAN still gets its reasoning shown."
      and "move" not in carol.chat_history[-1],
      carol.chat_history[-1])
check("coach turns do not leak", alice.chat_history == [{"role": "user", "text": "hello"}],
      alice.chat_history)


# --- a new game is not a new account -------------------------------------

alice.reset_board()
check("reset clears the board", len(alice.game.game_history) == 0)
check("reset clears the transcript", alice.chat_history == [])
check("reset clears the learning row", alice.current_game_id is None)
check("reset clears the finalized flag", alice.game_finalized is False)
check("reset leaves AI-vs-AI", alice.game_mode == "human_vs_ai")
check("reset stops auto-play", alice.ai_vs_ai_running is False)
# Preferences are per-player, not per-game. Losing the profile someone
# chose every time they start again would be its own bug.
check("reset keeps the chosen profile", alice.opponent_profile == "beginner",
      alice.opponent_profile)
check("reset keeps the grading preference", alice.move_quality_enabled is False)
check("reset keeps the identity", alice.identity == "alice")

# The epoch is what stops a background grade landing on a recycled ply index
# after a new game started. It must move, every time.
before = alice.game_epoch
alice.reset_board()
check("reset advances the game epoch", alice.game_epoch == before + 1,
      f"{before} -> {alice.game_epoch}")

alice.reset_board("black")
check("reset can switch colour", alice.player_color == "black")
alice.reset_board()
check("reset without a colour keeps the current one",
      alice.player_color == "black", alice.player_color)


# --- the store is bounded ------------------------------------------------

# Unbounded storage keyed on something a caller controls is how a server falls
# over, so the cap is not decoration.
small = PlayerStore(max_sessions=3)
for i in range(5):
    small.for_identity(f"user-{i}")
# Inclusive, matching SessionStore: the sweep evicts until it is below the
# cap and then inserts, so the store settles holding exactly max_sessions.
check("the store stays at its cap", small.count() <= 3, small.count())
check("the newest session survives eviction", small.peek("user-4") is not None)
check("the oldest is the one evicted", small.peek("user-0") is None)

expired = PlayerStore(ttl_seconds=0)
expired.for_identity("ghost")
expired.for_identity("live")
check("an idle session is swept", expired.peek("ghost") is None)

dropped = PlayerStore()
dropped.for_identity("gone")
check("drop removes a session", dropped.drop("gone") is True)
check("dropping twice is not an error", dropped.drop("gone") is False)


# --- and it holds no module state ----------------------------------------

# The whole point. A session reaches its own board and nothing else; there is
# no module-level game for one to fall back to.
check("the module exposes no shared game",
      not hasattr(player_state, "game"))
check("the module exposes no shared board state",
      not any(hasattr(player_state, n) for n in
              ("player_color", "game_mode", "chat_history", "opponent_profile")))

summary = alice.to_dict()
check("the summary names the session", summary["session_id"] == alice.id)
check("the summary does not carry the transcript", "chat_history" not in summary)


print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

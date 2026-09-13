"""
Guided Play (Play mode): the deterministic facts that ground the coach's
"Watch out" section, the prompt that asks for it, and the parsing that gets
it back out. Pure - no Stockfish, no network, no key.

The product rule under test throughout: Guided Play trains attention. It
may tell the player what to LOOK AT after the AI moves; it must never tell
them what to PLAY.
"""
import sys

import guided_play
import learning_events
from gemini_move_service import GeminiMoveService
from player_state import PlayerSession

# Italian game, white to move. Nf3-g5 hits f7 and h7; d2-d3 and d2-d4 are
# quiet. Black's king is on e8, rook on h8, so neither f7 nor h7 is loose.
FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
CANDIDATES = [
    {"move": "f3g5", "san": "Ng5", "score": 34, "mate_in": None},
    {"move": "d2d3", "san": "d3", "score": 22, "mate_in": None},
    {"move": "d2d4", "san": "d4", "score": 10, "mate_in": None},
]
# A position where the move leaves something genuinely hanging: white knight
# on e5 takes the undefended pawn on f7 with check... simpler - a queen sortie
# that attacks an unprotected piece. White Qd1-h5 in a Scholar's-mate setup
# hits e5 (defended by Nc6) and f7 (defended by Ke8), and the queen on h5 can
# be hit by ...g6 - none loose. Use a bare position instead.
LOOSE_FEN = "4k3/8/8/8/8/8/3R4/4K3 w - - 0 1"  # Rd2-d8 gives check; Rd2-d7 attacks nothing

results = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if not ok and detail else ""))
    results.append(ok)


# --- 1. candidate_facts: what a move does, from python-chess -----------------
facts = guided_play.candidate_facts(FEN, "f3g5")
check("Ng5 gives no check", facts["gives_check"] is False, str(facts))
check("Ng5 attacks the pawns on f7 and h7",
      sorted(facts["attacks"]) == ["pawn on f7", "pawn on h7"], str(facts))
check("nothing hangs after Ng5 (f7 and h7 are both defended)",
      facts["hanging"] == [], str(facts))
check("the knight on g5 is not itself loose", facts["mover_loose"] is False, str(facts))

facts = guided_play.candidate_facts(LOOSE_FEN, "d2d8")
check("Rd8+ gives check", facts["gives_check"] is True, str(facts))
check("a rook that lands next to the bare king is loose",
      facts["mover_loose"] is True, str(facts))

# A hanging piece: black queen on a5 undefended, white bishop d2 attacks it
# after Bc1-d2? Bishop on d2 attacks a5 along the diagonal d2-c3-b4-a5.
HANG_FEN = "4k3/8/8/q7/8/8/8/2B1K3 w - - 0 1"
facts = guided_play.candidate_facts(HANG_FEN, "c1d2")
check("Bd2 leaves the undefended queen on a5 hanging",
      facts["hanging"] == ["queen on a5"], str(facts))

# An illegal or malformed move must not raise - the shortlist is engine-
# validated, but a helper that can take the AI move down with it is worse
# than one that says nothing.
facts = guided_play.candidate_facts(FEN, "zz99")
check("a malformed move yields empty facts rather than an exception",
      facts == guided_play.EMPTY_FACTS, str(facts))

# --- 2. describe_candidates: the same facts as prompt text --------------------
text = guided_play.describe_candidates(FEN, CANDIDATES)
check("the description covers every candidate",
      all(c["move"] in text for c in CANDIDATES), text)
check("the description names what Ng5 attacks", "f7" in text and "h7" in text, text)
check("a quiet move is described as quiet",
      "d2d3" in text and ("nothing" in text.lower() or "no " in text.lower()), text)

# --- 3. split_watch_out: getting the section back out of the reply -----------
body, watch = guided_play.split_watch_out(
    "The knight hits f7 and h7 at once.\nWatch out: check whether f7 is still "
    "defended, and what the knight can jump to next."
)
check("the body is everything before the section", body == "The knight hits f7 and h7 at once.", body)
check("the section is returned without its label",
      watch == "check whether f7 is still defended, and what the knight can jump to next.", watch)

body, watch = guided_play.split_watch_out("**Watch out:** your e4 pawn is attacked.\n")
check("a bold label is tolerated and stripped", watch == "your e4 pawn is attacked.", repr(watch))
check("a reply that is only the section has an empty body", body == "", repr(body))

body, watch = guided_play.split_watch_out("Just an explanation, no coaching.")
check("no section means None", watch is None and body == "Just an explanation, no coaching.", repr((body, watch)))

body, watch = guided_play.split_watch_out("Idea.\nWATCH OUT - the d-file is open now.")
check("label matching is case-insensitive and accepts a dash", watch == "the d-file is open now.", repr(watch))

# --- 4. The prompt --------------------------------------------------------------
svc = GeminiMoveService(api_key="k")
standard = svc._build_prompt(FEN, CANDIDATES)
guided = svc._build_prompt(FEN, CANDIDATES, {"guided": True, "facts": guided_play.describe_candidates(FEN, CANDIDATES)})
check("the standard prompt does not ask for a Watch out section", "Watch out" not in standard)
check("the guided prompt asks for a Watch out section", "Watch out" in guided)
check("the guided prompt carries the engine facts", "f7" in guided and "h7" in guided)
check("the guided prompt forbids recommending a move",
      "do not" in guided.lower() and "recommend" in guided.lower(), guided)
check("both prompts keep the FEN and the whole shortlist",
      all(FEN in p and all(c["move"] in p for c in CANDIDATES) for p in (standard, guided)))
check("guided off is byte-identical to the prompt with no context at all",
      svc._build_prompt(FEN, CANDIDATES, {"guided": False}) == standard)

# --- 5. _parse keeps the section on its own line ------------------------------
move, expl = GeminiMoveService._parse(
    "f3g5\nThe knight jumps in to hit f7.\nWatch out: f7 is attacked twice now.",
    ["f3g5", "d2d3", "d2d4"],
)
check("the move is still found", move == "f3g5", move)
check("the section survives parsing on its own line",
      "\nWatch out: f7 is attacked twice now." in expl, repr(expl))
_, expl = GeminiMoveService._parse("d2d3\nQuiet and solid.", ["d2d3"])
check("a standard reply parses as before", expl == "Quiet and solid.", repr(expl))

# --- 6. The coach turn carries the section separately -------------------------
s = PlayerSession("sess-1", "guest:x")
s.note_coach_turn("Ng5", "The knight hits f7.", watch_out="Check whether f7 is defended.")
turn = s.chat_history[-1]
check("the turn keeps the body in text for replay and reload",
      turn["text"].startswith("**Ng5** — The knight hits f7."), turn["text"])
check("the turn's text ends with the section so the chat model sees it too",
      turn["text"].endswith("\n\nWatch out: Check whether f7 is defended."), turn["text"])
check("the section is its own key", turn.get("watch_out") == "Check whether f7 is defended.", str(turn))
s.note_coach_turn("d3", "Quiet.")
check("a turn without the section has no key", "watch_out" not in s.chat_history[-1])
s.note_coach_turn("d4", "Centre.", watch_out="   ")
check("a blank section is dropped", "watch_out" not in s.chat_history[-1] and s.chat_history[-1]["text"] == "**d4** — Centre.")

# --- 7. Instrumentation -----------------------------------------------------------
sink = learning_events.EventSink()
for name in ("guided_play_enabled", "guided_play_disabled",
             "ai_move_explanation_generated", "guided_watchout_generated"):
    check(f"event {name} is accepted", sink.emit(name, "guest:x", opponent_profile="club", approx_elo=1500, source_mode="Play"))
row = sink.recent(1)[0]
check("opponent_profile is an allowed property", row.get("opponent_profile") == "club" and row.get("approx_elo") == 1500, str(row))
check("the old difficulty integer is no longer an allowed property",
      "difficulty" not in learning_events.ALLOWED_PROPERTIES)
check("the decision record's fields are allowed",
      {"rank_in_pool", "rank_overall", "cpl", "n_candidates", "selected_by", "fallback_reason"} <= set(learning_events.ALLOWED_PROPERTIES))
check("guided is an allowed property",
      sink.emit("ai_move_explanation_generated", "guest:x", guided=True) and sink.recent(1)[0].get("guided") is True)

# --- 8. The preference is an account setting like the other five ------------
import settings_service
check("guidedPlay is accepted by the settings validator",
      settings_service.sanitise({"guidedPlay": True}) == {"guidedPlay": True})
check("guidedPlay rejects a non-boolean", settings_service.sanitise({"guidedPlay": "yes"}) == {})
check("guidedPlay defaults off", settings_service.DEFAULTS.get("guidedPlay") is False)

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

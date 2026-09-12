"""
Tests for the learning loop's pure half: the store, the taxonomy, the
diagnosis validator, and the event sink.

Run:
    /tmp/chessapp/bin/python test_learning_loop.py

No Stockfish, no Gemini, no network. The bank's chess claims are verified
against the real engine in test_retest_bank.py, and the HTTP flow end to end
is test_learning_loop_api.py.

The claims these hold up, in order of how much damage getting them wrong would
do:

  1. **One player never sees another's corrections.** The store is keyed by
     identity and there is no unkeyed read.
  2. **The model cannot assert a chess fact.** A diagnosis mentioning a move
     that is not legal in the position, or an evaluation the engine did not
     produce, is rejected rather than cleaned up.
  3. **Recurrence is a fact, not a feeling.** It happens when and only when
     two diagnoses carry the same controlled token.
  4. **The identity string is never written into an event.**
"""

import chess

import learning_events
import retest_bank
from diagnosis_service import (
    DiagnosisError,
    classify_from_evidence,
    fallback_diagnosis,
    unsupported_claims,
    validate,
)
from learning_loop import (
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    THEMES,
    CorrectionStore,
    PracticeAttempt,
    is_theme,
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


def rejects(label, raw, evidence, fragment=None):
    """`validate` must refuse this reply, and say why."""
    global PASSED, FAILED
    try:
        validate(raw, evidence)
    except DiagnosisError as exc:
        if fragment and fragment.lower() not in str(exc).lower():
            FAILED += 1
            print(f"FAIL  {label} - refused for the wrong reason: {exc}")
            return
        PASSED += 1
        print(f"PASS  {label}")
    except Exception as exc:
        FAILED += 1
        print(f"FAIL  {label} - raised {type(exc).__name__}: {exc}")
    else:
        FAILED += 1
        print(f"FAIL  {label} - it was accepted")


# A real evidence packet's shape, with real numbers. The position is after
# 1. e4 e5 2. Nf3 Nc6 3. Bb5, so `a6`, `Nf6` and `Bc5` are legal and `Qh5` is
# not (the queen is on d1 with the pawn gone from e2 - Qh5 IS legal here, so
# the illegal example below uses a move that genuinely is not).
FEN_BEFORE = "r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
FEN_AFTER = "r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"
EVIDENCE = {
    "ply": 5,
    "san": "a6",
    "uci": "a7a6",
    "color": "black",
    "phase": "opening",
    "fen_before": FEN_BEFORE,
    "fen_after": FEN_AFTER,
    "eval_before": {"score": 34, "mate_in": None},
    "eval_after": {"score": 41, "mate_in": None},
    "best_move": "g8f6",
    "best_san": "Nf6",
    "pv_san": ["Nf6", "O-O", "Be7"],
    "cpl": 7,
    "quality": {"label": "good", "cpl": 7},
    "depth": 12,
}


print("=== the taxonomy ===")
check("eight themes, and every one carries a check to make next time",
      len(THEMES) == 8 and all(t.get("check") for t in THEMES.values()))
check("a token outside the list is not a theme", not is_theme("SOMETHING_ELSE"))
check("a non-string is not a theme", not is_theme(None) and not is_theme(3))
check("every theme has a label and a description",
      all(t.get("label") and t.get("description") for t in THEMES.values()))


print("\n=== the store is keyed by identity ===")
store = CorrectionStore()
alice, bob = "guest:aaa", "guest:bbb"
card_a, recurred_a = store.upsert(
    alice, "KING_SAFETY", "castle", "the king stayed in the centre",
    "You left the king where the file was about to open.", 0.7, {"game_id": "g1"},
)
check("a first diagnosis creates a card", card_a is not None and not recurred_a)
check("...and the card is the player's", len(store.list_for(alice)) == 1)
check("...and nobody else's", store.list_for(bob) == [])
check("another player cannot read it by id", store.get(bob, card_a.id) is None)
check("the owner can", store.get(alice, card_a.id) is card_a)
check("the card does not carry the identity to the client",
      "player_id" not in card_a.to_dict())


print("\n=== recurrence is the same token, twice ===")
same, recurred_same = store.upsert(
    alice, "KING_SAFETY", "attack", "again with the king in the centre",
    "The same thing, two games later.", 0.8, {"game_id": "g2"},
)
check("the same theme recurs rather than making a second card", recurred_same)
check("...onto the same card", same.id == card_a.id)
check("...counting the occurrences", same.occurrence_count == 2)
check("...and keeping both pieces of evidence", len(same.evidence) == 2)
check("...and this player still has exactly one card", len(store.list_for(alice)) == 1)

other, recurred_other = store.upsert(
    alice, "PIECE_ACTIVITY", "improve a piece", "the rook never moved",
    "A different pattern.", 0.6, {"game_id": "g3"},
)
check("a different theme does NOT recur", not recurred_other)
check("...and makes a second card", len(store.list_for(alice)) == 2)

bob_card, bob_recurred = store.upsert(
    bob, "KING_SAFETY", "defend", "same theme, different player",
    "Bob's own.", 0.5, {"game_id": "g4"},
)
check("the same theme for a DIFFERENT player is not a recurrence", not bob_recurred)
check("...and does not touch the first player's count", same.occurrence_count == 2)

check("the correction rule comes from the taxonomy, not the caller",
      card_a.correction_rule == THEMES["KING_SAFETY"]["check"])

try:
    store.upsert(alice, "MADE_UP_THEME", "x", "y", "z", 0.5, {})
    check("an unknown theme is refused", False, "it was accepted")
except ValueError:
    check("an unknown theme is refused", True)


print("\n=== practice history ===")
check("a fresh card has no practice rate, rather than 0%",
      card_a.practice_summary["rate"] is None
      and card_a.practice_summary["attempted"] == 0)
store.record_attempt(alice, card_a.id, PracticeAttempt(
    card_a.id, FEN_BEFORE, "a7a6", ["g8f6"], passed=False, hints_used=1))
store.record_attempt(alice, card_a.id, PracticeAttempt(
    card_a.id, FEN_BEFORE, "g8f6", ["g8f6"], passed=True))
summary = card_a.practice_summary
check("attempts are counted", summary["attempted"] == 2)
check("passes are counted", summary["passed"] == 1)
check("the rate is the two of them", summary["rate"] == 0.5)
check("hints are counted", summary["hints_used"] == 1)
check("the most recent result is reported", summary["last_passed"] is True)
check("an attempt for a card the caller does not own is refused",
      store.record_attempt(bob, card_a.id, PracticeAttempt(
          card_a.id, FEN_BEFORE, "g8f6", ["g8f6"], passed=True)) is None)

check("a card can be accepted", store.set_status(alice, card_a.id, STATUS_ACCEPTED).status == STATUS_ACCEPTED)
check("a card can be rejected and is kept, not deleted",
      store.set_status(alice, card_a.id, STATUS_REJECTED).status == STATUS_REJECTED
      and store.get(alice, card_a.id) is not None)
check("another player cannot change its status", store.set_status(bob, card_a.id, STATUS_ACCEPTED) is None)


print("\n=== the validator: the model may interpret, not assert ===")
good = {
    "theme": "PIECE_ACTIVITY",
    "missed_factor": "The knight on g8 had not moved.",
    "diagnosis": "You wanted to question the bishop, but Nf6 developed and did the same job.",
    "confidence": 0.72,
    "uncertainty": "",
}
ok = validate(good, EVIDENCE)
check("a clean reply is accepted", ok["theme"] == "PIECE_ACTIVITY")
check("...and is marked as coming from the coach", ok["source"] == "coach")
check("...and an empty uncertainty becomes None", ok["uncertainty"] is None)

rejects("a theme outside the taxonomy is rejected",
        {**good, "theme": "BAD_KING_VIBES"}, EVIDENCE, "taxonomy")
rejects("a missing diagnosis is rejected",
        {**good, "diagnosis": ""}, EVIDENCE, "diagnosis")
rejects("a non-numeric confidence is rejected",
        {**good, "confidence": "high"}, EVIDENCE, "confidence")
rejects("a move that is not legal in the position is rejected",
        {**good, "diagnosis": "You should have played Nxe5 immediately."}, EVIDENCE, "not legal")
rejects("an evaluation the engine never produced is rejected",
        {**good, "diagnosis": "This drops you to -3.40 straight away."}, EVIDENCE, "evaluation")
rejects("a mate claim the engine never produced is rejected",
        {**good, "diagnosis": "There was mate in 4 available."}, EVIDENCE, "mate")
rejects("an unsupported claim hidden in missed_factor is rejected too",
        {**good, "missed_factor": "Qxf7 was hanging."}, EVIDENCE, "not legal")
rejects("a fabricated pawn move named as a move is rejected",
        {**good, "diagnosis": "You should have played c5 instead."}, EVIDENCE, "not legal")
rejects("...and so is one offered as an alternative",
        {**good, "diagnosis": "Consider h4 there."}, EVIDENCE, "not legal")
check("a bare square used as a REFERENCE is not treated as a move",
      validate({**good, "diagnosis": "The knight on g8 and the f7 square were both loose."},
               EVIDENCE)["diagnosis"])
check("a legal pawn move named as a move is allowed",
      validate({**good, "diagnosis": "You could have played a6 a move earlier."},
               EVIDENCE)["diagnosis"])
rejects("a reply that is not an object is rejected", ["nope"], EVIDENCE)

check("confidence above 1 is clamped rather than refused",
      validate({**good, "confidence": 4}, EVIDENCE)["confidence"] == 1.0)
check("negative confidence is clamped too",
      validate({**good, "confidence": -2}, EVIDENCE)["confidence"] == 0.0)

# A legal alternative is not fabrication - a coach may reasonably raise one.
check("a legal alternative move is allowed",
      validate({**good, "diagnosis": "Bc5 was also natural here."}, EVIDENCE)["diagnosis"])
check("the engine's own numbers are quotable",
      validate({**good, "diagnosis": "The engine had this at +0.34 before the move."},
               EVIDENCE)["diagnosis"])
check("unsupported_claims is silent on prose with no chess in it",
      unsupported_claims("You were trying to gain space, which is reasonable.", EVIDENCE) == [])
check("...and names the problem when there is one",
      any("not legal" in p for p in unsupported_claims("Play Rxh8 next time.", EVIDENCE)))


print("\n=== the fallback says less, and says it honestly ===")
fb = fallback_diagnosis(EVIDENCE, "I wanted to attack")
check("the fallback is marked as the engine's, not the coach's", fb["source"] == "engine")
check("...carries a theme from the taxonomy", is_theme(fb["theme"]))
check("...is not confident", fb["confidence"] <= 0.4)
check("...says the coach could not be reached", "coach could not be reached" in fb["diagnosis"])
check("...and makes no claim the evidence does not support",
      unsupported_claims(fb["diagnosis"] + " " + fb["missed_factor"], EVIDENCE) == [])

# The cost sentence is on the same scale the badges use (moveQuality.lossText):
# a mate-scale loss is not "9561 centipawns", which is not a quantity of anything.
fb_cp = fallback_diagnosis(dict(EVIDENCE, cpl=180, label="mistake"), "")
check("a centipawn cost reads in pawns", "about 1.8 pawns" in fb_cp["diagnosis"], fb_cp["diagnosis"])
fb_mate = fallback_diagnosis(dict(EVIDENCE, cpl=9561, label="blunder"), "")
check("a mate-scale cost is not quoted as centipawns",
      "centipawns" not in fb_mate["diagnosis"] and "forced win" in fb_mate["diagnosis"], fb_mate["diagnosis"])
fb_miss = fallback_diagnosis(dict(EVIDENCE, cpl=9561, quality={"label": "miss"}), "")
check("a missed mate says so", "missed a forced mate" in fb_miss["diagnosis"], fb_miss["diagnosis"])

# The deterministic classifier, on facts it can actually read.
check("a best move that gives check is a missed forcing move",
      classify_from_evidence({
          "fen_before": "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1",
          "best_move": "a1a8", "uci": "g1g2"}) == "FORCING_MOVE_MISSED")
check("a best move that is a quiet capture is a miscounted exchange",
      classify_from_evidence({
          "fen_before": "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
          "best_move": "e4d5", "uci": "g1f3"}) == "CAPTURE_RECALCULATION")
check("nonsense evidence classifies as the vague theme rather than raising",
      classify_from_evidence({"fen_before": "not a fen", "best_move": "zz99"}) == "TACTICAL_OVERLOOK")


print("\n=== the bank refuses to guess ===")
check("every bank entry names a single answer",
      all(e.get("best_uci") and e.get("best_san") for _, e in retest_bank.all_entries()))
check("every bank entry clears the minimum gap",
      all(e["gap"] >= retest_bank.MIN_GAP for _, e in retest_bank.all_entries()))
check("the answer is never in what the client is given",
      all("best_uci" not in retest_bank.public(e) and "best_san" not in retest_bank.public(e)
          and "gap" not in retest_bank.public(e)
          for _, e in retest_bank.all_entries()))
check("a theme with no certified position offers none",
      retest_bank.position_for("KING_SAFETY") is None
      and not retest_bank.has_position("KING_SAFETY"))
check("...and that is true of all four quiet themes",
      not any(retest_bank.has_position(t) for t in
              ("KING_SAFETY", "PIECE_ACTIVITY", "PREMATURE_ATTACK",
               "PLAN_BEFORE_OPPONENT_RESPONSE")))
check("a theme with positions offers one", retest_bank.position_for("FORCING_MOVE_MISSED") is not None)
check("selection is deterministic in the attempt number",
      retest_bank.position_for("FORCING_MOVE_MISSED", 1)
      == retest_bank.position_for("FORCING_MOVE_MISSED", 1))
check("...and rotates, so a second attempt is a different position",
      retest_bank.position_for("FORCING_MOVE_MISSED", 0)
      != retest_bank.position_for("FORCING_MOVE_MISSED", 1))
check("...and wraps rather than falling off the end",
      retest_bank.position_for("FORCING_MOVE_MISSED", 99) is not None)
check("TACTICAL_OVERLOOK borrows from the tactical banks",
      retest_bank.has_position("TACTICAL_OVERLOOK")
      and set(retest_bank.sources_for("TACTICAL_OVERLOOK"))
      == {"FORCING_MOVE_MISSED", "CAPTURE_RECALCULATION"})
entry = retest_bank.position_for("CAPTURE_RECALCULATION", 0)
check("the certified answer is correct", retest_bank.is_correct(entry, entry["best_uci"]))
check("...and anything else is not", not retest_bank.is_correct(entry, "a1a2"))
check("...including nothing at all", not retest_bank.is_correct(entry, ""))
check("every bank position is a legal, reachable position",
      all(chess.Board(e["fen"]).is_valid() for _, e in retest_bank.all_entries()))
check("every certified answer is legal in its position",
      all(chess.Move.from_uci(e["best_uci"]) in chess.Board(e["fen"]).legal_moves
          for _, e in retest_bank.all_entries()))
check("no position is already over",
      not any(chess.Board(e["fen"]).is_game_over() for _, e in retest_bank.all_entries()))
check("no prompt gives the motif away",
      not any(word in e["prompt"].lower()
              for _, e in retest_bank.all_entries()
              for word in ("check", "capture", "fork", "pin", "sacrifice", "mate")))


print("\n=== events carry no identity and no prose ===")
sink = learning_events.EventSink()
identity = "guest:deadbeefdeadbeef"
check("a known event is recorded", sink.emit("practice_passed", identity, theme="KING_SAFETY"))
check("an unknown event is refused", not sink.emit("something_made_up", identity))
row = sink.recent()[-1]
check("the identity string is not in the row", identity not in str(row))
check("...it is an actor HMAC instead", row["actor"] == learning_events.player_key(identity))
check("...which is stable within a process",
      learning_events.player_key(identity) == learning_events.player_key(identity))
check("...and differs between players",
      learning_events.player_key("guest:aaa") != learning_events.player_key("guest:bbb"))
check("the theme is kept, because the funnel needs it", row["theme"] == "KING_SAFETY")
sink.emit("hint_used", identity, note={"free": "text"})
check("a non-scalar property is dropped rather than stored",
      "note" not in sink.recent()[-1])
check("counts add up", sink.counts()["practice_passed"] == 1)
check("emitting never raises, whatever it is handed",
      sink.emit("practice_passed", None) is True)


print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

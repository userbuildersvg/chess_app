"""
Integration test for /api/learning-loop/*: the whole loop, end to end.

Real Stockfish and the real app wiring. The *diagnosis* model is faked, for
the same reason `test_postmortem_api.py` fakes the move decider: the test has
to be deterministic and must spend no Gemini quota. The validator it feeds is
the real one, so a faked reply that makes an unsupported claim is rejected
here exactly as a real one would be.

Run:
    DISABLE_LANGFLOW=true /tmp/chessapp/bin/python test_learning_loop_api.py

The claims:

  1. **The loop works from one end to the other**: import a game, open a
     decision, state an intent, get a card, try the better move as a real
     branch, take a re-test, and have the result recorded.
  2. **A card belongs to one player.** A second cookie jar is a second person
     and sees nothing of the first.
  3. **State survives the request that made it.** A card is still there on a
     later request with the same cookie - which is what "survives a refresh"
     means on the server side.
  4. **A recurrence is the same theme twice, and nothing else is.**
  5. **The re-test never leaks its answer** before the attempt, and refuses
     rather than inventing an exercise for a theme it cannot certify.
  6. **Failure is honest**: when the model is unavailable the card says it
     came from the engine.
"""

import chess
from fastapi.testclient import TestClient

import os as _os

# The closed beta gate is fail-closed by default (`beta_service.beta_required`),
# so with it left alone every request in this file would be answered 403 by
# `beta_gate.py` before reaching the route under test. Switched off here rather
# than worked around, because this suite is testing what the routes do and not
# who may reach them - that is `test_beta_access.py`, which asserts among other
# things that this default is ON when nobody says otherwise.
_os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")

import app
import diagnosis_service
import learning_loop
import learning_loop_api
import postmortem_api
import retest_bank

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


async def fake_decide(fen, color, *, difficulty=None, last_move=None, use_learning=True, learning=None):
    board = chess.Board(fen)
    return sorted(m.uci() for m in board.legal_moves)[0], "Fake reason.", "gemini"


postmortem_api.configure(fake_decide)


# --- the faked coach --------------------------------------------------------
#
# Swapped at the service boundary rather than mocked inside it, so everything
# below the swap - the validator, the fallback, the store - is the real code.
class FakeDiagnosis:
    """Returns whatever `script` says next. `None` means 'model unavailable'."""

    def __init__(self):
        self.script = []
        self.calls = []

    def available(self):
        return True

    async def diagnose(self, evidence, intent, prior=None):
        self.calls.append({"evidence": evidence, "intent": intent, "prior": prior})
        if not self.script:
            return False, "no script left"
        nxt = self.script.pop(0)
        if nxt is None:
            return False, "model unavailable"
        try:
            return True, diagnosis_service.validate(nxt, evidence)
        except diagnosis_service.DiagnosisError as exc:
            return False, f"rejected: {exc}"


fake = FakeDiagnosis()
learning_loop_api.diagnosis_service = fake

GAME = """[Event "Casual"]
[White "Alice"]
[Black "Bob"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""


def a_diagnosis(theme="TACTICAL_OVERLOOK", diagnosis="You went for the plan and missed what was on the board.",
                factor="The square was not covered.", confidence=0.66, uncertainty=""):
    return {
        "theme": theme, "diagnosis": diagnosis, "missed_factor": factor,
        "confidence": confidence, "uncertainty": uncertainty,
    }


with TestClient(app.app) as client:
    learning_loop.corrections.clear()

    print("=== reference data ===")
    themes = client.get("/api/learning-loop/themes").json()
    check("the taxonomy is served", len(themes["themes"]) == 8)
    check("...with the check to make next time on every theme",
          all(t["check"] for t in themes["themes"]))
    check("...and says honestly which themes can be practised",
          {t["id"]: t["practice_available"] for t in themes["themes"]}["KING_SAFETY"] is False)
    check("...and which can", {t["id"]: t["practice_available"] for t in themes["themes"]}["FORCING_MOVE_MISSED"])
    check("intent presets are served so the UI has no second copy",
          any(p["id"] == "unsure" for p in themes["intent_presets"]))

    print("\n=== a game, and a decision in it ===")
    imported = client.post("/api/postmortem/import", json={"pgn": GAME, "source_name": "g.pgn"})
    check("the game imports", imported.status_code == 200, imported.text[:120])
    state = imported.json()
    game_id = state["game_id"]
    # Step to Black's 3...Nf6, the move that allows mate.
    for _ in range(6):
        state = client.post(f"/api/postmortem/game/{game_id}/forward").json()
    node_id = state["current_id"]
    check("we are on a real move", state["ply"] == 6, state["ply"])

    print("\n=== intent is required before a diagnosis ===")
    no_intent = client.post("/api/learning-loop/diagnose", json={"game_id": game_id, "node_id": node_id})
    check("a diagnosis without an intent is refused", no_intent.status_code == 400)
    check("...and says why, in words a player can act on",
          "trying to do" in no_intent.json()["detail"], no_intent.json()["detail"])

    print("\n=== the diagnosis ===")
    fake.script = [a_diagnosis()]
    first = client.post("/api/learning-loop/diagnose", json={
        "game_id": game_id, "node_id": node_id,
        "intent": "I wanted to develop and hit the queen", "intent_preset": "improve_piece",
    })
    check("a diagnosis with an intent succeeds", first.status_code == 200, first.text[:200])
    body = first.json()
    card = body["correction"]
    check("...and makes a card", bool(card["id"]))
    check("...filed under a theme from the taxonomy", learning_loop.is_theme(card["theme"]))
    check("...carrying the player's own words back", card["player_intent"].startswith("I wanted to develop"))
    check("...with the taxonomy's check, not the model's",
          card["correction_rule"] == learning_loop.THEMES[card["theme"]]["check"])
    check("...marked as coming from the coach", body["diagnosis_source"] == "coach")
    check("...and it is the first time", body["recurred"] is False)
    check("the card never carries the identity to the client", "player_id" not in card)

    print("\n=== the evidence behind it is the engine's, and traceable ===")
    ev = card["evidence"][0]
    check("the evidence names the game it came from", ev["game_id"] == game_id)
    check("...and the move", ev["san"] == "Nf6", ev.get("san"))
    check("...and the position before it", chess.Board(ev["fen_before"]).is_valid())
    check("...and the engine's own preference", bool(ev["best_move"]))
    check("...and the depth it was searched to", isinstance(ev["depth"], int))
    check("...and what the player said they were doing", ev["intent"].startswith("I wanted to develop"))
    check("the engine's evaluation is a real number, not a placeholder",
          ev["eval_before"]["score"] is not None or ev["eval_before"]["mate_in"] is not None)
    check("the diagnosis prompt was handed the same packet",
          fake.calls[-1]["evidence"]["fen_before"] == ev["fen_before"])

    print("\n=== the model cannot smuggle a claim past the validator ===")
    fake.script = [a_diagnosis(diagnosis="You had Qxh8 winning on the spot.")]
    smuggled = client.post("/api/learning-loop/diagnose", json={
        "game_id": game_id, "node_id": node_id, "intent": "attack",
    })
    check("a reply naming an impossible move still returns a card",
          smuggled.status_code == 200, smuggled.text[:160])
    check("...but NOT the model's - it fell back to the engine",
          smuggled.json()["diagnosis_source"] == "engine")
    check("...and the card says the coach could not be reached",
          "coach could not be reached" in smuggled.json()["correction"]["diagnosis"])

    print("\n=== recurrence ===")
    before = client.get("/api/learning-loop/corrections").json()["count"]
    fake.script = [a_diagnosis(theme=card["theme"])]
    again = client.post("/api/learning-loop/diagnose", json={
        "game_id": game_id, "node_id": node_id, "intent": "same idea again",
    }).json()
    check("the same theme recurs rather than making a second card", again["recurred"] is True)
    check("...onto the same card", again["correction"]["id"] == card["id"])
    check("...and the count went up", again["correction"]["occurrence_count"] >= 2)
    check("...without adding a card", client.get("/api/learning-loop/corrections").json()["count"] == before)

    fake.script = [a_diagnosis(theme="KING_SAFETY")]
    different = client.post("/api/learning-loop/diagnose", json={
        "game_id": game_id, "node_id": node_id, "intent": "castle soon",
    }).json()
    check("a different theme does not claim to be a recurrence", different["recurred"] is False)
    check("...and makes its own card", different["correction"]["id"] != card["id"])

    print("\n=== the player can push back, and the card is kept ===")
    rejected = client.post(f"/api/learning-loop/correction/{card['id']}/status",
                           json={"status": "rejected"})
    check("a card can be rejected", rejected.status_code == 200)
    check("...and stays in the list rather than vanishing",
          any(c["id"] == card["id"] for c in client.get("/api/learning-loop/corrections").json()["corrections"]))
    check("an unknown status is refused",
          client.post(f"/api/learning-loop/correction/{card['id']}/status",
                      json={"status": "banana"}).status_code == 400)
    client.post(f"/api/learning-loop/correction/{card['id']}/status", json={"status": "accepted"})

    print("\n=== trying the better move is a real Post-Mortem branch ===")
    best_uci = body["best_move"]
    back = client.post(f"/api/postmortem/game/{game_id}/back").json()
    branched = client.post(f"/api/postmortem/game/{game_id}/branch", json={"move": best_uci})
    check("the engine's move can be played as a branch", branched.status_code == 200, branched.text[:160])
    check("...and the app knows it is a what-if, not the game",
          branched.json()["on_mainline"] is False)
    tried = client.post("/api/learning-loop/branch-tried",
                        json={"correction_id": card["id"], "uci": best_uci})
    check("...and the loop records that it matched the engine's move",
          tried.status_code == 200 and tried.json()["matched_best"] is True)
    other_move = client.post("/api/learning-loop/branch-tried",
                             json={"correction_id": card["id"], "uci": "a2a3"}).json()
    check("...and knows when it did not", other_move["matched_best"] is False)

    print("\n=== the re-test ===")
    practice = client.post("/api/learning-loop/practice/start", json={"correction_id": card["id"]}).json()
    check("a practisable theme gets a position", practice["available"] is True, practice)
    position = practice["position"]
    check("...that is a real position", chess.Board(position["fen"]).is_valid())
    check("...with a prompt", bool(position["prompt"]))
    check("the answer does NOT travel with the question",
          "best_uci" not in position and "best_san" not in position)
    check("no engine number travels with it either",
          "gap" not in position and "score" not in position and "eval" not in position)

    entry = retest_bank.position_for(card["theme"], 0)
    wrong = next(m.uci() for m in chess.Board(entry["fen"]).legal_moves if m.uci() != entry["best_uci"])
    missed = client.post("/api/learning-loop/practice/attempt", json={
        "correction_id": card["id"], "uci": wrong, "response_ms": 4200,
    }).json()
    check("a wrong move fails", missed["passed"] is False)
    check("...and only now is the answer revealed", missed["best_san"] == entry["best_san"])
    check("...and it is recorded", missed["correction"]["practice_summary"]["attempted"] == 1)

    hint = client.post("/api/learning-loop/practice/hint", json={"correction_id": card["id"]}).json()
    check("a hint says what kind of move, not which move", "The move you are looking for" in hint["hint"])
    check("...and does not name the move",
          retest_bank.position_for(card["theme"], 1)["best_san"] not in hint["hint"])

    second = retest_bank.position_for(card["theme"], 1)
    check("a second attempt is a different position", second["fen"] != entry["fen"])
    hit = client.post("/api/learning-loop/practice/attempt", json={
        "correction_id": card["id"], "uci": second["best_uci"], "hints_used": 1, "response_ms": 9000,
    }).json()
    check("the certified answer passes", hit["passed"] is True)
    summary = hit["correction"]["practice_summary"]
    check("...and the history accumulates rather than resetting", summary["attempted"] == 2)
    check("...counting the pass", summary["passed"] == 1)
    check("...and the hint", summary["hints_used"] == 1)

    illegal = client.post("/api/learning-loop/practice/attempt", json={
        "correction_id": card["id"], "uci": "e2e9",
    })
    check("a malformed move is refused", illegal.status_code == 400)
    not_legal = client.post("/api/learning-loop/practice/attempt", json={
        "correction_id": card["id"], "uci": "a1a8",
    })
    check("an illegal move is refused rather than graded", not_legal.status_code == 400)

    print("\n=== a theme with no certified position says so ===")
    ks_id = different["correction"]["id"]
    ks = client.post("/api/learning-loop/practice/start", json={"correction_id": ks_id}).json()
    check("king safety offers no exercise", ks["available"] is False)
    check("...and explains why rather than erroring",
          "verified" in ks["reason"] and "single best answer" in ks["reason"])

    print("\n=== state survives the request that made it ===")
    later = client.get("/api/learning-loop/corrections").json()
    check("the cards are still there on a later request", later["count"] >= 2)
    mine = next(c for c in later["corrections"] if c["id"] == card["id"])
    check("...with their practice history intact", mine["practice_summary"]["attempted"] == 2)
    check("...and their occurrence count", mine["occurrence_count"] >= 2)
    check("...and their evidence", len(mine["evidence"]) >= 2)

    print("\n=== another visitor is another person ===")
    with TestClient(app.app) as stranger:
        stranger.get("/api/status")  # mints a separate guest cookie
        theirs = stranger.get("/api/learning-loop/corrections").json()
        check("a second visitor has no corrections", theirs["count"] == 0, theirs)
        check("...and cannot read one by id",
              stranger.post("/api/learning-loop/practice/start",
                            json={"correction_id": card["id"]}).status_code == 404)
        check("...and cannot change its status",
              stranger.post(f"/api/learning-loop/correction/{card['id']}/status",
                            json={"status": "accepted"}).status_code == 404)
        check("...and cannot record an attempt against it",
              stranger.post("/api/learning-loop/practice/attempt",
                            json={"correction_id": card["id"], "uci": "e2e4"}).status_code == 404)
        check("...and cannot diagnose against the first player's game",
              stranger.post("/api/learning-loop/diagnose",
                            json={"game_id": game_id, "intent": "curious"}).status_code == 404)
    check("and the first player's cards are untouched by all that",
          client.get("/api/learning-loop/corrections").json()["count"] == later["count"])

    print("\n=== the funnel counted the loop ===")
    counts = client.get("/api/learning-loop/funnel").json()["counts"]
    for name in ("intent_submitted", "diagnosis_viewed", "correction_created",
                 "pattern_recurred", "practice_started", "practice_passed",
                 "practice_failed", "hint_used", "branch_matched_best"):
        check(f"funnel recorded {name}", counts.get(name, 0) >= 1, counts)
    check("the fallback was counted too", counts.get("diagnosis_fallback", 0) >= 1)
    check("an unknown event is refused",
          client.post("/api/learning-loop/events", json={"name": "made_up"}).status_code == 400)
    check("a known one is accepted",
          client.post("/api/learning-loop/events",
                      json={"name": "critical_decision_opened", "theme": "KING_SAFETY"}).status_code == 200)

    print("\n=== nothing leaked into the real game ===")
    status = client.get("/api/status").json()
    check("the player's own game is still the starting position",
          status["status"]["move_count"] == 0, status["status"]["move_count"])

print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)

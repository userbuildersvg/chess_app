"""Play move-explanation stance, payload separation and narrow cleanup."""

import sys

import guided_play
from gemini_move_service import GeminiMoveService, sanitise_move_explanation
from opponent_profiles import PROFILE_IDS, get_profile

FEN = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 2"
CANDIDATES = [
    {"move": "d8h4", "san": "Qh4#", "score": 10000, "mate_in": 1},
    {"move": "b8c6", "san": "Nc6", "score": 120, "mate_in": None},
]
results = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" ({detail})" if not ok and detail else ""))
    results.append(ok)


service = GeminiMoveService(api_key="test")
context = {
    "moving_color": "black",
    "profile": get_profile("club"),
    "move_history_san": ["f3", "e5", "g4"],
    "facts": guided_play.describe_candidates(FEN, CANDIDATES),
    "coach_style": {"bluntness": 9, "creativity": 9},
}
prompt = service._build_prompt(FEN, CANDIDATES, context)
for heading in ("SPEAKER IDENTITY", "CHESS EVIDENCE", "OPPONENT PROFILE", "COACH STYLE", "OUTPUT CONSTRAINTS"):
    check(f"prompt has separate {heading.lower()} block", heading in prompt)
check("prompt requires first-person present stance", "Use natural first person" in prompt and "present moment" in prompt)
check("prompt explicitly rejects As Black/White", "'As Black'" in prompt and "'As White'" in prompt)
check("prompt makes directness respectful", "High directness" in prompt and "not abuse" in prompt)
check("prompt preserves FEN and history", FEN in prompt and "f3, e5, g4" in prompt)
check("prompt preserves the candidate pool", all(item["move"] in prompt for item in CANDIDATES) and "ONLY moves" in prompt)
check("prompt grounds explanation in computed facts", "gives check" in prompt)
check("high creativity is phrasing-only", "Creativity: 9.0/10" in prompt and "must not affect which candidate" in prompt)

for profile_id in PROFILE_IDS:
    profile_prompt = service._build_prompt(FEN, CANDIDATES, {**context, "profile": get_profile(profile_id)})
    check(f"{profile_id} keeps the same speaker stance", "Use natural first person" in profile_prompt)
check("beginner is simple without becoming a character", "without playing a fool or a character" in service._build_prompt(FEN, CANDIDATES, {**context, "profile": get_profile("beginner")}))

guided = service._build_prompt(FEN, CANDIDATES, {**context, "guided": True})
standard = service._build_prompt(FEN, CANDIDATES, context)
check("Guided asks for Watch out", "Line 3" in guided and "Watch out:" in guided)
check("standard does not ask for Watch out", "Line 3" not in standard and "begins with exactly 'Watch out:'" not in standard)

for label, sample in (
    ("opening", "I'm challenging your center with ...e5 and making room to develop."),
    ("tactical", "I'm attacking your queen while adding pressure to the pinned knight."),
    ("check", "I'm checking your king directly, so you have to answer the threat."),
    ("checkmate", "I'm attacking your exposed king directly. This queen check leaves no escape square."),
):
    cleaned_sample = sanitise_move_explanation(sample, "black")
    check(f"{label} example keeps natural first-person stance",
          cleaned_sample.startswith("I") and cleaned_sample == sample)

bad = "As Black, I gladly accept the invitation to exploit White’s self-destruction in this bizarre opening."
clean = sanitise_move_explanation(bad, "black")
for phrase in ("As Black", "gladly", "self-destruction", "bizarre opening"):
    check(f"cleanup removes {phrase}", phrase.lower() not in clean.lower(), clean)
check("cleanup keeps first-person opponent voice", clean.startswith("I'm"), clean)

for sample in (
    "As White, I’m checking your king directly.",
    "If I were Black, I would attack the pinned piece.",
    "Black would develop the knight.",
    "I gladly punish you for that stupid move.",
    "Your terrible move invited disaster.",
):
    cleaned = sanitise_move_explanation(sample, "black")
    check("sample clears prohibited wording", not any(term in cleaned.lower() for term in (
        "as black", "as white", "if i were", "black would", "gladly", "stupid", "terrible", "invited disaster"
    )), cleaned)

move, explanation = service._parse(
    "d8h4\nAs Black, I gladly exploit White's self-destruction.\n"
    "Watch out: Do not make another stupid move.",
    [item["move"] for item in CANDIDATES],
)
check("parser still returns only an allowed candidate", move == "d8h4")
check("parser cleans body and Guided section", not any(term in explanation.lower() for term in ("as black", "gladly", "self-destruction", "stupid")), explanation)
check("parser preserves Watch out boundary", "\nWatch out:" in explanation, explanation)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

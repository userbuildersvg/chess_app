"""Advanced coach style mapping and prompt isolation. Pure: no network or engine.

The point of the banded mapping is that 1 and 10 read as different coaches,
so most of these checks are about the *gap*: what the low band says that the
high band does not, and vice versa.
"""

import re
import sys

import coach_style
import guided_play
import settings_service
from gemini_chat_service import GeminiChatService
from gemini_move_service import GeminiMoveService, sanitise_move_explanation
from opponent_profiles import PROFILE_IDS, get_profile

results = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" ({detail})" if not ok and detail else ""))
    results.append(ok)


# --- normalisation ---------------------------------------------------------
check("defaults are balanced", coach_style.normalise(None) == {"bluntness": 5.0, "creativity": 5.0})
check("corrupt values fall back safely", coach_style.normalise({"bluntness": "bad"}) == {"bluntness": 5.0, "creativity": 5.0})
check("values clamp to 0..10", coach_style.normalise({"bluntness": 14, "creativity": -3}) == {"bluntness": 10.0, "creativity": 0.0})

# --- bands -----------------------------------------------------------------
for value, expected in ((0, "extreme_low"), (1, "extreme_low"), (2, "extreme_low"), (2.4, "extreme_low"),
                        (2.5, "moderate_low"), (3, "moderate_low"), (4, "moderate_low"),
                        (4.5, "balanced"), (5, "balanced"), (6, "balanced"), (6.5, "balanced"),
                        (6.6, "moderate_high"), (7, "moderate_high"), (8, "moderate_high"),
                        (8.5, "extreme_high"), (9, "extreme_high"), (10, "extreme_high")):
    check(f"band({value}) is {expected}", coach_style.band(value) == expected, coach_style.band(value))
check("five bands, five distinct directness blocks", len({coach_style.DIRECTNESS[b] for b in coach_style.BANDS}) == 5)
check("five bands, five distinct creativity blocks", len({coach_style.CREATIVITY[b] for b in coach_style.BANDS}) == 5)

# --- sampling (explanation-only calls) --------------------------------------
check("temperature floor is 0.12", coach_style.temperature({"creativity": 0}) == 0.12)
check("temperature at creativity 1 is in 0.10-0.20", 0.10 <= coach_style.temperature({"creativity": 1}) <= 0.20)
check("temperature at creativity 5 is 0.40", coach_style.temperature({"creativity": 5}) == 0.40)
check("temperature ceiling is 0.80", coach_style.temperature({"creativity": 10}) == 0.80)
check("temperature is monotonic", all(
    coach_style.temperature({"creativity": a}) < coach_style.temperature({"creativity": a + 0.5})
    for a in [x / 2 for x in range(0, 20)]))
check("top-p remains conservative", 0.75 <= coach_style.top_p({"creativity": 0}) and coach_style.top_p({"creativity": 10}) <= 0.90)

# --- style blocks ----------------------------------------------------------
gentle_plain = coach_style.prompt_block({"bluntness": 1, "creativity": 1})
blunt_plain = coach_style.prompt_block({"bluntness": 10, "creativity": 1})
gentle_vivid = coach_style.prompt_block({"bluntness": 1, "creativity": 10})
blunt_vivid = coach_style.prompt_block({"bluntness": 10, "creativity": 10})
balanced = coach_style.prompt_block(None)

check("style block says phrasing only", "PHRASING ONLY" in balanced and "never what is true" in balanced)
check("style block names both values and bands", "Directness: 5.0/10 (balanced)" in balanced and "Creativity: 5.0/10 (balanced)" in balanced)

check("directness 1 is very gentle", all(w in gentle_plain for w in ("very gentle", "Soften every critique", "One thing to notice", "confidence-preserving")))
check("directness 1 keeps an engine-stated grade", "stays true" in gentle_plain and "never hide it" in gentle_plain)
check("directness 10 is blunt and direct", all(w in blunt_plain for w in ("blunt and tactically direct", "no cushioning", "ten words or fewer", "flat consequence")))
check("directness 10 removes cushioning that directness 1 requires", "Soften every critique" not in blunt_plain and "no cushioning" not in gentle_plain)
check("directness 10 contains no insult licence", all(w not in blunt_plain.lower() for w in ("stupid move", "idiot", "mock them", "humiliate them")) and "no insults, no mockery, no humiliation" in blunt_plain)
check("directness 10 is about the chess, not the person", "never about them" in blunt_plain)

check("creativity 1 is plain and literal", all(w in gentle_plain for w in ("plain, literal and minimal", "twelve words or fewer", "No metaphors", "clinical")))
check("creativity 10 is vivid and expressive", all(w in gentle_vivid for w in ("vivid, memorable phrasing", "analogy", "strong rhythm")))
check("creativity 10 forbids what creativity 1 forbids differently", "No metaphors" not in gentle_vivid and "vivid" not in gentle_plain)
check("creativity 10 keeps grounding constraints", "tied to a real feature" in blunt_vivid and "not permission to invent" in blunt_vivid and "never become smug, theatrical" in blunt_vivid)

check("directness 1 forbids imperatives that directness 10 requires", "Forbidden wording: 'you missed', 'you allowed', 'you must', 'defend it'" in gentle_plain and "Use 'you must', 'defend it', 'or you lose'" in blunt_plain)
check("directness 10 forbids the hedges directness 1 requires", "Forbidden wording: 'keep an eye on', 'watch out for', 'should', 'might', 'may'" in blunt_plain and "Use 'might', 'may' and 'could'" in gentle_plain)
check("creativity 10 requires an image, creativity 1 forbids one", "MUST include at least one concrete image" in gentle_vivid and "No metaphors, analogies, imagery" in gentle_plain)
check("every band carries an off-position tone example", all("a different position" in coach_style.DIRECTNESS[b] and "a different position" in coach_style.CREATIVITY[b] for b in coach_style.BANDS))
check("tone examples never mention the preview position", all("e4" not in coach_style.DIRECTNESS[b] and "e4" not in coach_style.CREATIVITY[b] for b in coach_style.BANDS))
check("all four corners differ from each other", len({gentle_plain, blunt_plain, gentle_vivid, blunt_vivid, balanced}) == 5)
check("axes are independent", gentle_plain.split("Creativity:")[1] == blunt_plain.split("Creativity:")[1]
      and gentle_plain.split("Creativity:")[0].replace("1.0/10", "") != blunt_plain.split("Creativity:")[0].replace("10.0/10", ""))
check("every block carries the respect rule", all("Never insult, shame, humiliate" in b for b in (gentle_plain, blunt_plain, gentle_vivid, blunt_vivid, balanced)))
check("every block carries the invention rule", all("not permission to invent" in b for b in (gentle_plain, blunt_plain, gentle_vivid, blunt_vivid, balanced)))

# The extremes must be a real gap, not a synonym swap: little shared wording
# between the two directness instructions beyond stopwords.
def content_words(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 4}
low_d, high_d = content_words(coach_style.DIRECTNESS["extreme_low"]), content_words(coach_style.DIRECTNESS["extreme_high"])
low_c, high_c = content_words(coach_style.CREATIVITY["extreme_low"]), content_words(coach_style.CREATIVITY["extreme_high"])
check("directness extremes share little vocabulary", len(low_d & high_d) / len(low_d | high_d) < 0.2, str(low_d & high_d))
check("creativity extremes share little vocabulary", len(low_c & high_c) / len(low_c | high_c) < 0.2, str(low_c & high_c))

# --- chat prompts (explanation-only) ----------------------------------------
service = GeminiChatService(api_key="test", models=["test"])
base_context = {"fen": "test", "coach_style": {"bluntness": 9, "creativity": 1}}
for mode, build in (
    ("Play", service._build_system_instruction),
    ("Learn", service._build_sandbox_instruction),
    ("Review", service._build_postmortem_instruction),
):
    prompt = build(base_context)
    check(f"{mode} prompt includes the banded style block", "Coach style settings (PHRASING ONLY" in prompt and "blunt and tactically direct" in prompt and "plain, literal and minimal" in prompt)
    check(f"{mode} prompt preserves chess grounding", any(word in prompt for word in ("Stockfish", "engine", "legal")))

# --- Play move prompt (combined selection + explanation) --------------------
FEN = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 2"
CANDIDATES = [
    {"move": "d8h4", "san": "Qh4#", "score": 10000, "mate_in": 1},
    {"move": "b8c6", "san": "Nc6", "score": 120, "mate_in": None},
]
move_service = GeminiMoveService(api_key="test")
facts = guided_play.describe_candidates(FEN, CANDIDATES)


def move_prompt(bluntness, creativity, **extra):
    return move_service._build_prompt(FEN, CANDIDATES, {
        "moving_color": "black", "profile": get_profile("club"), "move_history_san": ["f3", "e5", "g4"],
        "facts": facts, "coach_style": {"bluntness": bluntness, "creativity": creativity}, **extra,
    })


def evidence(prompt):
    return prompt.split("CHESS EVIDENCE")[1].split("COACH STYLE")[0]


p11, p101, p110, p1010, p55 = move_prompt(1, 1), move_prompt(10, 1), move_prompt(1, 10), move_prompt(10, 10), move_prompt(5, 5)
check("Play move explanation receives the banded style block", "Directness: 10.0/10 (extreme high)" in p1010 and "vivid, memorable phrasing" in p1010)
check("Play move explanation at 1,1 receives the gentle/plain block", "very gentle" in p11 and "plain, literal and minimal" in p11)
check("Play prompt tells the model style is phrasing-only", "must not affect which candidate" in p1010)
check("style block sits between profile and output constraints", p1010.index("OPPONENT PROFILE") < p1010.index("COACH STYLE") < p1010.index("OUTPUT CONSTRAINTS"))
check("evidence block identical across all styles", len({evidence(p) for p in (p11, p101, p110, p1010, p55)}) == 1)
check("candidate list unchanged by style", all(c["move"] in evidence(p) for p in (p11, p1010) for c in CANDIDATES))
check("FEN and history unchanged by style", all(FEN in p and "f3, e5, g4" in p for p in (p11, p1010)))
check("engine scores unchanged by style", all("10000" in evidence(p) or "mate" in evidence(p).lower() for p in (p11, p1010)))
guided_p = move_prompt(10, 10, guided=True)
check("Guided Play receives the banded style block", "vivid, memorable phrasing" in guided_p and "Watch out:" in guided_p)
check("Guided Play evidence identical to standard", evidence(guided_p) == evidence(p1010))

# --- profile knobs are not touched ------------------------------------------
before = {pid: (get_profile(pid).approx_elo, get_profile(pid).label) for pid in PROFILE_IDS}
for b, c in ((1, 1), (10, 10)):
    move_prompt(b, c)
check("opponent profile knobs unchanged after style prompts", before == {pid: (get_profile(pid).approx_elo, get_profile(pid).label) for pid in PROFILE_IDS})

# --- sanitiser still applies at the extremes --------------------------------
raw = "As Black, I gladly punish you for this bizarre opening; that was a stupid move and you invited disaster."
clean = sanitise_move_explanation(raw, "black")
check("banned wording removed", all(w not in clean.lower() for w in ("as black", "gladly", "bizarre", "stupid", "invited disaster")), clean)

# --- account settings --------------------------------------------------------
clean = settings_service.sanitise({
    "coachBluntness": 9.5, "coachCreativity": 2, "coachStylePreset": "blunt"
})
check("account settings accept bounded style", clean == {
    "coachBluntness": 9.5, "coachCreativity": 2, "coachStylePreset": "blunt"
})
check("account settings reject out-of-range style", settings_service.sanitise({"coachBluntness": 11}) == {})
check("new accounts receive balanced defaults", settings_service.DEFAULTS["coachBluntness"] == 5 and settings_service.DEFAULTS["coachCreativity"] == 5)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

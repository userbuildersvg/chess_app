"""Communication-only coach style settings.

This module deliberately has no chess imports. It maps two bounded UI values
to prompt wording and sampling parameters; engine evidence, legal moves and
grades remain outside this boundary.

Each axis is split into five bands. A value near 1 and a value near 10 must
read as a different coach, not as the same coach with an adjective changed,
so every band carries its own concrete instruction block rather than a
"slightly more" qualifier on the balanced one.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CoachStyle(BaseModel):
    bluntness: float = Field(default=5, ge=0, le=10)
    creativity: float = Field(default=5, ge=0, le=10)


def normalise(value=None) -> dict:
    if isinstance(value, CoachStyle):
        value = value.model_dump()
    if not isinstance(value, dict):
        value = {}
    try:
        bluntness = max(0.0, min(10.0, float(value.get("bluntness", 5))))
        creativity = max(0.0, min(10.0, float(value.get("creativity", 5))))
    except (TypeError, ValueError):
        bluntness, creativity = 5.0, 5.0
    return {"bluntness": round(bluntness, 1), "creativity": round(creativity, 1)}


BANDS = ("extreme_low", "moderate_low", "balanced", "moderate_high", "extreme_high")


def band(value: float) -> str:
    """1-2 extreme low, 3-4 moderate low, 5-6 balanced, 7-8 moderate high, 9-10 extreme high.

    The UI allows tenths, so the cut points sit halfway between the integer
    bands; 0 counts as the extreme low end.
    """
    if value < 2.5:
        return "extreme_low"
    if value < 4.5:
        return "moderate_low"
    if value <= 6.5:
        return "balanced"
    if value < 8.5:
        return "moderate_high"
    return "extreme_high"


# --- sampling -------------------------------------------------------------
#
# Used ONLY by the explanation-only chat calls (gemini_chat_service), where
# the move or grade is already fixed before the model speaks. The combined
# Play move-selection call carries no generationConfig at all; sampling
# there could change which candidate is picked, so creativity reaches it as
# prompt wording only.

def temperature(value=None) -> float:
    c = normalise(value)["creativity"]
    # creativity 0-5 -> 0.12..0.40, 5-10 -> 0.40..0.80
    if c <= 5:
        t = 0.12 + (c / 5.0) * 0.28
    else:
        t = 0.40 + ((c - 5) / 5.0) * 0.40
    return round(t, 3)


def top_p(value=None) -> float:
    c = normalise(value)["creativity"]
    return round(0.78 + (c / 10.0) * 0.12, 3)


# --- phrasing bands -------------------------------------------------------

DIRECTNESS = {
    "extreme_low": (
        "Be very gentle and supportive. Soften every critique. Do not add harsh "
        "labels of your own (a grade the evidence above states, such as blunder, "
        "stays true - deliver it kindly, never hide it); frame what went wrong as "
        "something to notice next time. Lead with what is reasonable about the "
        "student's idea before pointing at the problem. Keep the tone calm, "
        "patient and confidence-preserving.\n"
        "Required wording: open the critical part with 'One thing to notice' or "
        "'It may be worth checking'. Use 'might', 'may' and 'could' for what the "
        "student does next, never for a fact that is true on the board (an attack "
        "is an attack). Forbidden "
        "wording: 'you missed', 'you allowed', 'you must', 'defend it', 'or you "
        "lose', and any bare imperative aimed at the student.\n"
        "Tone example (a different position - copy the tone, never the content): "
        "\"I'm bringing a piece toward your king. One thing to notice is that the "
        "back rank may want a little more cover before you continue.\""
    ),
    "moderate_low": (
        "Be supportive and encouraging. Name the issue, but cushion it: "
        "acknowledge the student's intention, then point at the problem in a "
        "kind, non-judgemental way. Prefer 'worth' and 'consider' over commands. "
        "Avoid blunt verdicts and avoid piling on.\n"
        "Tone example (a different position - copy the tone, never the content): "
        "\"I'm bringing a piece toward your king. Your plan is sensible, but it "
        "is worth considering the back rank before you go further.\""
    ),
    "balanced": (
        "Use a clear, candid coaching tone. Say what the problem or threat is in "
        "plain words, without harshness and without excessive softening. Direct "
        "enough to be useful, still supportive.\n"
        "Tone example (a different position - copy the tone, never the content): "
        "\"I'm bringing a piece toward your king. Check your back rank before "
        "your next move; it is thinner than it looks.\""
    ),
    "moderate_high": (
        "Be direct and tactically clear. State the threat or the mistake "
        "outright, without a reassuring preamble. Do not soften important "
        "criticism, but do not dwell on it either: one plain statement, then the "
        "chess. Forbidden wording: 'keep an eye on', 'might want to', 'gently'.\n"
        "Tone example (a different position - copy the tone, never the content): "
        "\"I'm attacking your back rank. It is weak, and you need to cover it now.\""
    ),
    "extreme_high": (
        "Be blunt and tactically direct. The FIRST sentence names the threat or "
        "the mistake in ten words or fewer, plainly, with no cushioning, no "
        "reassurance and no praise padding. Then say what it costs if ignored, "
        "as a flat consequence, not a suggestion. Serious, no-nonsense, "
        "coach-like.\n"
        "Forbidden wording: 'keep an eye on', 'watch out for', 'should', "
        "'might', 'may', 'consider', 'a little', 'gently', 'while', and any "
        "hedge. Use 'you must', 'defend it', 'or you lose', 'this costs you'. "
        "Stay respectful: no insults, no mockery, no humiliation, no comments "
        "about the student as a person; the bluntness is about the chess, never "
        "about them.\n"
        "Tone example (a different position - copy the tone, never the content): "
        "\"I'm attacking your back rank. Cover it or you get mated.\""
    ),
}

CREATIVITY = {
    "extreme_low": (
        "Be plain, literal and minimal. At most two sentences, each twelve "
        "words or fewer. Standard chess terminology only; no adjectives beyond "
        "chess terms. No metaphors, analogies, imagery, jokes, rhetorical "
        "questions or dramatic phrasing. Say the fact, then stop; almost "
        "clinical.\n"
        "Shape example (a different position - copy the shape, never the "
        "content): \"I'm attacking the rook. It is undefended.\""
    ),
    "moderate_low": (
        "Be plain and precise. Prefer standard chess terminology and short, "
        "simple sentences. Only mild variation in phrasing; no figurative "
        "language.\n"
        "Shape example (a different position - copy the shape, never the "
        "content): \"I'm attacking the undefended rook, so it has to move or be "
        "protected.\""
    ),
    "balanced": (
        "Use natural, approachable explanation with some variation in "
        "phrasing. A light figure of speech is fine when it clarifies the idea; "
        "keep it concise and grounded.\n"
        "Shape example (a different position - copy the shape, never the "
        "content): \"I'm going after the rook, which nothing is defending right "
        "now.\""
    ),
    "moderate_high": (
        "Use expressive, memorable phrasing when it clarifies the chess idea. "
        "Include a vivid verb or a compact analogy; keep every image anchored "
        "to a concrete feature of the position.\n"
        "Shape example (a different position - copy the shape, never the "
        "content): \"I'm leaning on the rook, and nothing is holding it up.\""
    ),
    "extreme_high": (
        "Use vivid, memorable phrasing with strong rhythm. You MUST include at "
        "least one concrete image or analogy tied to a real feature of the "
        "position (a loose thread, a hinge, a doorway, a keystone), and vary "
        "your sentence shapes. Never let colour replace a fact, and never become "
        "smug, theatrical, mocking or self-congratulatory. Vividness is not "
        "length: in a move explanation stay within two sentences and about forty "
        "words, and every image must point at a piece or square that is really there.\n"
        "Shape example (a different position - copy the shape, never the "
        "content): \"I'm leaning on the rook, and nothing is holding it up: once "
        "it goes, the whole back rank goes with it.\""
    ),
}


def prompt_block(value=None) -> str:
    settings = normalise(value)
    directness = settings["bluntness"]
    creativity = settings["creativity"]
    return "\n".join([
        "Coach style settings (PHRASING ONLY: they change how you say things, "
        "never what is true; never alter the chess evidence above):",
        f"Directness: {directness:.1f}/10 ({band(directness).replace('_', ' ')}).",
        DIRECTNESS[band(directness)],
        f"Creativity: {creativity:.1f}/10 ({band(creativity).replace('_', ' ')}).",
        CREATIVITY[band(creativity)],
        "Never insult, shame, humiliate or demotivate the student, even at maximum "
        "directness. Never use 'self-destruction', 'bizarre opening', 'stupid', "
        "'terrible', 'pathetic', 'I gladly punish' or 'you invited disaster'.",
        "Creativity is not permission to invent moves, grades, threats, history, "
        "evaluations or engine facts, even at maximum creativity.",
    ])

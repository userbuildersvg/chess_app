/**
 * The seven opponent profiles, as the UI shows them.
 *
 * This is a mirror of `opponent_profiles.py` - id, label, approximate Elo and
 * one-line blurb - and `test_opponent_profiles.py` parses this file and fails
 * if the two tables disagree. The backend is the source of truth for what a
 * profile *does*; this file only knows what to call it.
 *
 * It replaced `difficulty.ts` (1-20 with five named bands). The number told a
 * learner nothing, and the bands were a description of a window position,
 * not of a player. "Club - about 1500" is a kind of opponent people have met.
 */

export type OpponentProfile = {
    id: string;
    label: string;
    approxElo: number;
    /** One line saying what playing this opponent is actually like. */
    blurb: string;
};

export const OPPONENT_PROFILES: OpponentProfile[] = [
    { id: 'beginner', label: 'Beginner', approxElo: 400, blurb: 'Misses tactics and leaves pieces loose. Good for learning how the pieces work.' },
    { id: 'casual', label: 'Casual', approxElo: 800, blurb: 'Sees obvious captures and checks, misses anything deeper.' },
    { id: 'improving', label: 'Improving', approxElo: 1200, blurb: 'Develops sensibly and spots one-move tactics; misses quiet defence.' },
    { id: 'club', label: 'Club', approxElo: 1500, blurb: 'Reasonable, coherent chess with the occasional strategic slip.' },
    { id: 'advanced', label: 'Advanced', approxElo: 1800, blurb: 'Coherent plans and solid defence; the errors are subtle.' },
    { id: 'expert', label: 'Expert', approxElo: 2100, blurb: 'Strong tactics and sound positional play.' },
    { id: 'master', label: 'Master-like', approxElo: 2400, blurb: 'Near-best moves nearly every time. A very strong training partner.' },
];

export const DEFAULT_PROFILE_ID = 'club';

/** The strongest profile is open-ended: "2400+", never "2400". */
function eloText(p: OpponentProfile): string {
    return p.id === 'master' ? `${p.approxElo}+` : `${p.approxElo}`;
}

export function profileById(id: string | null | undefined): OpponentProfile {
    return OPPONENT_PROFILES.find(p => p.id === id)
        ?? OPPONENT_PROFILES.find(p => p.id === DEFAULT_PROFILE_ID)!;
}

/** "Club — about 1500": the option text in the selector. */
export function profileLabel(id: string): string {
    const p = profileById(id);
    return `${p.label} — about ${eloText(p)}`;
}

/** "Club (~1500)": the compact form for status lines and headers. */
export function profileShort(id: string): string {
    const p = profileById(id);
    return `${p.label} (~${eloText(p)})`;
}

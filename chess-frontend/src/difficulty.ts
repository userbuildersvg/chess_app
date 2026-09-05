/**
 * Engine strength, 1-20, and what those numbers mean.
 *
 * The raw level is the engine's window position, which tells a learner
 * nothing. Five named bands give it meaning, and the number stays visible for
 * anyone who wants it: "12 - Club".
 *
 * This lived in two places - ChessBoard.tsx with blurbs and Sandbox.tsx
 * without them - and Sandbox's copy carried a comment promising it was "the
 * same five bands the real game's difficulty slider names, so a level means
 * the same thing in both modes". A promise in a comment is not a mechanism.
 * One table keeps it true.
 */

export const DIFFICULTY_MIN = 1;
export const DIFFICULTY_MAX = 20;

export type DifficultyBand = {
    /** The highest level in this band. */
    upTo: number;
    name: string;
    /** One line saying what playing at this strength is actually like. */
    blurb: string;
};

export const DIFFICULTY_BANDS: DifficultyBand[] = [
    { upTo: 4, name: 'Beginner', blurb: 'Plays the weakest legal moves. Good for learning how pieces move.' },
    { upTo: 8, name: 'Casual', blurb: 'Makes real mistakes you can punish.' },
    { upTo: 12, name: 'Club', blurb: 'Solid moves, occasional slips.' },
    { upTo: 16, name: 'Strong', blurb: 'Punishes loose play straight away.' },
    { upTo: 20, name: 'Merciless', blurb: 'Close to the best move it can find, every time.' },
];

export function difficultyBand(level: number): DifficultyBand {
    return DIFFICULTY_BANDS.find(b => level <= b.upTo)
        ?? DIFFICULTY_BANDS[DIFFICULTY_BANDS.length - 1];
}

/** "12 - Club" — the label and the value in one breath. */
export function difficultyLabel(level: number): string {
    return `${level} - ${difficultyBand(level).name}`;
}

/** Every selectable level, for a <select>. */
export const DIFFICULTY_LEVELS: number[] = Array.from(
    { length: DIFFICULTY_MAX - DIFFICULTY_MIN + 1 },
    (_, i) => DIFFICULTY_MIN + i,
);

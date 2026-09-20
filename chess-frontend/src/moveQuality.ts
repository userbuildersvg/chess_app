/**
 * The move-grade vocabulary, shared by every mode that shows one.
 *
 * These lived inside ChessBoard.tsx, which was fine while the real game was
 * the only thing that graded a move. Post-Mortem grades a whole imported game
 * with the same backend function (move_quality.py), so the alternative to
 * pulling them out here was a second copy of the palette - and a second copy
 * is how a blunder ends up one red in the game and a different red in the
 * review, for no reason a user could ever discover.
 *
 * The backend's `label` is the contract. `move_quality.QUALITY_META` mints it,
 * every consumer switches on it, and neither side renames one without the
 * other.
 */

/**
 * Chess.com-style grade for a single half-move, produced by the backend's
 * move_quality.py. `label` is the stable key styling switches on; `symbol` is
 * the annotation glyph ("!!", "??", ...) a badge renders.
 *
 * Arrives asynchronously in both modes - a move exists before its grade does,
 * and a whole-game scan fills these in over a minute - so every consumer has
 * to treat it as optional rather than assuming it is there.
 */
export type MoveQuality = {
    label: string;
    name: string;
    symbol: string;
    cpl?: number;
    best_move?: string | null;
    opening?: string | null;
    accuracy?: number | null;
    /** What search produced the grade. Null for `book` and `forced`, which are
     *  facts about the position rather than engine verdicts. */
    depth?: number | null;
    /**
     * Whether the evidence actually separates this grade from the gentler one
     * next door. `move_quality.grade_from_scores` sets it to "low" when the
     * centipawn loss sits within the engine's own measured run-to-run spread
     * of a threshold, or when the search was too shallow to be drawing the
     * distinction at all. It exists because the tester report this sprint
     * answered was a move graded worse than the engine could actually justify.
     */
    confidence?: 'high' | 'low';
    /** What to say instead, in words, when `confidence` is "low". */
    note?: string | null;
    /** "stockfish" | "book" | "rules" - never a language model. */
    grade_source?: string | null;
};

/**
 * Grade -> badge color. Mirrors chess.com's palette closely enough to be
 * readable at a glance: teal/green for the good end, blue-grey for neutral
 * book/forced moves, amber through red for the mistakes.
 *
 * Best, Excellent and Good previously sat within a few hex points of each
 * other, which made them indistinguishable at badge size. They now step down a
 * clear green ramp, well separated from each other and from the amber/red end.
 *
 * These are fixed hex values rather than theme tokens on purpose: a grade
 * means the same thing in both themes, and the ramp has to stay ordered - a
 * palette that re-derived itself per theme would be free to reorder it.
 */
export const QUALITY_COLORS: Record<string, string> = {
    // Muted to sit in the Slate + Parchment palette: the ramp is still
    // ordered cool-good to warm-bad, just in vegetable ink rather than neon.
    brilliant: '#7FB3A3',
    great: '#8A9BB0',
    best: '#6F8F6A',
    excellent: '#8FAE8B',
    good: '#A9B388',
    book: '#9F988D',
    inaccuracy: '#C8B38A',
    mistake: '#C19461',
    miss: '#B77E68',
    blunder: '#B76E61',
    forced: '#8f9296',
};

export const qualityColor = (label: string): string => QUALITY_COLORS[label] ?? '#8f9296';

/**
 * Excluded from the accuracy average for the same reason the backend excludes
 * them (move_quality.NON_JUDGING_LABELS): neither reflects a decision made at
 * the board. A forced move had no alternative; a book move is memorised.
 */
export const NON_JUDGING_LABELS = new Set(['book', 'forced']);

/**
 * Grades worth calling out when reviewing a finished game - the ones that cost
 * something. Used to filter a game's move list down to "show me what went
 * wrong", which is the first thing anyone does with a post-mortem.
 */
export const ERROR_LABELS = new Set(['inaccuracy', 'mistake', 'blunder', 'miss']);

/**
 * What a move cost, as text - never as a pawn count the number cannot bear.
 *
 * `cpl` is centipawn loss on the backend's MATE_SCORE scale (move_quality.py),
 * where a forced mate is mapped to ~10000 so that mates sort against ordinary
 * scores. That mapping is right for sorting and wrong for reading: rendered as
 * pawns, throwing away a mate in three came out as "-93.0", which is not a
 * quantity of anything. A move that loses a forced win is described as losing
 * a forced win.
 */
export function lossText(cpl: number | null | undefined, label?: string): string {
    if (typeof cpl !== 'number' || cpl <= 0) {
        return '';
    }
    if (label === 'miss') {
        return 'missed mate';
    }
    // Ten pawns. Past this the difference is not a material count any more,
    // and on this scale it is usually a mate that appeared or disappeared.
    if (cpl >= 1000) {
        return 'lost a forced win';
    }
    return `-${(cpl / 100).toFixed(1)}`;
}

/**
 * The grade as a sentence, hedged exactly as far as the evidence allows.
 *
 * The badge shows a symbol and a colour; this is what its tooltip and the move
 * list say, and it is the one place the frontend phrases a verdict. Three
 * rules, all from the trust half of the beta-hardening sprint:
 *
 *  - It never states a grade the backend did not send. Every part of the
 *    string is read out of the dict rather than derived here.
 *  - When `confidence` is low it uses the engine's own hedge instead of
 *    repeating the label with more conviction than was earned. "Not the
 *    engine's top choice" is a true thing to say about a move 60cp behind at
 *    depth 12; "a mistake" is a claim that search cannot support.
 *  - It names WHOSE move it is. The board badge tracks the most recent
 *    half-move, and in Play the AI answers within about a second, so the badge
 *    a player sees a moment after moving is usually the grade of the AI's
 *    REPLY sitting on the AI's square. A "??" appearing just after your own
 *    move, with nothing on it to say whose it is, reads as a verdict on your
 *    move - which is one of the ways "I played the best move and it called it
 *    bad" happens without any grade being wrong at all.
 */
export function gradeSentence(q: MoveQuality, mover?: 'white' | 'black'): string {
    const who = mover ? `${mover === 'white' ? 'White' : 'Black'}: ` : '';
    if (q.confidence === 'low' && q.note) {
        return `${who}${q.name} - ${q.note}`;
    }
    const parts = [`${who}${q.name}`];
    if (q.opening) {
        parts.push(q.opening);
    } else {
        const cost = lossText(q.cpl, q.label);
        if (cost) parts.push(cost);
    }
    if (q.depth) parts.push(`depth ${q.depth}`);
    return parts.join(' · ');
}

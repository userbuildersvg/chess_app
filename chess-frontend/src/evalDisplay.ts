/**
 * How an engine evaluation is drawn, shared by every eval bar in the app.
 * All of it takes the evaluation in White's absolute frame.
 */
export type EvalLike = { score: number | null; mate_in: number | null };

/**
 * Whether the engine said anything about this position at all.
 *
 * `{score: null, mate_in: null}` is what comes back for a ply the scan has
 * not reached, a grade it could not produce, and a finished game that was
 * not a mate. It used to fall through the `?? 0` below and draw a bar at
 * dead level with "0.0" under it - the app asserting a balanced position
 * when what it had was no reading at all, which is how a lost position came
 * to look equal. Callers ask this first and draw the bar as unknown.
 */
export const evalKnown = (evalData: EvalLike): boolean =>
    evalData.score !== null || evalData.mate_in !== null;

/**
 * White's share of the bar, 0-100. Centipawns are compressed into +/-1000
 * (10 pawns) so a single blunder does not max out the bar - beyond that,
 * one side is winning so decisively the exact number stops mattering.
 *
 * `sideToMove` is only needed for mate that is already ON the board.
 * `mate_in: 0` carries no sign (postmortem_analysis writes it on any
 * checkmate), so the unsigned test below read every finished mate as a win
 * for Black - including the game in the fixture, which White won. The side
 * to move in a mated position is the side that was mated, so the other one
 * has won; without it we cannot tell and the bar stays level.
 */
export const evalToWhitePercent = (evalData: EvalLike, sideToMove?: 'white' | 'black'): number => {
    if (evalData.mate_in !== null) {
        if (evalData.mate_in === 0) {
            if (!sideToMove) return 50;
            return sideToMove === 'black' ? 100 : 0;
        }
        return evalData.mate_in > 0 ? 100 : 0;
    }
    const cp = evalData.score ?? 0;
    const clamped = Math.max(-1000, Math.min(1000, cp));
    return 50 + (clamped / 1000) * 50;
};

/** "+2.1", "-4.6", "M3" (White mates in 3), "-M1" (Black mates in 1),
 *  "#" (mate on the board), "-" (the engine has not said). */
export const formatEval = (evalData: EvalLike): string => {
    if (evalData.mate_in !== null) {
        if (evalData.mate_in === 0) return '#';
        return evalData.mate_in > 0 ? `M${evalData.mate_in}` : `-M${Math.abs(evalData.mate_in)}`;
    }
    if (evalData.score === null) {
        return '-';
    }
    const pawns = evalData.score / 100;
    const sign = pawns > 0 ? '+' : '';
    return `${sign}${pawns.toFixed(1)}`;
};

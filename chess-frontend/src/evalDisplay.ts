/**
 * How an engine evaluation is drawn, shared by every eval bar in the app.
 * Both take the evaluation in White's absolute frame.
 */
export type EvalLike = { score: number | null; mate_in: number | null };

/** White's share of the bar, 0-100. Centipawns are compressed into +/-1000
 *  (10 pawns) so a single blunder does not max out the bar - beyond that,
 *  one side is winning so decisively the exact number stops mattering. */
export const evalToWhitePercent = (evalData: EvalLike): number => {
    if (evalData.mate_in !== null) {
        return evalData.mate_in > 0 ? 100 : 0;
    }
    const cp = evalData.score ?? 0;
    const clamped = Math.max(-1000, Math.min(1000, cp));
    return 50 + (clamped / 1000) * 50;
};

/** "+2.1", "-4.6", "M3" (White mates in 3), "-M1" (Black mates in 1). */
export const formatEval = (evalData: EvalLike): string => {
    if (evalData.mate_in !== null) {
        return evalData.mate_in > 0 ? `M${evalData.mate_in}` : `-M${Math.abs(evalData.mate_in)}`;
    }
    if (evalData.score === null) {
        return '0.0';
    }
    const pawns = evalData.score / 100;
    const sign = pawns > 0 ? '+' : '';
    return `${sign}${pawns.toFixed(1)}`;
};

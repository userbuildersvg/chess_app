import type { CSSProperties } from 'react';

/**
 * The vertical evaluation bar beside the board - the one every chess site
 * draws: a black-and-white column exactly the board's height, white's share
 * growing from the bottom when White sits at the bottom, with the score
 * printed inside the leading side. Play's "Engine numbers" and Learn's "Eval
 * bar" are the same component; two boards measuring the same thing should
 * not disagree about how it is shown.
 *
 * It is rendered INSIDE the board frame (`position: absolute; right: 100%`),
 * which is what makes two of the three promises free:
 *
 *   - it is the board's height, whatever the board size is set to, because
 *     `height: var(--board-size)` is the very number the board is drawn at,
 *     and `top` is the frame's padding;
 *   - it cannot move the board, because an absolutely positioned child takes
 *     no space in the flow.
 *
 * The third - that switching it on does not push the board either - is the
 * column's job: the workspace reserves `--ws-eval-slot` to the left of the
 * board at all times (styles/shell.css), and the bar hangs into that slot
 * whether it is showing or not. `visibility` rather than `display` when off,
 * so it also leaves the accessibility tree on its own.
 */
export function EvalBar({
    share,
    label,
    on,
    flipped,
}: {
    /** White's share of the position, 0..1 - already compressed by the caller. */
    share: number;
    /** "+2.1", "M3", "-M1" - printed inside the bar. */
    label: string;
    on: boolean;
    /** Black at the bottom of the board, so white's share grows from the top. */
    flipped: boolean;
}) {
    return (
        <div
            className={`eval-bar ${on ? '' : 'is-off'} ${flipped ? 'is-flipped' : ''}`}
            style={{ ['--eval-share' as string]: share } as CSSProperties}
            title="Position evaluation, from White's point of view"
            aria-hidden={!on}
            role="img"
            aria-label={`Evaluation ${label}`}
        >
            <div className="eval-bar-fill" />
            {/* Under the bar, in the page's text colour, at a size that can
                be read. Inside the bar at 9px it could not. */}
            <span className="eval-bar-label">{label}</span>
        </div>
    );
}

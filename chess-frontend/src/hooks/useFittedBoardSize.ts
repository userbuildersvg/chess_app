import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

/**
 * The largest board that does not push the page past the viewport.
 *
 * useBoardSize takes the surrounding chrome as a NUMBER the caller supplies,
 * which works while that chrome is a fixed height. Learner Mode's is not: the
 * control row under the board wraps to a different number of lines at
 * different widths, so one constant cannot serve two viewports. Measured at
 * 1440 it overflowed 1280 by 92px; sized for 1280 it threw away 49px of board
 * at 1440. The history of that number here is a list of hand-retuned values -
 * 340, 372, 360, 297 - each right at the one size it was checked against, and
 * each wrong again the next time a row was added under the board.
 *
 * So this does not model the layout at all. It reads the one fact that
 * actually matters - whether the page overflows - and corrects the board by
 * exactly that much, letting it grow back into any slack up to the width the
 * viewport allows. Whatever gets added or removed under the board, the
 * correction is still the overflow.
 *
 * It settles in a frame or two and then stops: a deadband keeps sub-pixel
 * layout differences from becoming a measure-render-measure loop, and the
 * result is clamped so a transient zero-height layout (a backgrounded tab, a
 * hidden pane) cannot drive the board to nothing.
 *
 * The correction is only sound while the board is what makes the page tall -
 * that is, while the board and the panel are side by side. Stacked, the panel
 * sits BELOW the board and its height is the overflow, so taking width off the
 * board removes none of it and the loop simply runs the board down to its
 * floor. Review at 1024 measured a 236px board under a full-width panel: a
 * postage stamp, in the one mode whose whole purpose is looking at a position.
 * `enabled` is how the caller says which layout it is in - see useStacked.ts.
 *
 * @param columnRef   the column holding the board and everything under it
 * @param widthTarget how wide the board would like to be, from the viewport
 * @param enabled     false while the layout is stacked: take the width target
 *                    as given and let the page scroll, which is correct for a
 *                    single column anyway
 * @param allowance   the page overflow to aim for, in px. Signed.
 *
 *   Zero is "the page must not scroll", which is what this has always done and
 *   what the Auto board size still means.
 *
 *   POSITIVE is the user having asked for a board bigger than the layout would
 *   choose, and it is the only honest way to give them one: on a height-bound
 *   layout there is no slack to grow into, so the only thing a larger board can
 *   cost is a scrollbar - and someone who picks "Large" has decided that is a
 *   fair price.
 *
 *   NEGATIVE is "leave this much slack", which is how "Small" is built. It has
 *   to go through the allowance rather than being subtracted from the result,
 *   and that is not a stylistic choice: this hook measures the REAL page, so a
 *   board shrunk after the fact shows up here as slack, gets grown back into on
 *   the next pass, and the two fight. Inside the loop it is just a different
 *   convergence target and it settles in a frame like any other.
 *
 *   Lowering `widthTarget` does not achieve the same thing, which is the bug
 *   that produced this parameter's second half: the fitted size is usually
 *   already well BELOW the width target (Review at 1440x900 fits 345px against
 *   a 540px target), so a smaller ceiling changes a number nothing was reading.
 *   Measured before the fix: auto, small, medium and large all produced exactly
 *   369px.
 *
 *   It cannot oscillate in either direction. The loop converges on
 *   `overflow == allowance` instead of `overflow == 0`; the shrink branch only
 *   removes overflow above the allowance, the grow branch is still capped by
 *   widthTarget, and the floor is still 240px.
 */
export function useFittedBoardSize(
    columnRef: React.RefObject<HTMLElement | null>,
    widthTarget: number,
    enabled = true,
    allowance = 0,
): number {
    const [size, setSize] = useState(widthTarget);
    // What the current layout was produced from, so a measurement that agrees
    // with what is on screen does not schedule a render to say so.
    const settled = useRef(widthTarget);
    // The widest board that did not overflow sideways, at this width target.
    const xCeiling = useRef(Infinity);

    const measure = useCallback(() => {
        if (!columnRef.current) {
            return;
        }
        // Stacked: the board is not what overflows, so there is nothing here
        // to correct. Sit at the width target and let the page scroll.
        if (!enabled) {
            if (settled.current !== widthTarget) {
                settled.current = widthTarget;
                setSize(widthTarget);
            }
            return;
        }
        const doc = document.documentElement;
        // A hidden or not-yet-laid-out pane reports zero. Correcting against
        // that would collapse the board for no reason.
        if (doc.clientHeight === 0 || columnRef.current.offsetHeight === 0) {
            return;
        }
        // How far past the ACCEPTABLE overflow the page is. With the default
        // allowance of 0 this is just the overflow, which is what it always
        // was; with a positive one it is the part of the overflow the user did
        // not ask for.
        const excess = (doc.scrollHeight - doc.clientHeight) - allowance;
        // Sideways overflow is never acceptable, allowance or not. It happens
        // where "Large" asks for more board than the row has room for beside
        // the panel (1100-1300 wide), and it is remembered as a ceiling: the
        // vertical slack a narrower board leaves would otherwise be grown
        // straight back into, and the two corrections would alternate forever.
        // ponytail: assumes the board is what overflows sideways, as it is the
        // vertical case; a wide stray element would shrink the board instead.
        const excessX = doc.scrollWidth - doc.clientWidth;
        if (excessX > 0) {
            xCeiling.current = Math.min(xCeiling.current, settled.current - excessX);
        }

        let next: number;
        if (excess > 0 || excessX > 0) {
            next = settled.current - Math.max(excess, excessX);
        } else if (settled.current < widthTarget) {
            // Room to spare and the board is smaller than it wants to be:
            // give back what is going unused, up to the width target.
            next = Math.min(widthTarget, settled.current - excess);
        } else {
            next = widthTarget;
        }

        next = Math.max(240, Math.min(widthTarget, xCeiling.current, Math.floor(next)));
        // The deadband guards the GROW-BACK direction only. Applied to both,
        // it left the page permanently overflowing by 1 or 2px - a correction
        // too small to clear the band, so the board never took it and the
        // window kept a scrollbar it did not need. Shrinking cannot oscillate:
        // each pass removes real overflow, and once there is none the
        // grow-back branch computes the same size it already has.
        if (next < settled.current || Math.abs(next - settled.current) > 2) {
            settled.current = next;
            setSize(next);
        }
    }, [columnRef, widthTarget, enabled, allowance]);

    // The width target changes on a window resize; the board should follow it
    // up as well as down rather than staying wherever the last correction left
    // it. Crossing the stacking breakpoint does the same - the board coming
    // back from a stacked layout starts from the target, not from whatever the
    // last two-column correction left behind.
    useEffect(() => {
        settled.current = widthTarget;
        xCeiling.current = Infinity;
        setSize(widthTarget);
    }, [widthTarget, enabled, allowance]);

    // Before paint, so the first frame is already right rather than showing an
    // oversized board and snapping.
    useLayoutEffect(measure);

    useEffect(() => {
        const column = columnRef.current;
        window.addEventListener('resize', measure);
        if (!column || typeof ResizeObserver === 'undefined') {
            return () => window.removeEventListener('resize', measure);
        }
        // Watches the column, not the window: the chrome changes when a row
        // wraps or the eval bar is switched on, and neither is a resize.
        const observer = new ResizeObserver(measure);
        observer.observe(column);
        return () => {
            observer.disconnect();
            window.removeEventListener('resize', measure);
        };
    }, [columnRef, measure]);

    return size;
}

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
 * @param columnRef   the column holding the board and everything under it
 * @param widthTarget how wide the board would like to be, from the viewport
 */
export function useFittedBoardSize(
    columnRef: React.RefObject<HTMLElement | null>,
    widthTarget: number,
): number {
    const [size, setSize] = useState(widthTarget);
    // What the current layout was produced from, so a measurement that agrees
    // with what is on screen does not schedule a render to say so.
    const settled = useRef(widthTarget);

    const measure = useCallback(() => {
        if (!columnRef.current) {
            return;
        }
        const doc = document.documentElement;
        // A hidden or not-yet-laid-out pane reports zero. Correcting against
        // that would collapse the board for no reason.
        if (doc.clientHeight === 0 || columnRef.current.offsetHeight === 0) {
            return;
        }
        const overflow = doc.scrollHeight - doc.clientHeight;

        let next: number;
        if (overflow > 0) {
            next = settled.current - overflow;
        } else if (settled.current < widthTarget) {
            // Room to spare and the board is smaller than it wants to be:
            // give back what is going unused, up to the width target.
            next = Math.min(widthTarget, settled.current - overflow);
        } else {
            next = widthTarget;
        }

        next = Math.max(240, Math.min(widthTarget, Math.floor(next)));
        if (Math.abs(next - settled.current) > 2) {
            settled.current = next;
            setSize(next);
        }
    }, [columnRef, widthTarget]);

    // The width target changes on a window resize; the board should follow it
    // up as well as down rather than staying wherever the last correction left
    // it.
    useEffect(() => {
        settled.current = widthTarget;
        setSize(widthTarget);
    }, [widthTarget]);

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

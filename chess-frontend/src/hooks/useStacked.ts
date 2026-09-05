import { useEffect, useState } from 'react';

/**
 * The width below which every mode stacks its two columns into one.
 *
 * The same number as the `max-width: 1100px` block in styles/shell.css. CSS
 * cannot hand a media query a variable and JS cannot read one out of a
 * stylesheet, so the value is stated in both places; it is stated ONCE in each
 * of them, which is what the three previous breakpoints (1100 in Review, 980
 * in Learn, none in Play) were not.
 */
export const STACK_BREAKPOINT = 1100;

/**
 * Whether the layout is currently stacked into a single column.
 *
 * This exists because the board fitter has to know. useFittedBoardSize shrinks
 * the board by exactly however much the page overflows, which is the right
 * correction while the board and the panel are side by side - there the board
 * is what makes the page tall, so taking width off the board is what puts the
 * page back inside the viewport.
 *
 * Stacked, that reasoning inverts. The panel now sits BELOW the board and its
 * height is the overflow, so shrinking the board removes nothing and the
 * fitter simply runs the board down until it hits its own floor. Review at
 * 1024 measured a 236px board under a full-width panel: a postage stamp of a
 * position, in the one mode whose entire purpose is looking at the position.
 *
 * So below the breakpoint the fitter is switched off and the page is allowed
 * to scroll, which is the correct behaviour for a single column anyway.
 */
export function useStacked(): boolean {
    const [stacked, setStacked] = useState(() => matches());

    useEffect(() => {
        // matchMedia is missing in some test environments; falling back to a
        // resize listener keeps the hook honest rather than throwing.
        if (typeof window.matchMedia !== 'function') {
            const update = () => setStacked(matches());
            window.addEventListener('resize', update);
            return () => window.removeEventListener('resize', update);
        }
        const query = window.matchMedia(`(max-width: ${STACK_BREAKPOINT}px)`);
        const update = () => setStacked(query.matches);
        update();
        query.addEventListener('change', update);
        return () => query.removeEventListener('change', update);
    }, []);

    return stacked;
}

function matches(): boolean {
    if (typeof window === 'undefined') {
        return false;
    }
    if (typeof window.matchMedia === 'function') {
        return window.matchMedia(`(max-width: ${STACK_BREAKPOINT}px)`).matches;
    }
    return window.innerWidth <= STACK_BREAKPOINT;
}

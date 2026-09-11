import { useCallback, useEffect, useState, type RefObject } from 'react';
import { useBoardSize } from './useBoardSize';
import { useFittedBoardSize } from './useFittedBoardSize';
import { useStacked } from './useStacked';

/**
 * How big the player wants the board, as one preference shared by all three
 * modes.
 *
 * A beta tester said the board was too small and asked to be able to change
 * it. The board already grows with the viewport (useBoardSize) and is already
 * capped by what fits (useFittedBoardSize), so what was missing was not
 * responsiveness - it was a say in the trade-off those two make. Someone
 * reading a position wants the board bigger than the layout would choose;
 * someone reading the coach wants it smaller.
 *
 * ONE preference, not three
 * -------------------------
 * Play, Learn and Review all read this single key. Two boards of different
 * sizes in one app read as two apps, which is the same argument useBoardSize's
 * docstring already makes for sharing the sizing between the game and the
 * sandbox - a demonstration rendered differently from the game it is teaching
 * does not look like the same product.
 *
 * Why localStorage and not the account
 * ------------------------------------
 * `services/preferences.ts` syncs five settings to the account, and this is
 * deliberately not a sixth. Those five are about taste - which pieces, which
 * panel - and taste travels with a person. Board size is about the screen in
 * front of them: the right answer on a 27-inch monitor is the wrong answer on
 * a laptop, and syncing it would carry a choice made on one to the other and
 * call it a preference. It is stored the way `zugzwang-theme` and
 * `sandbox-piece-theme` are, which is the established shape for a device-local
 * display setting here. It would also need a backend migration, and this is a
 * hardening sprint.
 *
 * How the sizes work
 * ------------------
 * A pixel DELTA on whatever the layout would have chosen, applied in two
 * places at once: it raises the ceiling `useBoardSize` sets, and it becomes
 * the page overflow `useFittedBoardSize` is willing to accept.
 *
 * It was a multiplier first, and that version did nothing at all - a tester
 * reported picking "Large" in Review and seeing no change, and the measurement
 * agreed: auto, medium and large all produced a 369px board at 1440x900.
 * Two reasons, and both matter:
 *
 *   1. `useBoardSize` caps its width ambition at `height - chrome`. On any
 *      layout where the HEIGHT is the binding constraint - Review at almost
 *      every viewport, because it passes the largest chrome figure - scaling
 *      the ambition changes a number that is then thrown away by the cap.
 *   2. Even where it survived, the fitter shrank it straight back: its job was
 *      to remove ALL page overflow, so any growth it had not chosen itself was
 *      undone within a frame.
 *
 * A delta fixes both, because it is applied to the thing the fitter actually
 * converges on rather than to an ambition the layout discards.
 *
 * The two directions are NOT symmetrical, and finding that out cost a third
 * iteration. Growing goes through the fitter's overflow allowance: the page is
 * allowed to scroll by 110px and the fitter stops trimming at that point.
 * Shrinking cannot use the same route, because `scrollHeight - clientHeight`
 * is clamped at zero by the browser - a page that fits reports NO overflow,
 * never negative slack - so asking the fitter to converge on -90px of overflow
 * is asking it to reach a number it can never measure, and it simply shrank
 * the board to its 240px floor looking for it. Measured: "Small" produced a
 * 264px board at 1920x1080, where the board that fits is 549px.
 *
 * So shrinking is subtracted from the fitted result instead, which is safe for
 * the same reason the negative allowance was not: the fitter cannot see the
 * slack that creates, so it has nothing to grow back into and stays put.
 *
 * Auto and Medium are 0 - the board that fits. Large is +110px of accepted
 * page scroll, the only currency available on a layout with no slack left, and
 * someone choosing "Large" has decided a scrollbar is worth it. Small is 90px
 * off the fitted size, and always fits, because it only ever asks for less.
 *
 * Pixels rather than percentages, and this is the same argument
 * `useFittedBoardSize` makes for measuring rather than modelling: a percentage
 * of a board that is 640px on one screen and 270px on another means two very
 * different amounts of "bigger", while 110px is 110px.
 */

export type BoardSizePref = 'auto' | 'small' | 'medium' | 'large';

const STORAGE_KEY = 'zugzwang-board-size';

/**
 * Pixels added to (or taken from) the board the layout would have chosen.
 *
 * Auto and Medium are both 0 and they are not the same thing. Auto means "you
 * decide", and is the default so nobody who never opens this control sees any
 * change from the build before it. Medium means "I chose this", and exists so
 * the ladder has a middle rung to come back to - a three-position control
 * whose middle is missing makes returning from Large feel like a downgrade.
 */
const DELTAS: Record<BoardSizePref, number> = {
    auto: 0,
    small: -90,
    medium: 0,
    large: 110,
};

export const BOARD_SIZE_OPTIONS: { id: BoardSizePref; label: string }[] = [
    { id: 'auto', label: 'Auto' },
    { id: 'small', label: 'Small' },
    { id: 'medium', label: 'Medium' },
    { id: 'large', label: 'Large' },
];

function read(): BoardSizePref {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored && stored in DELTAS) {
            return stored as BoardSizePref;
        }
    } catch {
        // Private browsing, or storage blocked. Auto is the right fallback:
        // it is what the app did before this control existed.
    }
    return 'auto';
}

/**
 * The event the three modes talk to each other over.
 *
 * `storage` only fires in OTHER tabs, so without this a change made in Play
 * would not reach the Learn and Review components mounted beside it - and all
 * three ARE mounted at once, hidden rather than unmounted (CLAUDE.md §9), so
 * switching modes would show a board still at the old size until something
 * else re-rendered it.
 */
const CHANGED = 'zugzwang-board-size-changed';

export function useBoardScale(): [BoardSizePref, (next: BoardSizePref) => void, number] {
    const [pref, setPref] = useState<BoardSizePref>(read);

    useEffect(() => {
        const sync = () => setPref(read());
        window.addEventListener(CHANGED, sync);
        // The other tab's copy of the app, for a user with two open.
        window.addEventListener('storage', sync);
        return () => {
            window.removeEventListener(CHANGED, sync);
            window.removeEventListener('storage', sync);
        };
    }, []);

    const choose = useCallback((next: BoardSizePref) => {
        try {
            localStorage.setItem(STORAGE_KEY, next);
        } catch {
            // The size still applies for this session; it just will not persist.
        }
        setPref(next);
        window.dispatchEvent(new Event(CHANGED));
    }, []);

    return [pref, choose, DELTAS[pref]];
}

/**
 * The whole board-sizing chain for one mode, in one call.
 *
 * All three modes need the identical four steps - read the preference, raise
 * the ceiling, fit to the page, apply a shrink - and the asymmetry between
 * growing and shrinking (see above) is exactly the kind of detail that gets
 * copied correctly into two call sites and wrongly into the third. It is
 * written once here instead.
 *
 * @param columnRef the column holding the board and everything under it
 * @param chrome    vertical space this mode's surroundings need - each mode
 *                  frames the board differently, so each passes its own figure
 */
export function useBoardSizing(
    columnRef: RefObject<HTMLElement | null>,
    chrome: number,
): { pref: BoardSizePref; setPref: (next: BoardSizePref) => void; boardSize: number } {
    const [pref, setPref, delta] = useBoardScale();
    // Only a POSITIVE delta reaches the layout and the fitter: it raises the
    // ceiling so a bigger board is reachable at all, and it becomes the page
    // overflow the fitter will tolerate to get there.
    const grow = Math.max(0, delta);
    const widthTarget = useBoardSize(chrome) + grow;
    const stacked = useStacked();
    const fitted = useFittedBoardSize(columnRef, widthTarget, !stacked, grow);
    // A NEGATIVE delta is applied here, after the fit, because the browser
    // cannot report negative overflow for the fitter to converge on. Floored
    // at 240px, which is where the pieces stop being legible - a preference
    // does not get to go below that.
    const boardSize = Math.max(240, fitted + Math.min(0, delta));
    return { pref, setPref, boardSize };
}

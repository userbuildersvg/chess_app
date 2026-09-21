import { useEffect, useState } from 'react';

/**
 * How big the board should be, in pixels.
 *
 * The board is the primary visual anchor, so it grows with the viewport rather
 * than sitting at a fixed 440px - at 1440 wide that left it narrower than the
 * analysis rail beside it, which inverts the hierarchy the layout exists to
 * express.
 *
 * Width sets the ambition and height caps it. A 620px board on a 720px-tall
 * screen pushes the player strips off the bottom, and having to scroll to see
 * whose turn it is defeats the point of the board being the anchor.
 *
 * Shared by the real game and the sandbox so the two boards cannot drift; a
 * demonstration that renders at a different size to the game it is teaching
 * reads as a different app.
 *
 * @param chrome Vertical space the surrounding layout needs - header, player
 *   strips, container padding. The two modes frame the board differently, so
 *   each passes its own figure.
 */
/** The widest viewport that gets the phone treatment; mobile.css uses the
 *  same number. */
export const MOBILE_MAX = 640;

/** The largest board Auto will draw. Large may go past it where the height
 *  already allows (useBoardScale), never where it does not. */
export const MAX_BOARD = 620;

/**
 * @param capHeight false asks for the biggest board the WIDTH alone allows,
 *   ignoring what fits under it - the floor "Large" stands on (useBoardScale).
 */
export function useBoardSize(chrome = 300, capHeight = true): number {
    const [size, setSize] = useState(() => compute(chrome, capHeight));

    useEffect(() => {
        const update = () => setSize(compute(chrome, capHeight));
        update();
        window.addEventListener('resize', update);
        return () => window.removeEventListener('resize', update);
    }, [chrome, capHeight]);

    return size;
}

function compute(chrome: number, capHeight: boolean): number {
    const width = window.innerWidth;
    const height = window.innerHeight;

    // A phone: the board is the viewport's full width, edge to edge, the way
    // a chess app draws it. styles/mobile.css removes the frame's padding and
    // bleeds the frame across the workspace's side padding at the same
    // breakpoint, so this number is exactly what the frame can hold. The
    // height cap is ignored too: the board is the page's first block and the
    // page scrolls, so nothing under it needs to fit above the fold.
    if (width <= MOBILE_MAX) {
        return Math.max(240, Math.min(MAX_BOARD, width));
    }

    // Everything else: as big as the width allows, up to the ceiling. This
    // used to step through width tiers (900/1200/1400/1600 -> 460/500/560/620),
    // which made two laptops with room for the same board draw different ones
    // - a 1440 screen was held at 560 while a 1600 one got 620 - and made the
    // board jump at each tier while a window was resized. The width is only
    // ever the binding constraint in the stacked layout; side by side, the
    // height is, and useFittedBoardSize measures that.
    let target = Math.min(MAX_BOARD, width - 80);
    if (capHeight) target = Math.min(target, height - chrome);

    // Clamped at both ends. window.innerWidth/innerHeight report 0 during some
    // layout passes (a backgrounded tab, a zero-size frame), and an unclamped
    // `width - 80` then yields a negative size. That reached pieceThemes.tsx,
    // which divides it by 8 and passes the result straight to <svg width> -
    // the source of the repeated "negative value is not valid" console errors.
    return Math.max(240, Math.min(MAX_BOARD, target));
}

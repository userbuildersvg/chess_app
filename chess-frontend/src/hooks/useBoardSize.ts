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

export function useBoardSize(chrome = 300): number {
    const [size, setSize] = useState(() => compute(chrome));

    useEffect(() => {
        const update = () => setSize(compute(chrome));
        update();
        window.addEventListener('resize', update);
        return () => window.removeEventListener('resize', update);
    }, [chrome]);

    return size;
}

function compute(chrome: number): number {
    const width = window.innerWidth;
    const height = window.innerHeight;

    let target: number;
    if (width >= 1600) target = 620;
    else if (width >= 1400) target = 560;
    else if (width >= 1200) target = 500;
    else if (width >= 900) target = 460;
    // A phone: the board is the viewport's full width, edge to edge, the way
    // a chess app draws it. styles/mobile.css removes the frame's padding and
    // bleeds the frame across the workspace's side padding at the same
    // breakpoint, so this number is exactly what the frame can hold.
    else if (width <= MOBILE_MAX) target = width;
    else target = width - 80;

    // On a phone the height cap is ignored: the board is the page's first
    // block and the page scrolls, so nothing under it needs to fit above the
    // fold - the desktop reason for the cap.
    if (width > MOBILE_MAX) target = Math.min(target, height - chrome);

    // Clamped at both ends. window.innerWidth/innerHeight report 0 during some
    // layout passes (a backgrounded tab, a zero-size frame), and an unclamped
    // `width - 80` then yields a negative size. That reached pieceThemes.tsx,
    // which divides it by 8 and passes the result straight to <svg width> -
    // the source of the repeated "negative value is not valid" console errors.
    return Math.max(240, Math.min(620, target));
}

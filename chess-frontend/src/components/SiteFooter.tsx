import { Link } from 'react-router-dom';
import { BUILD_VERSION } from '../buildInfo';
import './SiteFooter.css';

/**
 * The quiet line at the bottom of the account and information pages.
 *
 * WHERE IT IS NOT
 * ---------------
 * It is deliberately absent from the three-mode app shell. That layout is
 * height-fitted - `useFittedBoardSize` measures the space the board is allowed
 * to occupy - and CLAUDE.md traps 8 and 11 each record an occasion where an
 * element added to that layout pushed the board frame out of its own column.
 * A footer there would be a fourth thing competing for the same vertical
 * space, and the cost of getting it wrong is a broken board rather than an
 * ugly footer. On the app shell, About and the version live in the account
 * dropdown, which is already there and already scrolls.
 *
 * So this renders only on surfaces that scroll and hold no board: sign in,
 * sign up, settings, about, and the two password-reset pages.
 *
 * WHY THE VERSION IS HERE
 * -----------------------
 * The frontend and the backend deploy separately and skew silently
 * (CLAUDE.md section 2). A visible build id turns "is this the new one?" into
 * something a person can read off the page and quote back, instead of a
 * question only a maintainer with a terminal can answer.
 */
export function SiteFooter() {
    return (
        <footer className="site-footer">
            <Link className="site-footer-link" to="/about">About</Link>
            <span className="site-footer-sep" aria-hidden="true">·</span>
            <Link className="site-footer-link" to="/">Play</Link>
            <span className="site-footer-version" title="The build this page came from">
                {BUILD_VERSION}
            </span>
        </footer>
    );
}

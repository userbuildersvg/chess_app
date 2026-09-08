/**
 * Which build this bundle is.
 *
 * Vite replaces `__BUILD_VERSION__` at build time (see vite.config.ts), which
 * is the only way to know: the value has to be baked in, because by the time
 * the page is running there is nothing left to ask.
 *
 * It exists because the frontend and the backend deploy separately and skew
 * silently - CLAUDE.md section 2 records a deploy where Vercel picked up
 * Post-Mortem and Render did not, and the symptom a user saw was "Not Found -
 * try another file" on a perfectly good PGN. A build id on the page and one
 * on /api/health turns that into a comparison anybody can make.
 */
declare const __BUILD_VERSION__: string;

/** The short commit this bundle was built from, or "dev" outside a platform build. */
export const BUILD_VERSION: string =
    typeof __BUILD_VERSION__ === 'string' ? __BUILD_VERSION__ : 'dev';

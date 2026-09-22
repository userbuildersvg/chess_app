/**
 * Whether a verifier is allowed to spend real Gemini quota.
 *
 * Several tools here drive the coach for real: they ask a question, press
 * "Show me what I missed", or let the AI move, and each of those is a
 * provider call made by the backend they are pointed at. That is the right
 * thing for a live smoke test and the wrong thing for ordinary verification -
 * running the suite becomes a burst of traffic on one shared API key, which
 * is how a demo gets 429s from its own test run.
 *
 * So the live paths are opt-in:
 *
 *     node tools/verify/loop.mjs                # provider-free, skips them
 *     LIVE_GEMINI=1 node tools/verify/loop.mjs  # the real thing
 *
 * A skip is printed loudly and exits 0: it is a deliberate choice, not a
 * pass, and the line says exactly what was not checked and how to check it.
 */

export const LIVE = process.env.LIVE_GEMINI === '1';

/**
 * End the run cleanly when the whole tool is a live-provider test.
 *
 * For scripts whose entire subject is the coach speaking. Exits 0, because
 * "not run" is not a failure - CI and a routine sweep both want this to be
 * a clean, visible no-op rather than a red cross.
 */
export function requireLive(what) {
    if (LIVE) return true;
    console.log(`SKIP  ${what}`);
    console.log('      This tool calls Gemini for real. Set LIVE_GEMINI=1 to run it:');
    console.log(`      LIVE_GEMINI=1 node ${process.argv[1].split('/').slice(-3).join('/')}`);
    process.exit(0);
}

/** One line for a section skipped inside an otherwise provider-free tool. */
export function skipLive(what) {
    console.log(`SKIP  ${what} (needs LIVE_GEMINI=1)`);
    return false;
}

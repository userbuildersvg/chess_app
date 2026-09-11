/**
 * How long a move actually takes, measured rather than guessed.
 *
 * A beta tester said "the AI takes too long to play a move". The engine time
 * was about a second - but nobody had measured what the other half of the wait
 * was made of, and the first attempt at a fix would have been to cut the game
 * history out of the API call, which is the one thing that must not happen:
 * Zugzwang coaches from the game so far, and a coach handed only the current
 * FEN invents the history it was not given.
 *
 * So this measures first. Each stage of a move marks itself, and the whole
 * sequence prints as one group when the move completes. What it showed on the
 * dev stack was that a player's own move cost two round trips before the piece
 * moved at all - a `/api/status` re-sync and then `/api/move` - so a large part
 * of "the AI is slow" was the board not responding to the human.
 *
 * Off unless asked for. It is a diagnostic, not telemetry: nothing is sent
 * anywhere, and a console full of timings during ordinary play is noise. Turn
 * it on in the browser console with
 *
 *     localStorage.setItem('zugzwang-move-timing', '1')
 *
 * and reload. The marks are cheap enough (`performance.now()` and a push) that
 * the disabled path costs nothing worth measuring.
 */

const STORAGE_KEY = 'zugzwang-move-timing';

let enabled = false;
try {
    enabled = localStorage.getItem(STORAGE_KEY) === '1';
} catch {
    // Private mode, or storage blocked. Off is the right answer.
}

type Mark = { label: string; at: number };
let marks: Mark[] = [];

/** Whether timings are being collected. Exported so a caller can skip work. */
export const moveTimingEnabled = (): boolean => enabled;

/**
 * Record that a stage finished, `since` milliseconds-origin the move started.
 *
 * The elapsed figure is measured from the start of the move rather than from
 * the previous mark, because the question being asked is always "how long has
 * the player been waiting", not "how long did this step take".
 */
export function markMoveTiming(label: string, since: number): void {
    if (!enabled) return;
    const at = performance.now() - since;
    marks.push({ label, at });
    console.log(`⏱ ${label}: ${at.toFixed(0)}ms`);
}

/**
 * Close out a move and print the whole sequence together.
 *
 * The stages this app cares about, in order: the player's move submitted, the
 * board showing it, the server answering, the AI's move arriving, the board
 * showing that, and the explanation rendering. The last two are separate on
 * purpose - the move is what the player is waiting for, and if the explanation
 * ever starts arriving after it, that is the number that says so.
 */
export function endMoveTiming(label = 'move complete', since?: number): void {
    if (!enabled) return;
    if (since !== undefined) markMoveTiming(label, since);
    marks = [];
}

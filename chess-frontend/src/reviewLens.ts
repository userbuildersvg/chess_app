/**
 * The coach lens: who Review's explanations are written for.
 *
 * Phrasing only. The seven levels are the same seven names Play and Learn
 * use for an opponent, so the two pickers read alike, but here a level is a
 * READER - "Club player" is how the coach explains, not how anyone plays -
 * and nothing about the engine's evidence changes with it. The chosen id
 * travels as `coach_style.lens` on Review's chat call and becomes one
 * paragraph in the style block (coach_style.LENS, backend).
 *
 * Two more choices sit under the seven:
 *  - "Matched to me" needs a server calibration that does not exist yet, so
 *    it is shown locked with the honest reason.
 *  - "Explain at opponent level" reads the opponent's Elo from the PGN's
 *    own WhiteElo/BlackElo header and picks the nearest of the seven. That
 *    is all it knows: a number the file carried. It never looks anyone up.
 */
import { OPPONENT_PROFILES, profileById } from './opponentProfiles';

export const LENS_KEY = 'postmortem-coach-lens';
export const OPPONENT_LENS = 'opponent';

export const LENS_OPTIONS: { id: string; label: string; blurb: string }[] = [
    { id: 'beginner', label: 'Beginner explanation', blurb: 'Names the pieces and squares, says what a threat is, one idea at a time.' },
    { id: 'casual', label: 'Casual explanation', blurb: 'Plain words, spells out why a capture or check matters, short lines.' },
    { id: 'improving', label: 'Improving player', blurb: 'Names the tactic and shows the short line that proves it.' },
    { id: 'club', label: 'Club player', blurb: 'Standard vocabulary, one concrete line, the plan the move served or broke.' },
    { id: 'advanced', label: 'Advanced player', blurb: 'Assumes tactics are seen; spends the words on the positional reason.' },
    { id: 'expert', label: 'Expert player', blurb: 'Terse and precise, engine lines welcome, the subtle point over the obvious one.' },
    { id: 'master', label: 'Master-level detail', blurb: 'Dense notation, no basics, the critical variation and the reasoning only.' },
];

export function readLens(): string | null {
    try {
        const v = localStorage.getItem(LENS_KEY);
        return v && (v === OPPONENT_LENS || LENS_OPTIONS.some(o => o.id === v)) ? v : null;
    } catch {
        return null;
    }
}

export function writeLens(id: string | null): void {
    try {
        if (id) localStorage.setItem(LENS_KEY, id); else localStorage.removeItem(LENS_KEY);
    } catch {
        // storage unavailable - the choice lasts this render
    }
}

/** The opponent's rating from the PGN headers, when the file carried one. */
export function opponentElo(headers: Record<string, string>, playerColor: 'white' | 'black' | null): number | null {
    if (!playerColor) return null;
    const raw = headers[playerColor === 'white' ? 'BlackElo' : 'WhiteElo'];
    const n = raw ? parseInt(raw, 10) : NaN;
    return Number.isFinite(n) && n > 0 ? n : null;
}

/** The nearest of the seven levels to a rating. */
export function levelForElo(elo: number): string {
    let best = OPPONENT_PROFILES[0];
    for (const p of OPPONENT_PROFILES) {
        if (Math.abs(p.approxElo - elo) < Math.abs(best.approxElo - elo)) best = p;
    }
    return best.id;
}

/** The lens id to send: a level, the opponent's bucket, or none. */
export function resolveLens(lens: string | null, headers: Record<string, string>, playerColor: 'white' | 'black' | null): string | null {
    if (lens === OPPONENT_LENS) {
        const elo = opponentElo(headers, playerColor);
        return elo === null ? null : levelForElo(elo);
    }
    return lens;
}

/** What the trigger says. */
export function lensLabel(lens: string | null, headers: Record<string, string>, playerColor: 'white' | 'black' | null): string {
    if (!lens) return 'Coach lens: standard';
    if (lens === OPPONENT_LENS) {
        const id = resolveLens(lens, headers, playerColor);
        return id ? `Opponent level — ${profileById(id).label}` : 'Coach lens: standard';
    }
    return LENS_OPTIONS.find(o => o.id === lens)?.label ?? 'Coach lens: standard';
}

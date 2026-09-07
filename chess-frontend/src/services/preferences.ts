import { accountService, type Prefs } from './accountService';

/**
 * Where a preference lives, which depends on who is asking.
 *
 * These five settings - piece set, coordinates, engine numbers, move grading,
 * open rail panel - have always been `localStorage` keys read directly by
 * `ChessBoard.tsx`. That is still exactly right for a guest: there is nowhere
 * else to put them and they should not outlive the browser.
 *
 * It became wrong for an account. Once games, profile and history follow a
 * person to another device, their board arriving as a stranger's is a visible
 * inconsistency - and a settings screen that presents device-local values as
 * "your account settings" is telling the user something untrue.
 *
 * So: `localStorage` remains the value the app reads, synchronously, on every
 * render - no loading state, no flicker, nothing to refactor in ChessBoard
 * beyond swapping the accessor. When signed in, it is additionally a cache of
 * the account's stored preferences: pulled down at sign-in, pushed up on
 * change. Signed out, the push is skipped and the behaviour is what it always
 * was.
 *
 * THE ONE THING TO KNOW
 * ---------------------
 * `hydrateFromAccount()` overwrites local values with the account's. It runs
 * on sign-in, where that is what the user means - "my settings, on this
 * device". It must NOT run on every page load for an already-signed-in user
 * without also reloading the UI, or a preference changed in this tab would be
 * silently reverted by a stale copy from the server.
 */

/** localStorage key -> the name the server knows it by. */
const KEYS = {
    'chess-piece-theme': 'pieceTheme',
    'chess-coordinates': 'showCoordinates',
    'chess-engine-numbers': 'showEngineNumbers',
    'chess-move-quality': 'showMoveQuality',
    'chess-active-section': 'activeSection',
} as const;

type LocalKey = keyof typeof KEYS;

/** Whether the last identity check said we are signed in. Set by the app
 *  shell; preferences never ask the server themselves, because they are read
 *  during render and a network call there would be a bug. */
let signedIn = false;

export function setSignedIn(value: boolean) {
    signedIn = value;
}

export function readLocal(key: LocalKey): string | null {
    try {
        return localStorage.getItem(key);
    } catch {
        // Private browsing, or storage disabled. The caller falls back to its
        // own default, which is what it did before any of this existed.
        return null;
    }
}

/**
 * Write a preference. Local always; the account too, when there is one.
 *
 * The server write is deliberately unawaited and its failure swallowed. A
 * preference that did not sync is a preference that is still correct on this
 * device and will be corrected on the next change - it is not worth an error
 * in front of someone who just toggled coordinates on.
 */
export function writeLocal(key: LocalKey, value: string) {
    try {
        localStorage.setItem(key, value);
    } catch {
        // Storage unavailable - the setting just will not persist locally.
    }
    if (!signedIn) return;
    const remoteKey = KEYS[key];
    const parsed: Partial<Prefs> = {};
    if (remoteKey === 'pieceTheme' || remoteKey === 'activeSection') {
        parsed[remoteKey] = value as never;
    } else {
        parsed[remoteKey] = (value === 'true') as never;
    }
    accountService.savePrefs(parsed).catch(() => {
        /* see the note above: a failed sync is not worth interrupting anyone */
    });
}

/**
 * Pull this account's preferences into local storage.
 *
 * Returns true if anything actually changed, so the caller can decide whether
 * a reload is warranted - the app reads these during render and will not pick
 * up a change made underneath it.
 */
export async function hydrateFromAccount(): Promise<boolean> {
    let prefs: Prefs;
    try {
        prefs = await accountService.prefs();
    } catch {
        // No account, or the server is unreachable. Local values stand.
        return false;
    }
    let changed = false;
    for (const [localKey, remoteKey] of Object.entries(KEYS) as [LocalKey, keyof Prefs][]) {
        const value = prefs[remoteKey];
        // A null pieceTheme means "never chosen" - leave whatever this device
        // has rather than clearing it to the default.
        if (value === null || value === undefined) continue;
        const asString = String(value);
        if (readLocal(localKey) !== asString) {
            try {
                localStorage.setItem(localKey, asString);
                changed = true;
            } catch {
                /* storage unavailable */
            }
        }
    }
    return changed;
}

/**
 * Push whatever this device currently has up to a newly created account.
 *
 * Called once, right after signup. Someone who has spent a while as a guest
 * choosing a board and turning the eval bar on should not have those choices
 * quietly discarded by the act of creating an account - which is exactly what
 * would happen if the new account's empty defaults were pulled down instead.
 */
export async function seedAccountFromLocal(): Promise<void> {
    const prefs: Partial<Prefs> = {};
    for (const [localKey, remoteKey] of Object.entries(KEYS) as [LocalKey, keyof Prefs][]) {
        const raw = readLocal(localKey);
        if (raw === null) continue;
        if (remoteKey === 'pieceTheme' || remoteKey === 'activeSection') {
            prefs[remoteKey] = raw as never;
        } else {
            prefs[remoteKey] = (raw === 'true') as never;
        }
    }
    if (Object.keys(prefs).length === 0) return;
    try {
        await accountService.savePrefs(prefs);
    } catch {
        /* the account simply starts with defaults */
    }
}

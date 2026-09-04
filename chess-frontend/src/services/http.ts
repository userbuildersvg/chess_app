/**
 * One fetch wrapper, for two reasons: the identity cookie has to travel, and
 * it has to exist before anything else asks for it.
 *
 * CARRYING THE COOKIE
 * -------------------
 * The backend decides whose board you get from an HttpOnly cookie
 * (`identity.py`). A request that does not carry it looks like a brand-new
 * visitor, so the server mints a fresh guest, hands back an empty board, and
 * the game appears to reset itself at random. That failure is silent - no
 * error, no console warning, just a board that keeps forgetting.
 *
 * `credentials: 'include'` prevents it. The browser default (`same-origin`)
 * is enough in development, where Vite proxies /api to the backend, and in
 * production, where Vercel rewrites /api to Render for the same reason. It is
 * NOT enough the moment the frontend talks to the API's own URL directly - a
 * preview build pointed at Render, a local page against a deployed backend -
 * which is exactly the configuration someone reaches for when debugging.
 *
 * ESTABLISHING IT FIRST
 * ---------------------
 * A brand-new visitor has no cookie at all, and the app opens by firing
 * several calls at once (the game's status poll, and Learner Mode's session
 * creation if it opens on the restored mode). Each of those arrives with no
 * cookie, so the server mints a SEPARATE guest identity for each, and the
 * browser keeps whichever Set-Cookie landed last. Everything created under
 * the others is orphaned - owned by an identity the browser no longer has.
 *
 * That was not theoretical: it showed up as a 404 on the sandbox's own
 * StrictMode cleanup DELETE, because the session had been created under one
 * identity and was being deleted under another. The same race would have
 * silently separated a visitor's chess game from their sandbox session.
 *
 * So the first call through here establishes identity and every other call
 * waits on it. One request, once per page load, and after it the cookie
 * exists and every subsequent request agrees about who is asking. The
 * bootstrap deliberately uses bare `fetch` rather than `apiFetch`, or it
 * would wait on itself.
 */

/** Resolves once this page load has an identity cookie. Started at most once. */
let identityReady: Promise<void> | null = null;

function ensureIdentity(): Promise<void> {
    if (identityReady === null) {
        identityReady = fetch('/api/auth/me', { credentials: 'include' })
            .then(
                () => undefined,
                // A failed bootstrap must not deadlock the app. If the call
                // cannot be made at all, carry on: the first real request
                // then mints the cookie exactly as it did before, and the
                // worst case is the race this exists to avoid - not a page
                // that never loads.
                () => undefined,
            );
    }
    return identityReady;
}

export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    await ensureIdentity();
    return fetch(input, {
        ...init,
        credentials: 'include',
    });
}

import type { CapacitorConfig } from '@capacitor/cli';

/**
 * The Android shell around the live web app.
 *
 * WHY `server.url` AND NOT A BUNDLED BUILD
 * ----------------------------------------
 * This app talks to its API at RELATIVE paths - `fetch('/api/...')` in
 * `services/http.ts` - and that works because `vercel.json` rewrites
 * `/api/*` to the Render backend server-side. The browser therefore only
 * ever sees one origin, which is also what makes the identity cookie
 * first-party and lets `SameSite=Lax` and the CSRF origin check do their
 * jobs (CLAUDE.md §28).
 *
 * A bundled Capacitor build serves the SPA from `https://localhost` or
 * `file://`, where `/api/*` resolves to nothing at all. Making that work
 * would mean an absolute API base, CORS on the backend, `SameSite=None`
 * cookies and a re-examination of the CSRF rules - four changes to the
 * security boundary, days before a deadline, to gain nothing a webview
 * pointed at the real site does not already have.
 *
 * So the shell loads the deployed site. The Android app is the same product,
 * served by the same backend, with the same session and the same billing -
 * and every web fix reaches it without a Play Store release.
 *
 * `webDir` still points at `dist` because the CLI requires a build directory
 * to exist; with `server.url` set, its contents are not what the device runs.
 */
const config: CapacitorConfig = {
  appId: 'app.zugzwang.chess',
  appName: 'Zugzwang',
  webDir: 'dist',
  server: {
    url: 'https://chess-app-rho-swart.vercel.app',
    // The deployed site is HTTPS, so cleartext stays off.
    cleartext: false,
  },
  android: {
    // The board is drawn by the page; nothing here should paint over it
    // while it loads.
    backgroundColor: '#12140F',
  },
};

export default config;

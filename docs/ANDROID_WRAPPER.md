# The Android wrapper

## What it is

A Capacitor shell that opens the deployed Zugzwang web app in an Android
WebView. It exists so the product can be installed and judged as an Android
app; it is not a rewrite, a port, or a second implementation of anything.

| | |
| --- | --- |
| App name | Zugzwang |
| Package id | `app.zugzwang.chess` |
| Lives in | `chess-frontend/` (the Capacitor project) and `chess-frontend/android/` (the generated Android project) |
| Loads | `https://chess-app-rho-swart.vercel.app` — the live site |
| Device-tested | Galaxy S23, Android 16, 2026-09-23 — the full demo path |
| Capacitor | 8.5.x (`@capacitor/core`, `@capacitor/cli`, `@capacitor/android`, all devDependencies) |

## What it is not

- **Not a second product.** There is no native chess code, no native board, no
  native auth and no native billing. Every rule, every evaluation and every
  saved lesson is the same server doing the same work.
- **Not Google Play Billing.** Subscriptions remain RevenueCat Web Billing in
  the browser, which is what the web app already does and what
  `docs/SHIPATON_READINESS.md` describes.
- **Not an offline app.** It needs a network, exactly as the website does.
- **Not a separate deployment.** A fix shipped to Vercel reaches the installed
  app on its next launch, with no store release.

## Why `server.url`, and not a bundled build

This is the one decision in the wrapper worth understanding.

The frontend calls its API at **relative paths** — `fetch('/api/...')` in
`services/http.ts` — and that works because `chess-frontend/vercel.json`
rewrites `/api/*` to the Render backend **server-side**. The browser only ever
sees one origin, which is also what makes the identity cookie first-party and
lets `SameSite=Lax` plus the CSRF origin check do their jobs.

A bundled Capacitor build serves the SPA from `https://localhost` (or
`file://`), where `/api/*` resolves to nothing. Making that work would mean:

1. an absolute API base in the frontend,
2. CORS on the backend for a new origin,
3. `SameSite=None` cookies — which is the exact hole that was deliberately
   closed, since about twenty POST routes take no request body, and
4. re-examining the CSRF origin rules against a WebView origin.

Four changes to the security boundary, days before a deadline, to gain
nothing that a WebView pointed at the real site does not already have. So
`capacitor.config.ts` sets `server.url` to the deployed site and the shell
loads it. `webDir: 'dist'` is still required by the CLI (it copies a build
into the project), but with `server.url` set, those copied assets are not
what the device runs.

If the wrapper is ever taken further — offline play, push notifications, a
Play Store subscription — that is the point at which the four items above
have to be done properly, and none of them should be rushed.

## Working on it

Everything runs from `chess-frontend/`:

```bash
cd chess-frontend

npm run cap:sync:android     # build the web app, then sync it into android/
npm run cap:open:android     # open the project in Android Studio
```

or the underlying commands:

```bash
npm run build
npx cap sync android
npx cap open android
```

`cap sync` is the one to run after changing `capacitor.config.ts` or adding a
plugin. Nothing in `android/` should be edited by hand that Capacitor
generates — the exception is the Android-specific bits a store release needs
(icons, `AndroidManifest.xml` permissions, signing config), which are yours to
keep.

## Building an APK / AAB

**Required** (present on this machine under `~/android-build`, installed
without `sudo` - see the checklist's appendix):

- **JDK 21.** Not 17 - Capacitor 8.5 compiles against Java 21, and a JDK 17
  build stops at `invalid source release: 21`. Not 25 either: AGP 8.13
  rejects it. Android Studio's bundled JBR is 21 and is the simplest answer.
- **Android SDK** — platform **36**, build-tools 36.0.0 and platform-tools,
  through Android Studio or the command-line tools
- `ANDROID_HOME` (or `sdk.dir` in `android/local.properties`) pointing at it

The Gradle wrapper (`android/gradlew`) is already in the project, so Gradle
itself does not need installing.

```bash
cd chess-frontend/android

./gradlew assembleDebug        # app/build/outputs/apk/debug/app-debug.apk
./gradlew bundleRelease        # app/build/outputs/bundle/release/  (needs signing)
```

For a Play Store upload the release build has to be signed; for judging, a
debug APK installed with `adb install app-debug.apk` is enough.

**[ANDROID_BUILD_CHECKLIST.md](ANDROID_BUILD_CHECKLIST.md)** is the
step-by-step version of this for Windows, with the exact SDK components, the
JDK trap, and the on-device checks that have to pass before the APK may be
called tested.

## Known limitations

- **No offline mode.** No network, no app — the same as the website.
- **The back button** is Capacitor's default (it pops WebView history and then
  exits). Nothing custom has been wired.
- **No deep links.** Opening a `zugzwang` URL from elsewhere is not handled.
- **No push notifications**, no native share, no native file picker: the PGN
  upload is the browser's own file input inside the WebView.
- **Device-tested once, on one phone.** A Samsung Galaxy S23 (Android 16) on
  2026-09-23: installed, launched, and the whole demo path walked - homepage,
  focused Review, a PGN through the Android file picker, the scan, the saved
  lesson, practice on the main board, End practice. No crash and no blank
  screen. One device is not a device matrix, and nothing has been tried on a
  tablet, on Android 10-13, or on a slow connection.

## The statement that matters

Zugzwang is a **web and server-backed product**. The Android wrapper
distributes it; it does not reimplement it. The engine (Stockfish), the model
calls (Gemini), the accounts, the saved lessons and the improvement profile
all run on the backend described in [ARCHITECTURE.md](ARCHITECTURE.md), and
the Android app is one more client of it.

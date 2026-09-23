# Android build checklist (Windows)

Work through this at the keyboard. Every version below was read out of the
generated project, not assumed.

**Nothing here has been run.** The project is generated and `npx cap sync
android` succeeds; no APK has been built and nothing has run on a device. The
point of this list is to get from that state to a working install, and the
last step is the one that earns the word "tested".

| | |
| --- | --- |
| Project to open | `C:\Users\David\Documents\chess-app-v3.9\chess-frontend\android` |
| App / package | Zugzwang / `app.zugzwang.chess` |
| What it loads | `https://chess-app-rho-swart.vercel.app` (`server.url`) |
| minSdk / compileSdk / targetSdk | 24 / 36 / 36 |
| Android Gradle Plugin | 8.13.0 |
| Gradle | 8.14.3 (downloaded by the wrapper — do not install Gradle) |
| JDK | **17 or 21.** Android Studio's bundled JBR is the right one |

> The repository lives on the Windows drive, so Android Studio opens it
> directly — there is no copying out of WSL and no `\\wsl$` path involved.

---

## 1. Install Android Studio

- [ ] Download Android Studio (Windows, .exe) from
      <https://developer.android.com/studio> and install with the defaults.
- [ ] First launch → **Standard** setup, accept the SDK licences, let it
      finish downloading.
- [ ] Do **not** install a separate JDK. The Adoptium **JDK 25** already on
      this machine is rejected by AGP 8.13; Android Studio ships its own JBR
      and that is the one to use.

## 2. Open the project

- [ ] **Open** (not *New Project*, not *Import*) and select exactly:
      `C:\Users\David\Documents\chess-app-v3.9\chess-frontend\android`
- [ ] Let the first Gradle sync run to completion. It downloads Gradle 8.14.3
      and the Android dependencies; on a first run this takes a while.
- [ ] If it offers to upgrade AGP or Gradle: **decline.** The versions above
      are what the project was generated against.

## 3. SDK components

**File → Settings → Languages & Frameworks → Android SDK**

- [ ] *SDK Platforms* → **Android API 36** installed.
- [ ] *SDK Tools* → **Android SDK Build-Tools**, **Platform-Tools**
      (this is `adb`), **Android Emulator**, and **Android SDK Command-line
      Tools**.
- [ ] *Gradle JDK* (**Settings → Build, Execution, Deployment → Build Tools →
      Gradle**) is the **bundled JBR**, or any JDK 17/21 — **not** 25.
- [ ] Sync again if anything was installed. A green sync is the gate for
      step 4.

Optional, if you prefer the command line afterwards: add
`...\AppData\Local\Android\Sdk\platform-tools` to PATH so `adb` works in a
terminal.

## 4. Build the debug APK

- [ ] **Build → Build Bundle(s) / APK(s) → Build APK(s)**, or in a terminal
      at the project root:

      ```
      cd C:\Users\David\Documents\chess-app-v3.9\chess-frontend\android
      .\gradlew.bat assembleDebug
      ```

- [ ] Output lands at:
      `chess-frontend\android\app\build\outputs\apk\debug\app-debug.apk`
- [ ] If the web app changed since the project was generated, run
      `npm run cap:sync:android` from `chess-frontend\` first. (With
      `server.url` set, the device loads the live site rather than the copied
      bundle, so this matters less than it normally would — but keep the
      habit.)

## 5. Run it

**Emulator**

- [ ] Device Manager → create a device (Pixel, API 36) → start it.
- [ ] Press **Run ▶**, target the emulator.

**Physical device**

- [ ] On the phone: Settings → About phone → tap *Build number* 7× →
      Developer options → **USB debugging** on.
- [ ] Plug in over USB, accept the debugging prompt.
- [ ] `adb devices` should list it, then press **Run ▶** (or
      `adb install -r app\build\outputs\apk\debug\app-debug.apk`).

Either way the device needs **internet** — the app loads the live site, so
there is nothing to see offline.

## 6. Verify it is actually the product

This is the step that decides whether "the Android app works" is a true
sentence. Do all of it on the device.

- [ ] The app opens on the **Zugzwang homepage** — the headline "The chess
      coach that remembers why you keep making the same mistakes." A white or
      black screen here means the WebView never reached the site: check the
      device's connection first, then Logcat.
- [ ] Tap **Analyze a game right now** → the focused Review screen appears,
      with the guest note about creating an account.
- [ ] Load a game. Two ways, and the second needs no file on the phone:
      - **Choose a game file** → pick a `.pgn` you have put on the device, or
      - go to **Play**, play a short game against the coach, then use
        **Review this game** at the end.
- [ ] The scan completes and the **Report** appears with *Your biggest
      learning opportunity*.
- [ ] **Work through this decision** → pick an intent → **Show me what I
      missed** → a saved lesson is written.
- [ ] **Practise this idea** → the practice position appears **on the main
      board** → **End practice** → the reviewed game comes back.
- [ ] Check while you are there: the board fits the screen, nothing scrolls
      sideways, the header is not overlapping, and the back button behaves
      sanely (it pops WebView history, then exits).

**Only after every box in step 6 is ticked** may the APK be described as
tested, and `docs/ANDROID_WRAPPER.md`'s "nothing has run on a device" line be
updated.

---

## If it fails

| Symptom | Almost certainly |
| --- | --- |
| Gradle sync fails on a JDK error | Gradle JDK is set to 25. Point it at the bundled JBR (step 3). |
| `SDK location not found` | Open the project through Android Studio once so it writes `local.properties`, or set `ANDROID_HOME`. `local.properties` is git-ignored — do not commit it. |
| Blank/white screen on launch | No network, or the WebView could not reach the site. `INTERNET` permission is present in the manifest, so it is not that. |
| App loads but the board never moves | The backend is asleep (Render free tier). Open `https://zugzwang-api.onrender.com/api/health` once and retry. |
| "App not installed" on the phone | An older `app.zugzwang.chess` is installed with a different signature — uninstall it first. |

## What this build is not

A debug APK is unsigned for the Play Store and is fine for judging and
sideloading. A store release additionally needs `bundleRelease`, a signing
key, icons and a listing — none of which is set up, and none of which is
needed to demonstrate the app.

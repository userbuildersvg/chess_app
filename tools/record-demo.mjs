/**
 * Record the Zugzwang demo path, deterministically, for video material.
 *
 * This is a camera, not a test. It drives the real product at a pace a
 * viewer can follow and writes one video file; if a step cannot be found it
 * stops, screenshots what was on screen, and says which step failed - a
 * silent half-recording is worse than none.
 *
 * Three things it does that an ordinary Playwright script does not:
 *
 *   - **A visible cursor.** Playwright's video does not render the mouse, so
 *     clicks would appear as things happening by themselves. A small dot is
 *     injected into the page (by this script, at runtime - nothing in the
 *     product knows about it) and moved deliberately before each click, so
 *     the recording reads as somebody using the app.
 *   - **Deliberate pacing.** Every step pauses either side of the click.
 *     The app is fast; a video that keeps up with it is unwatchable.
 *   - **It waits for the real thing.** The whole-game scan and the saved
 *     lesson are waited for by their own signals, not by a fixed sleep, so
 *     the clip never films a spinner it has outrun.
 *
 * Usage:
 *
 *     node tools/record-demo.mjs                      # the live site
 *     node tools/record-demo.mjs http://localhost:3001
 *
 * Output: artifacts/zugzwang-web-demo-auto.webm, and the same as .mp4 when
 * ffmpeg is on PATH (H.264, yuv420p, faststart - the format nobody has to
 * think about).
 *
 * It makes ONE Gemini call (the saved lesson). If the provider is down the
 * engine-backed card appears instead and the recording still completes -
 * that is the product working, and it is fine to film.
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';
import { mkdirSync, writeFileSync, existsSync, renameSync, statSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const ARTIFACTS = join(ROOT, 'artifacts');
const BASE = process.argv[2]?.startsWith('http')
    ? process.argv[2].replace(/\/$/, '')
    : 'https://chess-app-rho-swart.vercel.app';
const HEALTH = 'https://zugzwang-api.onrender.com/api/health';
const OUT = join(ARTIFACTS, 'zugzwang-web-demo-auto.webm');
const MP4 = join(ARTIFACTS, 'zugzwang-web-demo-auto.mp4');
const PGN_PATH = join(ARTIFACTS, 'demo-game.pgn');

/** Morphy's Opera Game: short, decisive, and every grade in it is real. */
const PGN = `[Event "Paris Opera"]
[Site "Paris"]
[Date "1858.11.02"]
[White "Morphy"]
[Black "Duke Karl / Count Isouard"]
[WhiteElo "2400"]
[BlackElo "1600"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
`;

const wait = ms => new Promise(r => setTimeout(r, ms));
let step = 0;
const say = msg => console.log(`  ${String(++step).padStart(2)}. ${msg}`);

mkdirSync(ARTIFACTS, { recursive: true });
if (!existsSync(PGN_PATH)) writeFileSync(PGN_PATH, PGN);

// --- warm the backend -------------------------------------------------------
// Render's free tier sleeps. A cold start in the middle of a take is 30
// seconds of nothing, so it is paid for before the camera rolls.
process.stdout.write('warming the backend... ');
const warmStarted = Date.now();
try {
    const res = await fetch(HEALTH);
    const body = await res.json();
    console.log(`${res.status} in ${Date.now() - warmStarted}ms (build ${body.version ?? '?'})`);
} catch (err) {
    console.log(`could not reach the API: ${err.message}`);
}

const browser = await chromium.launch();
const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    // The dark theme is the one the product is designed around and the one
    // that reads on a projector.
    colorScheme: 'dark',
    recordVideo: { dir: ARTIFACTS, size: { width: 1440, height: 900 } },
});

// The theme is a stored preference, so it is set before the first paint
// rather than toggled on camera.
await context.addInitScript(() => {
    try { localStorage.setItem('zugzwang-theme', 'dark'); } catch { /* private mode */ }
});

// The cursor. Injected by this script only - the product has no idea.
await context.addInitScript(() => {
    const draw = () => {
        if (document.getElementById('__demo_cursor')) return;
        const dot = document.createElement('div');
        dot.id = '__demo_cursor';
        dot.style.cssText = [
            'position:fixed', 'z-index:2147483647', 'left:0', 'top:0',
            'width:22px', 'height:22px', 'margin:-11px 0 0 -11px',
            'border-radius:50%', 'pointer-events:none',
            'background:rgba(200,179,138,0.35)',
            'border:2px solid rgba(240,232,214,0.95)',
            'box-shadow:0 0 12px rgba(0,0,0,0.5)',
            'transition:transform 90ms linear',
        ].join(';');
        document.body.appendChild(dot);
        addEventListener('mousemove', e => {
            dot.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
        }, { passive: true });
        addEventListener('mousedown', () => { dot.style.background = 'rgba(200,179,138,0.75)'; });
        addEventListener('mouseup', () => { dot.style.background = 'rgba(200,179,138,0.35)'; });
    };
    if (document.body) draw();
    else addEventListener('DOMContentLoaded', draw);
});

const rollingSince = Date.now();
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });

/** Move the pointer to a locator, pause, then click - so the video reads. */
async function show(target, label, { settle = 900, after = 1400 } = {}) {
    // `.first()` on purpose: the page legitimately carries the same call to
    // action twice (the hero and the closing CTA), and a union locator then
    // resolves to both. The first one is the one on screen.
    const locator = target.first();
    await locator.waitFor({ state: 'visible', timeout: 45000 });
    await locator.scrollIntoViewIfNeeded();
    await wait(400);
    const box = await locator.boundingBox();
    if (box) {
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 24 });
    }
    await wait(settle);
    await locator.click();
    say(label);
    await wait(after);
}

let completed = false;
let usedFallback = null;

try {
    // 1 --------------------------------------------------------- the homepage
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.mouse.move(720, 450, { steps: 10 });
    await wait(4200);            // let the viewer read the promise
    say('homepage');

    // 2 ------------------------------------------------- into a focused Review
    const analyze = page.getByTestId('home-analyze')
        .or(page.getByRole('button', { name: /Analyze a game right now/i }));
    await show(analyze, 'Analyze a game right now', { after: 2200 });

    // 3 ------------------------------------------------------------ the game
    // The file input is the drop zone's own; setting files drives exactly what
    // the "Choose a game file" button opens, without filming a file dialog.
    await page.locator('.pm-file-input').setInputFiles(PGN_PATH);
    say('a game is uploaded (Morphy - Opera Game)');
    await page.waitForSelector('.pm-board-column', { timeout: 60000 });
    await wait(1800);

    // 4 ------------------------------------------- wait for the real analysis
    process.stdout.write('      waiting for the whole-game scan');
    for (let i = 0; i < 90; i++) {
        const done = await page.evaluate(async () => {
            const id = localStorage.getItem('postmortem-game');
            if (!id) return false;
            const r = await fetch(`/api/postmortem/game/${id}/analysis`);
            return r.ok && (await r.json()).scan?.status === 'done';
        }).catch(() => false);
        if (done) break;
        process.stdout.write('.');
        await wait(1000);
    }
    console.log('');
    say('every move graded');
    await wait(3600);            // the Report lands on its own

    // 5 ------------------------------------------------- the decision that mattered
    const key = page.getByTestId('pm-key').first();
    await key.waitFor({ state: 'visible', timeout: 45000 });
    await key.scrollIntoViewIfNeeded();
    await wait(4600);            // let the viewer read the card
    say('"Your biggest learning opportunity"');

    const cta = page.getByTestId('pm-key-cta')
        .or(page.getByRole('button', { name: /Work through this decision/i }));
    await show(cta, 'Work through this decision', { after: 2000 });

    // 6 ------------------------------------------------------------ the intent
    const intent = page.getByRole('button', { name: 'Improve a piece', exact: true })
        .or(page.locator('.corr-chip').first());
    await show(intent, 'the intent: "Improve a piece"', { after: 1400 });

    // 7 ------------------------------------------------------ the saved lesson
    const submit = page.getByRole('button', { name: /Show me what I missed/i })
        .or(page.locator('.corr-primary', { hasText: /Show me what I missed/ }));
    await show(submit, 'Show me what I missed', { after: 600 });

    await page.waitForSelector('.corr-card', { timeout: 95000 });
    say('the saved lesson is written');
    // Which wrote it - the coach, or the engine-backed fallback.
    const cardText = await page.locator('.corr-card').first().innerText();
    usedFallback = /engine-backed|could not reach the coach/i.test(cardText) ? 'fallback' : 'coach';
    await page.locator('.corr-card').first().scrollIntoViewIfNeeded();
    await wait(7000);            // the longest pause: this is the payoff

    // 8 ---------------------------------------------------------- the practice
    const practice = page.getByTestId('corr-practice-available')
        .or(page.getByRole('button', { name: /Practise this idea/i }));
    if (await practice.first().count()) {
        await show(practice, 'Practise this idea', { after: 1200 });
        await page.waitForSelector('.pm-board-wrapper.is-practice', { timeout: 60000 });
        await page.locator('.pm-board-wrapper').first().scrollIntoViewIfNeeded();
        say('practice, on the main board');
        await wait(6500);        // hold on practice mode
        completed = true;
    } else {
        say('practice was not offered for this theme - stopping here');
        await wait(2500);
        completed = true;        // the lesson is the magic moment; this is a bonus
    }
} catch (err) {
    console.log(`\n  FAILED at step ${step + 1}: ${err.message.split('\n')[0]}`);
    await page.screenshot({ path: join(ARTIFACTS, 'record-demo-failure.png') }).catch(() => {});
    console.log(`  screenshot: artifacts/record-demo-failure.png`);
} finally {
    const video = page.video();
    await context.close();       // the video is only finalised on close
    await browser.close();
    if (video) {
        const tmp = await video.path();
        if (existsSync(OUT)) renameSync(OUT, `${OUT}.previous`);
        renameSync(tmp, OUT);
    }
}

// --- mp4, when ffmpeg is about --------------------------------------------
// WebM is what Playwright writes and most editors take it, but some do not,
// and an .mp4 is the format nobody has to think about. H.264 + yuv420p is
// the combination that plays on everything; faststart puts the index at the
// front so it streams rather than downloading whole.
let mp4 = null;
if (existsSync(OUT)) {
    try {
        execFileSync('ffmpeg', [
            '-y', '-loglevel', 'error', '-i', OUT,
            '-c:v', 'libx264', '-preset', 'slow', '-crf', '20',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
            MP4,
        ], { stdio: 'inherit' });
        mp4 = `${(statSync(MP4).size / 1024 / 1024).toFixed(1)} MB`;
    } catch {
        mp4 = null;   // no ffmpeg, or it refused: the webm is still the output
    }
}

const size = existsSync(OUT) ? (statSync(OUT).size / 1024 / 1024).toFixed(1) : '0';
console.log('');
console.log(`video        artifacts/zugzwang-web-demo-auto.webm  (${size} MB)`);
console.log(`mp4          ${mp4 ? `artifacts/zugzwang-web-demo-auto.mp4  (${mp4})` : 'not made - ffmpeg is not on PATH'}`);
console.log(`recorded     ${((Date.now() - rollingSince) / 1000).toFixed(0)}s of wall clock`);
console.log(`flow         ${completed ? 'completed' : 'INCOMPLETE - see the failure screenshot'}`);
console.log(`lesson       written by the ${usedFallback ?? 'n/a'}`);
console.log(`console      ${errors.length === 0 ? 'no errors' : [...new Set(errors)].join(' | ').slice(0, 300)}`);
process.exit(completed ? 0 : 1);

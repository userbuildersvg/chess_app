/**
 * A short clip of the real RevenueCat paywall, for the Shipaton video.
 *
 * The paywall is the one the product shows: it is drawn by RevenueCat's Web
 * SDK from the offering configured in their dashboard, opened through the
 * app's own `billingService.presentPaywall`. Nothing here changes product
 * code, billing config, or any account - the module is simply imported from
 * the running dev server and asked to draw, with a throwaway viewer id.
 *
 * Nothing is ever purchased: the cursor is kept away from "Start Pro".
 *
 *     node tools/record-paywall.mjs [http://localhost:3001]
 *
 * Output: artifacts/zugzwang-paywall-shot.webm (+ .mp4 when ffmpeg is about).
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';
import { mkdirSync, renameSync, statSync, existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUT_DIR = join(ROOT, 'artifacts');
const NAME = 'zugzwang-paywall-shot';
const BASE = process.argv[2] ?? 'http://localhost:3001';
const SIZE = { width: 1440, height: 900 };
mkdirSync(OUT_DIR, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({
    viewport: SIZE,
    recordVideo: { dir: OUT_DIR, size: SIZE },
    colorScheme: 'dark',
});

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
    };
    if (document.body) draw();
    else addEventListener('DOMContentLoaded', draw);
});

const page = await context.newPage();
const wait = ms => page.waitForTimeout(ms);

/** Slow, human mouse travel - Playwright's default jump reads as a glitch. */
let at = { x: 720, y: 500 };
async function moveTo(x, y, steps = 26) {
    for (let i = 1; i <= steps; i++) {
        await page.mouse.move(at.x + (x - at.x) * i / steps, at.y + (y - at.y) * i / steps);
        await wait(16);
    }
    at = { x, y };
}
/** Hover the middle of something, without clicking it. */
async function linger(locator, hold) {
    const box = await locator.boundingBox();
    if (!box) return false;
    await moveTo(box.x + Math.min(box.width / 2, 320), box.y + box.height / 2);
    await wait(hold);
    return true;
}

await page.goto(`${BASE}/?home`, { waitUntil: 'networkidle', timeout: 60000 });
await wait(2200);

// The product's own paywall call, with a viewer id that buys nothing.
const opened = await page.evaluate(async () => {
    const m = await import('/src/services/billingService.ts');
    if (!m.sdkAvailable()) return 'purchases are switched off in this build';
    m.billingService.presentPaywall('shipaton-demo-viewer')
        .catch(e => { window.__rcErr = String(e?.message ?? e); });
    return null;
});
if (opened) { console.log(`blocked: ${opened}`); await browser.close(); process.exit(1); }

const title = page.getByText(/Zugzwang Pro/i).first();
await title.waitFor({ state: 'visible', timeout: 25000 });
await wait(1600);

for (const [name, hold] of [['Yearly', 2000], ['Monthly', 1700], ['Lifetime', 1900]]) {
    await linger(page.getByText(new RegExp(`^${name}$`)).first(), hold);
}
await linger(title, 2600);          // back up to the headline, well clear of "Start Pro"

const err = await page.evaluate(() => window.__rcErr ?? null);
if (err) console.log(`paywall reported: ${err}`);

await page.close();
await context.close();
await browser.close();

// Playwright names the file after the session; give it the name we asked for.
const { readdirSync } = await import('node:fs');
const latest = readdirSync(OUT_DIR).filter(f => f.endsWith('.webm') && !f.startsWith('zugzwang-'))
    .map(f => join(OUT_DIR, f)).sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)[0];
const webm = join(OUT_DIR, `${NAME}.webm`);
if (latest) renameSync(latest, webm);

let mp4 = null;
if (existsSync(webm)) {
    try {
        execFileSync('ffmpeg', ['-v', 'error', '-y', '-i', webm, '-c:v', 'libx264', '-crf', '20',
            '-preset', 'slow', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
            join(OUT_DIR, `${NAME}.mp4`)]);
        mp4 = `${(statSync(join(OUT_DIR, `${NAME}.mp4`)).size / 1048576).toFixed(1)} MB`;
    } catch { /* no ffmpeg - the webm is still the deliverable */ }
}
console.log(`webm  artifacts/${NAME}.webm  (${(statSync(webm).size / 1048576).toFixed(1)} MB)`);
console.log(`mp4   ${mp4 ? `artifacts/${NAME}.mp4  (${mp4})` : 'not made'}`);

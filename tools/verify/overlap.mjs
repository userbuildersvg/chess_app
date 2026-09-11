/**
 * Geometric overlap sweep. For every mode, every panel tab, a set of
 * viewports and both themes: no two visible leaf-ish elements (controls,
 * labels, text blocks, tabs) may intersect unless one contains the other,
 * nothing may reach past the viewport's right edge, and nothing may escape
 * its column into the other one.
 *
 *     node tools/verify/overlap.mjs [http://localhost:3001] [--shots out/]
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';
import { mkdirSync } from 'node:fs';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';
const shotsAt = process.argv.indexOf('--shots');
const SHOTS = shotsAt > 0 ? process.argv[shotsAt + 1] : null;
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

let passed = 0, failed = 0;
const check = (label, ok, detail = '') => {
    if (ok) { passed++; console.log(`PASS  ${label}`); }
    else { failed++; console.log(`FAIL  ${label}${detail ? ` - ${detail}` : ''}`); }
};

const OPERA = `[Event "Paris Opera"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
`;

// Everything that is a "thing on screen" for the purpose of overlap: any
// interactive control, any tab, any text-bearing element with no element
// children (a leaf), plus the board frame. Ancestor/descendant pairs are
// allowed to intersect; siblings and cousins are not. Chessboard internals
// are one opaque box.
const SCAN = `(root) => {
    const sel = 'button, input, select, textarea, a, label, [role=tab], [role=tabpanel], h1, h2, h3, p, span, dt, dd, li, article, .chess-board-wrapper, .sandbox-board-wrapper, .pm-board-wrapper';
    const vis = el => {
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
    };
    const boardish = el => el.closest('[data-boardid], .chess-board-wrapper > div, [data-square]') && !el.matches('.chess-board-wrapper, .sandbox-board-wrapper, .pm-board-wrapper');
    const items = [];
    for (const el of root.querySelectorAll(sel)) {
        if (!vis(el) || boardish(el)) continue;
        // Skip containers that only wrap other things we already count,
        // unless they are controls or tabs.
        const isLeaf = el.children.length === 0 || el.matches('button, input, select, label, [role=tab], .chess-board-wrapper, .sandbox-board-wrapper, .pm-board-wrapper, .move-quality-badge');
        if (!isLeaf) continue;
        if (el.closest('.board-endstate, .promotion-picker, .ai-thinking-indicator, .move-quality-badge')) continue;
        // Clip to every ancestor that clips (overflow other than visible):
        // a row scrolled out of a list is not on screen and cannot overlap
        // anything. An element clipped to nothing is dropped.
        let r = el.getBoundingClientRect();
        let cx = r.left, cy = r.top, cr = r.right, cb = r.bottom;
        for (let an = el.parentElement; an && an !== document.body; an = an.parentElement) {
            const o = getComputedStyle(an);
            if (o.overflowX !== 'visible' || o.overflowY !== 'visible') {
                const ar = an.getBoundingClientRect();
                cx = Math.max(cx, ar.left); cy = Math.max(cy, ar.top); cr = Math.min(cr, ar.right); cb = Math.min(cb, ar.bottom);
            }
        }
        if (cr - cx <= 2 || cb - cy <= 2) continue;
        r = { left: cx, top: cy, width: cr - cx, height: cb - cy };
        items.push({ tag: el.tagName.toLowerCase(), cls: (el.className && typeof el.className === 'string') ? el.className.split(' ').slice(0, 2).join('.') : '', text: (el.textContent || '').trim().slice(0, 24), x: r.left, y: r.top, w: r.width, h: r.height });
        el.__i = items.length - 1;
    }
    const els = Array.from(root.querySelectorAll(sel)).filter(e => e.__i !== undefined);
    const out = [];
    const TOL = 1; // sub-pixel rounding
    for (let a = 0; a < els.length; a++) {
        for (let b = a + 1; b < els.length; b++) {
            const A = els[a], B = els[b];
            if (A.contains(B) || B.contains(A)) continue;
            // A label's own input sits inside the label box by design.
            if (A.matches('label') && B.closest('label') === A) continue;
            const p = items[A.__i], q = items[B.__i];
            const ox = Math.min(p.x + p.w, q.x + q.w) - Math.max(p.x, q.x);
            const oy = Math.min(p.y + p.h, q.y + q.h) - Math.max(p.y, q.y);
            if (ox > TOL && oy > TOL) out.push({ a: p, b: q, ox: Math.round(ox), oy: Math.round(oy) });
        }
    }
    const docW = document.documentElement.scrollWidth, vw = window.innerWidth;
    const past = items.filter(i => i.x + i.w > vw + TOL || i.x < -TOL).map(i => i.tag + '.' + i.cls + ' "' + i.text + '" right=' + Math.round(i.x + i.w));
    for (const e of els) delete e.__i;
    return { overlaps: out, count: items.length, docW, vw, past };
}`;

const browser = await chromium.launch();
const VIEWPORTS = [[1920, 1080], [1440, 900], [1280, 800], [1024, 768], [390, 844]];
const THEMES = ['dark', 'light'];

async function sweep(page, label, root) {
    const r = await page.evaluate(`(${SCAN})(document.querySelector('${root}') || document.body)`);
    const desc = r.overlaps.slice(0, 4).map(o => `[${o.a.tag}.${o.a.cls} "${o.a.text}"] x [${o.b.tag}.${o.b.cls} "${o.b.text}"] ${o.ox}x${o.oy}px`).join('; ');
    check(`${label}: no overlapping elements (${r.count} scanned)`, r.overlaps.length === 0, `${r.overlaps.length} overlaps: ${desc}`);
    check(`${label}: nothing past the viewport edge`, r.past.length === 0 && r.docW <= r.vw + 1, `docW=${r.docW} vw=${r.vw} ${r.past.slice(0, 3).join('; ')}`);
}

for (const [w, h] of VIEWPORTS) for (const theme of THEMES) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, hasTouch: w < 500, isMobile: w < 500 });
    const page = await ctx.newPage();
    // The theme is pinned on every navigation; the mode is set per section
    // below and must survive the reload, so it is NOT set here.
    await page.addInitScript(t => { try { localStorage.setItem('zugzwang-theme', t); } catch {} }, theme);
    await page.goto(BASE, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => { localStorage.setItem('chess-mode', 'game'); localStorage.removeItem('sandbox-session'); });
    const tag = `${w}x${h} ${theme}`;

    // ---- Play, with a game in progress so every panel has content
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    if (w === VIEWPORTS[0][0] && theme === 'dark') {
        await page.evaluate(() => fetch('/api/reset', { method: 'POST' }));
        await page.reload({ waitUntil: 'networkidle' }); await page.waitForTimeout(1200);
        const SQ = s => page.locator(`.chess-container [data-square="${s}"]`);
        await SQ('e2').click(); await page.waitForTimeout(150); await SQ('e4').click();
        await page.waitForSelector('.chess-container .chat-message-coach', { timeout: 40000 });
        await page.locator('.chess-container .chat-input').fill('what is your plan?');
        await page.locator('.chess-container .chat-send-btn').click();
        await page.waitForSelector('.chess-container .chat-message-pending', { state: 'detached', timeout: 90000 });
    }
    await page.reload({ waitUntil: 'networkidle' }); await page.waitForTimeout(1200);
    for (const t of ['Chat', 'Review', 'Progress', 'Board', 'Actions']) {
        await page.locator('.chess-container .rail-icon-btn', { hasText: t }).click();
        await page.waitForTimeout(350);
        await sweep(page, `Play/${t} @${tag}`, '.chess-container');
        if (SHOTS && t === 'Chat') await page.screenshot({ path: `${SHOTS}/play-${w}-${theme}.png` });
    }
    // engine numbers on (the switch is on Actions), so the eval strip is in the sweep
    await page.locator('.chess-container .actions-list .game-switch input').first().check();
    await page.waitForTimeout(300);
    await sweep(page, `Play/eval-on @${tag}`, '.chess-container');
    await page.locator('.chess-container .actions-list .game-switch input').first().uncheck();

    // ---- Learn
    await page.evaluate(() => { localStorage.setItem('chess-mode', 'sandbox'); });
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForSelector('.sandbox [data-square="e2"]', { timeout: 20000 }); await page.waitForTimeout(900);
    if (await page.locator('.sandbox .sandbox-chat-coach').count() === 0) {
        await page.locator('.sandbox .sandbox-tab', { hasText: 'Chat' }).click();
        await page.locator('.sandbox .sandbox-controls button', { hasText: 'AI move' }).click();
        await page.waitForSelector('.sandbox .sandbox-chat-coach', { timeout: 40000 }).catch(() => {});
        await page.locator('.sandbox .sandbox-controls button', { hasText: 'Stop' }).click().catch(() => {});
        await page.waitForTimeout(600);
    }
    for (const t of ['Chat', 'Line', 'Board', 'Actions']) {
        await page.locator('.sandbox .sandbox-tab', { hasText: t }).click();
        await page.waitForTimeout(350);
        await sweep(page, `Learn/${t} @${tag}`, '.sandbox');
        if (SHOTS && t === 'Chat') await page.screenshot({ path: `${SHOTS}/learn-${w}-${theme}.png` });
    }

    // ---- Review
    await page.evaluate(() => { localStorage.setItem('chess-mode', 'postmortem'); });
    await page.reload({ waitUntil: 'networkidle' }); await page.waitForTimeout(1200);
    if (await page.locator('.pm-board-column').count() === 0) {
        await page.setInputFiles('.pm-file-input', { name: 'opera.pgn', mimeType: 'application/x-chess-pgn', buffer: Buffer.from(OPERA) });
        await page.waitForSelector('.pm-board-column', { timeout: 30000 }); await page.waitForTimeout(2500);
    }
    for (let i = 0; i < 6; i++) { await page.keyboard.press('ArrowRight'); await page.waitForTimeout(80); }
    for (const t of ['Chat', 'Moves', 'Report', 'Correct', 'Actions']) {
        await page.locator('.pm .pm-tab', { hasText: t }).click();
        await page.waitForTimeout(400);
        await sweep(page, `Review/${t} @${tag}`, '.pm');
        if (SHOTS && t === 'Chat') await page.screenshot({ path: `${SHOTS}/review-${w}-${theme}.png` });
    }
    await ctx.close();
}

await browser.close();
console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);

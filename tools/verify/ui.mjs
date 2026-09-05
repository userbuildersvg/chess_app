/**
 * Frontend invariants, checked against the running app.
 *
 * Every bug this file checks for was found by driving the real page, and none
 * of them would have been caught by tsc or by the Python suites - CLAUDE.md
 * §10 is emphatic about that and this is the tooling that makes it cheap. The
 * checks are written as invariants rather than screenshots-to-eyeball, so a
 * future session can run one command and get a yes or no.
 *
 * Usage (dev server on :3001, or pass a base URL):
 *
 *     node tools/verify/ui.mjs
 *     node tools/verify/ui.mjs http://localhost:3000
 *     node tools/verify/ui.mjs http://localhost:3001 --shots out/
 *
 * Needs Playwright's chromium. It is NOT a project dependency - it is
 * installed globally on this machine and imported by absolute path below,
 * because adding a browser to requirements/package.json for a check that runs
 * by hand was not worth the install cost on Render. If the path is wrong,
 * `npm i -g playwright && npx playwright install chromium` and update it.
 */

import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';
import { mkdirSync } from 'node:fs';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';
const shotsAt = process.argv.indexOf('--shots');
const SHOTS = shotsAt > -1 ? process.argv[shotsAt + 1] : null;
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

let passed = 0;
let failed = 0;
const check = (label, ok, detail = '') => {
    if (ok) { passed++; console.log(`PASS  ${label}`); }
    else { failed++; console.log(`FAIL  ${label}${detail ? ` - ${detail}` : ''}`); }
};

const browser = await chromium.launch();

/** A page in one mode/theme/viewport, with console errors collected. */
/**
 * A real, short, decisive game for the Review checks. Morphy's Opera Game:
 * 17 moves, a sacrifice, a mate, and an evaluation curve with something in it.
 */
const OPERA_PGN = `[Event "Paris Opera"]
[Site "Paris FRA"]
[Date "1858.11.02"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
`;

async function open({ width = 1440, height = 900, theme = 'dark', mode = 'game', touch = false, empty = false } = {}) {
    const ctx = await browser.newContext({
        viewport: { width, height }, hasTouch: touch, isMobile: touch,
    });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    page.on('pageerror', e => errors.push(`pageerror: ${String(e).slice(0, 200)}`));
    await page.addInitScript(m => {
        try { localStorage.setItem('chess-mode', m); } catch {}
    }, mode);
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.evaluate(t => document.documentElement.setAttribute('data-theme', t), theme);
    // The sandbox opens a server session on mount; give it time to land.
    await page.waitForTimeout(mode === 'sandbox' ? 3500 : 1500);
    // Review starts as an empty canvas, and an empty canvas exercises almost
    // none of the mode - so unless a check is specifically about that screen,
    // import a real game first and measure the workspace people actually use.
    // The PGN is inline rather than a fixture file because the point of this
    // tool is that it runs with one command and no setup.
    if (mode === 'postmortem' && !empty) {
        await page.setInputFiles('.pm-file-input', {
            name: 'opera.pgn', mimeType: 'application/x-chess-pgn', buffer: Buffer.from(OPERA_PGN),
        });
        await page.waitForSelector('.pm-board-column', { timeout: 20000 });
        // The whole-game scan starts on import and repaints the move list as it
        // goes; let the first results land so nothing measured here is caught
        // mid-update.
        await page.waitForTimeout(2500);
    }
    return { ctx, page, errors };
}

const box = (page, sel) => page.evaluate(s => {
    const el = document.querySelector(s);
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height) };
}, sel);

const overflow = page => page.evaluate(() => ({
    x: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    y: document.documentElement.scrollHeight - document.documentElement.clientHeight,
}));

// ---------------------------------------------------------------- 1. basics
console.log('\n=== console, overflow, both themes, both modes ===');
for (const mode of ['game', 'sandbox', 'postmortem']) {
    for (const theme of ['dark', 'light']) {
        const { ctx, page, errors } = await open({ mode, theme });
        const o = await overflow(page);
        check(`${mode}/${theme}: no console errors`, errors.length === 0, [...new Set(errors)][0]);
        check(`${mode}/${theme}: no horizontal overflow`, o.x <= 0, `${o.x}px`);
        check(`${mode}/${theme}: fits the viewport`, o.y <= 1, `${o.y}px over`);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/${mode}-${theme}.png`, fullPage: true });
        await ctx.close();
    }
}

// ------------------------------------------------------- 2. text contrast
// Board coordinates are excluded on purpose. They are ink on a halo, which a
// ratio against the bare square cannot see - see BOARD_NOTATION_STYLE.
console.log('\n=== text contrast (AA), excluding board coordinates ===');
for (const [mode, theme] of [['sandbox', 'dark'], ['sandbox', 'light'], ['postmortem', 'dark'], ['postmortem', 'light']]) {
    const { ctx, page } = await open({ mode, theme });
    const fails = await page.evaluate(() => {
        const lum = c => { const [r, g, b] = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
        const parse = s => (s.match(/[\d.]+/g) || []).slice(0, 4).map(Number);
        const ratio = (f, b) => { const a = lum(f), c = lum(b); return +(((Math.max(a, c) + 0.05) / (Math.min(a, c) + 0.05))).toFixed(2); };
        const bgOf = el => { let n = el; while (n && n !== document.documentElement) { const c = parse(getComputedStyle(n).backgroundColor); if (c.length === 3 || (c[3] ?? 1) > 0.85) return c.slice(0, 3); n = n.parentElement; } return [0, 0, 0]; };
        const out = [];
        document.querySelectorAll('span,p,em,label,button,h2,h3,h4,div').forEach(el => {
            const t = (el.textContent || '').trim();
            if (!t || t.length > 90 || el.children.length) return;
            if (/^[a-h1-8]$/.test(t)) return;
            const cs = getComputedStyle(el), r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4 || cs.visibility === 'hidden' || cs.opacity === '0') return;
            const fg = parse(cs.color);
            if ((fg[3] ?? 1) < 0.9) return;
            const px = parseFloat(cs.fontSize);
            const need = (px >= 24 || (px >= 18.66 && +cs.fontWeight >= 700)) ? 3 : 4.5;
            const cr = ratio(fg.slice(0, 3), bgOf(el));
            if (cr < need) out.push(`${cr}:1 (need ${need}) "${t.slice(0, 40)}"`);
        });
        return out;
    });
    check(`${mode}/${theme}: every text style clears AA`, fails.length === 0, fails.slice(0, 3).join(' | '));
    await ctx.close();
}

// -------------------------------------------------------- 3. touch targets
// Keyed off a COARSE pointer, not a narrow viewport: a touchscreen laptop is a
// coarse pointer at 1440px. The bare checkbox inputs are exempt - their label
// row is the real hit area and is what gets measured as >=44px.
console.log('\n=== touch targets at 390px, coarse pointer ===');
for (const mode of ['game', 'sandbox', 'postmortem']) {
    const { ctx, page } = await open({ mode, width: 390, height: 844, touch: true });
    const small = await page.evaluate(() => {
        const out = [];
        document.querySelectorAll('button,select,a,[role="switch"],[role="tab"]').forEach(el => {
            const b = el.getBoundingClientRect();
            if (b.width < 2 || b.height < 2) return;
            if (b.height < 44) out.push(`${Math.round(b.width)}x${Math.round(b.height)} .${String(el.className).slice(0, 30)}`);
        });
        return [...new Set(out)];
    });
    check(`${mode}: every control is at least 44px tall`, small.length === 0, small.slice(0, 3).join(' | '));
    await ctx.close();
}

// ------------------------------------------------- 4. Learner Mode layout
// Three invariants, each of which was a real bug:
//   - the board and the tab row start on one line (they drifted apart when the
//     heading lived inside the right column);
//   - toggling the eval bar moves nothing (a 19px strip cost 70px of board via
//     a wrap-and-reshrink feedback loop);
//   - the layout is horizontally centred (the panel's grid track was 1fr while
//     the panel itself was capped, leaving 292px dead to the right of it).
console.log('\n=== Learner Mode layout invariants ===');
for (const [width, height] of [[1920, 1080], [1440, 900], [1280, 800]]) {
    const { ctx, page } = await open({ mode: 'sandbox', width, height });

    const board = await box(page, '.sandbox-board-wrapper');
    const tabs = await box(page, '.sandbox-tabs');
    const identity = await box(page, '.sandbox-identity');
    check(`${width}: board and tab row start on the same line`,
        board && tabs && Math.abs(board.y - tabs.y) <= 1, `${board?.y} vs ${tabs?.y}`);
    check(`${width}: the heading sits above the board`,
        identity && board && identity.y + identity.h <= board.y);

    const left = board.x;
    const right = width - (tabs.x + tabs.w);
    check(`${width}: the layout is centred`, Math.abs(left - right) <= 2, `${left} left vs ${right} right`);

    const before = { board: board.w, tabs: tabs.w, canvas: (await box(page, '.sandbox-canvas')).h };
    await page.getByText('Eval bar', { exact: true }).click();
    await page.waitForTimeout(2500);
    const after = {
        board: (await box(page, '.sandbox-board-wrapper')).w,
        tabs: (await box(page, '.sandbox-tabs')).w,
        canvas: (await box(page, '.sandbox-canvas')).h,
    };
    check(`${width}: toggling the eval bar moves nothing`,
        before.board === after.board && before.tabs === after.tabs && before.canvas === after.canvas,
        `${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
    const o = await overflow(page);
    check(`${width}: still fits with the eval bar on`, o.y <= 1, `${o.y}px over`);
    await ctx.close();
}

// ------------------------------------------------------ 5. Review layout
// The same three claims Learner Mode makes, because Review has the same
// two-column shape and would fail them in the same ways - plus two of its own:
// stepping through a game must not resize the layout it is happening inside,
// and the empty canvas has to be a target worth dropping a file on.
console.log('\n=== Post-Mortem layout invariants ===');
for (const [width, height] of [[1920, 1080], [1440, 900], [1280, 800]]) {
    const { ctx, page } = await open({ mode: 'postmortem', width, height });

    const board = await box(page, '.pm-board-wrapper');
    const tabs = await box(page, '.pm-tabs');
    const identity = await box(page, '.pm-identity');
    check(`${width}: board and tab row start on the same line`,
        board && tabs && Math.abs(board.y - tabs.y) <= 1, `${board?.y} vs ${tabs?.y}`);
    check(`${width}: the heading sits above the board`,
        identity && board && identity.y + identity.h <= board.y);
    const left = board.x;
    const right = width - (tabs.x + tabs.w);
    check(`${width}: the layout is centred`, Math.abs(left - right) <= 2, `${left} left vs ${right} right`);

    // The move list grows a highlight and the evidence panel appears under it,
    // and either could grow the column and shrink the board - which is the
    // exact feedback loop the sandbox's eval row caused.
    await page.click('.pm-tab:text-is("Moves")');
    await page.waitForTimeout(400);
    const before = { board: board.w, tabs: tabs.w, canvas: (await box(page, '.pm-canvas')).h };
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(700);
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(700);
    const after = {
        board: (await box(page, '.pm-board-wrapper')).w,
        tabs: (await box(page, '.pm-tabs')).w,
        canvas: (await box(page, '.pm-canvas')).h,
    };
    check(`${width}: stepping through the game moves nothing`,
        before.board === after.board && before.tabs === after.tabs && before.canvas === after.canvas,
        `${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
    const o = await overflow(page);
    check(`${width}: the workspace fits the viewport`, o.y <= 1, `${o.y}px over`);
    await ctx.close();
}

// The empty canvas: the one screen a new visitor to this mode sees.
{
    const { ctx, page } = await open({ mode: 'postmortem', empty: true });
    const drop = await box(page, '.pm-drop');
    check('the empty canvas offers a large drop target',
        drop && drop.h >= 280 && drop.w >= 400, JSON.stringify(drop));
    const tag = await page.evaluate(() => document.querySelector('.pm-drop')?.tagName ?? null);
    check('the drop target is a real button, so it is reachable by keyboard',
        tag === 'BUTTON', String(tag));
    const o = await overflow(page);
    check('the empty canvas does not overflow', o.x <= 0 && o.y <= 1, JSON.stringify(o));
    await ctx.close();
}

await browser.close();
console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);

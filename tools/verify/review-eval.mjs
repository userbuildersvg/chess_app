/**
 * The Review eval bar, against a game with a decisive end.
 *
 * Written because the bar could be read three ways and two of them were
 * wrong (CLAUDE.md §51):
 *
 *   - a position the scan had not evaluated came back {score: null,
 *     mate_in: null} and was drawn at dead level with "0.0" under it, which
 *     is the app asserting a balanced game when it had no reading at all;
 *   - `mate_in: 0` (mate already on the board) carries no sign, and the
 *     unsigned test read every finished mate as a win for Black.
 *
 * Both are invisible to a typecheck and both are one glance away in a demo,
 * so they are checked here against the server's own numbers rather than
 * against a screenshot.
 *
 * Usage: node tools/verify/review-eval.mjs [http://localhost:3001]
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';

/** Morphy's Opera Game: White mates on move 17, so the end is not a draw. */
const PGN = `[Event "Paris Opera"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
`;

/** Fool's Mate: a compact, unambiguous Black-win calibration check. */
const BLACK_PGN = `[Event "Fool's Mate"]
[White "White"]
[Black "Black"]
[Result "0-1"]

1. f3 e5 2. g4 Qh4# 0-1
`;

let passed = 0;
let failed = 0;
const check = (label, ok, detail = '') => {
    if (ok) { passed++; console.log(`PASS  ${label}`); }
    else { failed++; console.log(`FAIL  ${label}${detail ? ` - ${detail}` : ''}`); }
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });

// A stranger gets the homepage; seed the mode, and the bar's own preference.
await page.addInitScript(() => {
    try {
        localStorage.setItem('chess-mode', 'postmortem');
        localStorage.setItem('postmortem-eval-bar', 'on');
    } catch { /* private browsing */ }
});
await page.goto(BASE, { waitUntil: 'networkidle' });
await page.setInputFiles('.pm-file-input', {
    name: 'opera.pgn', mimeType: 'application/x-chess-pgn', buffer: Buffer.from(PGN),
});
await page.waitForSelector('.pm-board-column', { timeout: 20000 });
for (let i = 0; i < 90; i++) {
    const done = await page.evaluate(async () => {
        const id = localStorage.getItem('postmortem-game');
        return (await (await fetch(`/api/postmortem/game/${id}/analysis`)).json()).scan?.status === 'done';
    });
    if (done) break;
    await page.waitForTimeout(1000);
}
await page.waitForTimeout(1200);

/** What the bar is showing, beside what the server said about that position. */
const read = () => page.evaluate(async () => {
    const id = localStorage.getItem('postmortem-game');
    const s = await (await fetch(`/api/postmortem/game/${id}`)).json();
    const bar = document.querySelector('.pm .eval-bar');
    return {
        ply: s.ply,
        turn: s.turn,
        serverEval: s.analysis ? s.analysis.eval_after : null,
        share: bar ? Number(getComputedStyle(bar).getPropertyValue('--eval-share')) : null,
        label: document.querySelector('.pm .eval-bar-label')?.textContent ?? null,
        unknown: bar?.classList.contains('is-unknown') ?? null,
        flipped: bar?.classList.contains('is-flipped') ?? null,
        // The board frame, to prove the toggle moves nothing.
        box: (() => { const b = document.querySelector('.pm .pm-board-wrapper')?.getBoundingClientRect(); return b ? [Math.round(b.x), Math.round(b.y), Math.round(b.width)] : null; })(),
    };
});

const cells = page.locator('.pm-move:not(.pm-move-empty)');
await page.click('#pm-tab-moves');

// ---------------------------------------------------------------- 1. opening
await cells.nth(0).click();
await page.waitForTimeout(600);
const opening = await read();
check('an even opening position sits near the middle',
    opening.share > 0.4 && opening.share < 0.6, JSON.stringify(opening));

// -------------------------------------------------- 2. the side that is ahead
// Move 16 (Qb8+) is mate in one for White: the bar must be at White's end.
await cells.nth(30).click();
await page.waitForTimeout(600);
const winning = await read();
check('a position White is winning shows White ahead',
    winning.share > 0.9, JSON.stringify(winning));

// -------------------------------------------------------- 3. mate on the board
await cells.nth(32).click();
await page.waitForTimeout(600);
const mated = await read();
check('the server reports mate with no sign (mate_in 0)',
    mated.serverEval?.mate_in === 0, JSON.stringify(mated.serverEval));
check('checkmate reads as a win for the side that DELIVERED it',
    mated.share === 1 && mated.turn === 'black', JSON.stringify(mated));
check('...and is labelled as mate rather than as a number',
    mated.label === '#', String(mated.label));

// ------------------------------------------------------------- 4. rotation
const beforeRotate = await read();
await page.locator('.pm-rotate-btn').click();
await page.waitForTimeout(600);
const rotated = await read();
check('turning the board does not change who is winning',
    rotated.share === beforeRotate.share && rotated.flipped === true, JSON.stringify(rotated));
await page.locator('.pm-rotate-btn').click();
await page.waitForTimeout(400);

// ------------------------------------------------- 5. no reading is not "equal"
await page.route('**/api/postmortem/game/**', async route => {
    const response = await route.fetch();
    const body = await response.text();
    try {
        const json = JSON.parse(body);
        if ('analysis' in json) { json.analysis = null; return route.fulfill({ response, json }); }
    } catch { /* not the JSON we meant */ }
    return route.fulfill({ response, body });
});
await cells.nth(20).click();
await page.waitForTimeout(800);
const blank = await read();
check('a position with no evaluation says so instead of showing equal',
    blank.unknown === true && blank.label === '-', JSON.stringify(blank));
check('...and the bar sits level rather than at one end',
    blank.share === 0.5, String(blank.share));
await page.unroute('**/api/postmortem/game/**');

// ------------------------------------------------------------ 6. the toggle
await cells.nth(10).click();
await page.waitForTimeout(600);
const on = await read();
await page.click('#pm-tab-actions');
await page.locator('[data-testid="pm-eval-toggle"]').click();
await page.waitForTimeout(600);
const off = await read();
check('switching the eval bar off does not move or resize the board',
    JSON.stringify(on.box) === JSON.stringify(off.box), `${JSON.stringify(on.box)} -> ${JSON.stringify(off.box)}`);
await page.locator('[data-testid="pm-eval-toggle"]').click();
await page.waitForTimeout(600);
const back = await read();
check('...and neither does switching it back on',
    JSON.stringify(back.box) === JSON.stringify(on.box), JSON.stringify(back.box));

// ----------------------------------------------- 7. decisive Black advantage
await page.evaluate(() => localStorage.removeItem('postmortem-game'));
await page.reload({ waitUntil: 'networkidle' });
await page.setInputFiles('.pm-file-input', {
    name: 'fools-mate.pgn', mimeType: 'application/x-chess-pgn', buffer: Buffer.from(BLACK_PGN),
});
await page.waitForSelector('.pm-board-column', { timeout: 20000 });
for (let i = 0; i < 60; i++) {
    const done = await page.evaluate(async () => {
        const id = localStorage.getItem('postmortem-game');
        return (await (await fetch(`/api/postmortem/game/${id}/analysis`)).json()).scan?.status === 'done';
    });
    if (done) break;
    await page.waitForTimeout(1000);
}
await page.click('#pm-tab-moves');
await page.locator('.pm-move:not(.pm-move-empty)').nth(3).click();
await page.waitForTimeout(600);
const blackWinning = await read();
check('a position Black is winning shows Black ahead',
    blackWinning.share === 0 && blackWinning.turn === 'white', JSON.stringify(blackWinning));
await page.locator('.pm-rotate-btn').click();
await page.waitForTimeout(400);
const blackRotated = await read();
check('rotating a Black-winning position preserves the evaluation',
    blackRotated.share === blackWinning.share, JSON.stringify(blackRotated));

check('no console errors', errors.length === 0, [...new Set(errors)][0] ?? '');

await browser.close();
console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);

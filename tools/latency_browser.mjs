/**
 * The browser's half of the latency profile: what the player actually waits
 * for, measured from the click. Companion to tools/latency_probe.py, which
 * measures the API on its own.
 *
 * Per move: click -> the piece is drawn -> "thinking" is shown -> the AI's
 * move is on the board -> its explanation is in the chat. Then a tab switch
 * while the coach is thinking, a board-size change, and (optionally) the
 * Play -> Review handoff: click -> Review mounted -> first graded ply -> scan
 * done.
 *
 *     node tools/latency_browser.mjs [http://localhost:3001] [--moves 4] [--handoff]
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';
import { Chess } from '/mnt/c/Users/David/Documents/chess-app-v3.9/chess-frontend/node_modules/chess.js/dist/esm/chess.js';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';
const movesAt = process.argv.indexOf('--moves');
const MOVES = movesAt > 0 ? Number(process.argv[movesAt + 1]) : 4;
const HANDOFF = process.argv.includes('--handoff');
const PLAY = '.chess-container', PM = '.pm';

const rows = [];
const note = (flow, stage, ms) => { rows.push({ flow, stage, ms }); console.log(`  ${flow.padEnd(10)} ${stage.padEnd(48)} ${String(Math.round(ms)).padStart(6)} ms`); };
const median = a => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : NaN; };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
const consoleTimings = [];
page.on('console', m => { const t = m.text(); if (t.startsWith('⏱')) consoleTimings.push(t); });
await page.addInitScript(() => { try {
    localStorage.setItem('chess-mode', 'game');
    localStorage.setItem('zugzwang-move-timing', '1');
    localStorage.removeItem('chess-active-section');
    localStorage.removeItem('postmortem-game');
} catch {} });
await page.goto(BASE, { waitUntil: 'networkidle' });
await page.evaluate(async d => {
    await fetch('/api/reset', { method: 'POST' });
    await fetch('/api/difficulty', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile: d }) });
}, HANDOFF ? 'beginner' : 'casual');
await page.reload({ waitUntil: 'networkidle' });
await page.waitForTimeout(1000);

const SQ = s => page.locator(`${PLAY} [data-square="${s}"]`);
const status = () => page.evaluate(async () => (await (await fetch('/api/status')).json()));

/** Time until `fn` is true, polled every few ms on the page's own clock. */
const until = async (fn, arg, timeout = 60000) => {
    const t0 = Date.now();
    await page.waitForFunction(fn, arg, { timeout, polling: 16 });
    return Date.now() - t0;
};

const PLAN = ['e2e4', 'f1c4', 'd1h5', 'b1c3', 'd2d3', 'g1f3', 'c1e3'];
const VALUE = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 };
const choose = fen => {
    const g = new Chess(fen);
    const moves = g.moves({ verbose: true });
    for (const m of moves) { const t = new Chess(fen); t.move(m); if (t.isCheckmate()) return m; }
    for (const uci of PLAN) { const m = moves.find(x => x.from + x.to === uci); if (m) return m; }
    const caps = moves.filter(m => m.captured).sort((a, b) => VALUE[b.captured] - VALUE[a.captured]);
    if (caps.length) return caps[0];
    return moves.find(m => 'nbq'.includes(m.piece)) ?? moves[0];
};

console.log(`\n-- Play through the UI (${MOVES} moves${HANDOFF ? ', then to mate for the handoff' : ''}) --`);
let st = await status();
let plies = 0, mated = false;
const limit = HANDOFF ? 60 : MOVES;
const deadline = Date.now() + 300000;
while (Date.now() < deadline && plies < limit) {
    const fen = st.status.fen;
    const g = new Chess(fen);
    if (g.isGameOver()) { mated = g.isCheckmate(); break; }
    if (g.turn() !== 'w') { await page.waitForTimeout(200); st = await status(); continue; }
    const m = choose(fen);
    const chatBefore = await page.locator(`${PLAY} .chat-message-ai:not(.chat-message-pending)`).count();
    await SQ(m.from).click();
    await page.waitForTimeout(120);
    const t0 = Date.now();
    await SQ(m.to).click();
    if (m.promotion) { await page.locator('.promotion-picker button').first().click().catch(() => {}); }
    const shown = await until(([sq, piece]) => {
        const el = document.querySelector(`.chess-container [data-square="${sq}"] [data-piece]`);
        return !!el && el.getAttribute('data-piece')?.toLowerCase().endsWith(piece);
    }, [m.to, m.piece], 10000).catch(() => NaN);
    note('play', `move ${plies + 1}: piece drawn after click`, shown);
    const thinking = await until(() => !!document.querySelector('.chess-container .ai-thinking, .chess-container .is-thinking, .chess-container .ai-thinking-indicator'), null, 10000).catch(() => NaN);
    note('play', `  thinking state visible`, thinking);
    if (plies === 0) {
        // Tab switch while the coach is thinking: is the UI still responsive?
        const tabs = page.locator(`${PLAY} .rail-icon-btn`);
        const n = await tabs.count();
        if (n > 1) {
            const target = tabs.nth(1);
            const tt = Date.now();
            await target.click();
            await page.waitForFunction(() => true);
            const sel = (await target.getAttribute('class') || '').includes('active');
            note('ui', `  tab switch during thinking (aria-selected=${sel})`, Date.now() - tt);
            await tabs.nth(0).click().catch(() => {});
        }
    }
    const before = (st.history || []).length;
    await until(async n => {
        const s = await (await fetch('/api/status')).json();
        return (s.history || []).length >= n + 2 || s.status.is_game_over;
    }, before, 90000).catch(() => NaN);
    note('play', `  AI reply known to the API (from click)`, Date.now() - t0);
    // The board actually showing it (the polling loop's own render), not the API.
    const after = await status();
    const last = after.history?.[after.history.length - 1];
    if (last?.move && !after.status.is_game_over) {
        const to = last.move.slice(2, 4);
        await until(([sq]) => !!document.querySelector(`.chess-container [data-square="${sq}"] [data-piece]`), [to], 10000).catch(() => {});
    }
    note('play', `  AI reply on the board (from click)`, Date.now() - t0);
    const explanation = await until(n => document.querySelectorAll('.chess-container .chat-message-ai:not(.chat-message-pending)').length > n, chatBefore, 15000).catch(() => NaN);
    note('play', `  explanation in the chat (after AI move)`, explanation);
    plies++;
    await page.waitForTimeout(250);
    st = await status();
}

// Board size change response
{
    const control = page.locator('select[aria-label="Board size"]').first();
    if (await control.count()) {
        const before = await page.locator(`${PLAY} .chess-board-wrapper`).evaluate(el => el.getBoundingClientRect().width);
        const t0 = Date.now();
        await control.selectOption({ index: 1 });
        await page.waitForFunction(w => document.querySelector('.chess-container .chess-board-wrapper').getBoundingClientRect().width !== w, before, { polling: 16, timeout: 5000 }).catch(() => {});
        note('ui', 'board size change reflowed', Date.now() - t0);
        await control.selectOption({ index: 0 }).catch(() => {});
    } else {
        console.log('  (no board-size range control found; skipped)');
    }
}

if (HANDOFF && mated) {
    console.log('\n-- Play -> Review handoff --');
    await page.waitForSelector(`${PLAY} .board-endstate`, { timeout: 5000 });
    const review = page.locator(`${PLAY} .board-endstate .board-endstate-secondary`);
    const t0 = Date.now();
    await review.click();
    const busy = await until(() => {
        const b = document.querySelector('.chess-container .board-endstate-secondary');
        return !b || /Opening/.test(b.textContent || '') || !!document.querySelector('.pm');
    }, null, 5000).catch(() => NaN);
    note('handoff', 'button acknowledges the click', busy);
    const mounted = await until(() => !!document.querySelector('.pm .pm-board-column'), null, 30000).catch(() => NaN);
    note('handoff', 'Review mounted (from click)', Date.now() - t0);
    // The scan, as the API reports it (the moves panel may not be the visible
    // tab, so the DOM is not the thing to watch here).
    const scanDone = await until(async () => {
        const id = localStorage.getItem('postmortem-game');
        if (!id) return false;
        const a = await (await fetch(`/api/postmortem/game/${id}/analysis`).catch(() => ({ json: async () => ({}) }))).json().catch(() => ({}));
        return a.scan && a.scan.status === 'done';
    }, null, 120000).catch(() => NaN);
    note('handoff', 'whole-game scan done (from click)', Date.now() - t0);
    // ...and the report tab rendering it: the grades on the moves list once shown.
    const reportTab = page.locator(`${PM} .rail-icon-btn`).nth(2);
    if (await reportTab.count()) { await reportTab.click().catch(() => {}); }
    await until(() => !document.querySelector('.pm .pm-scan.is-active'), null, 10000).catch(() => {});
    note('handoff', 'report shows a finished scan (from click)', Date.now() - t0);
} else if (HANDOFF) {
    console.log('  (game did not reach mate; handoff skipped)');
}

console.log('\n== console ⏱ marks from moveTiming.ts ==');
for (const t of consoleTimings) console.log('  ' + t);
console.log('\n== medians ==');
const by = {};
for (const r of rows) { const k = r.flow + '|' + r.stage.replace(/move \d+/, 'move n'); (by[k] ||= []).push(r.ms); }
for (const [k, v] of Object.entries(by)) console.log(`  ${k.padEnd(60)} median ${Math.round(median(v))} ms  n=${v.length}`);
await browser.close();

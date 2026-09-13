/**
 * "Review this game": Play -> Review in one click (CLAUDE.md §32), driven
 * live. Needs the backend on :8081 with BETA_ACCESS_REQUIRED=false and a
 * Gemini key (the coach moves during the real game; as a beginner the
 * shortlist is the engine's three worst moves, which is what makes a quick
 * mate reachable from the human side).
 *
 * Part 1 plays a real game to checkmate through the UI and takes the
 * handoff all the way into the analysed review. Part 2 serves ended
 * positions to check the layer's layout at four viewports without needing
 * a second real mate. Part 3 covers the failure paths.
 *
 *     node tools/verify/review-handoff.mjs [http://localhost:3001] [--shots out/]
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';
import { Chess } from '/mnt/c/Users/David/Documents/chess-app-v3.9/chess-frontend/node_modules/chess.js/dist/esm/chess.js';
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
const shot = async (page, name) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` }); };
const PLAY = '.chess-container', PM = '.pm';
const rect = async loc => loc.evaluate(el => { const r = el.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; });
const overlap = (a, b) => a.l < b.r && b.l < a.r && a.t < b.b && b.t < a.b;
const inView = async (page, loc) => loc.evaluate(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= window.innerWidth && r.bottom <= window.innerHeight;
});

const browser = await chromium.launch();

// ---------------------------------------------------------------- Part 1 ---
{
    console.log('\n--- Part 1: a real game, to mate, into Review (1366x768) ---');
    const errors = [];
    const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
    // Once per tab, not per navigation - the reload below is testing that the
    // review id survives, so the script must not clear it again.
    await page.addInitScript(() => { try {
        if (sessionStorage.getItem('handoff-probe')) return;
        sessionStorage.setItem('handoff-probe', '1');
        localStorage.setItem('chess-mode', 'game');
        localStorage.removeItem('chess-active-section');
        localStorage.removeItem('postmortem-game');
    } catch {} });
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);
    await page.evaluate(async () => {
        await fetch('/api/reset', { method: 'POST' });
        await fetch('/api/difficulty', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile: 'beginner' }) });
    });
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);

    const SQ = s => page.locator(`${PLAY} [data-square="${s}"]`);
    const status = () => page.evaluate(async () => (await (await fetch('/api/status')).json()));
    check('no Review button while the game is live', await page.locator(`${PLAY} .board-endstate`).count() === 0);

    // Play for mate: a mate-in-one if there is one, otherwise the Scholar's
    // pattern, otherwise the biggest capture, otherwise a developing move.
    const PLAN = ['e2e4', 'f1c4', 'd1h5', 'b1c3', 'd2d3', 'g1f3', 'c1e3'];
    const VALUE = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 };
    const choose = fen => {
        const g = new Chess(fen);
        const moves = g.moves({ verbose: true });
        for (const m of moves) { const t = new Chess(fen); t.move(m); if (t.isCheckmate()) return m; }
        for (const uci of PLAN) { const m = moves.find(x => x.from + x.to === uci); if (m) return m; }
        const caps = moves.filter(m => m.captured).sort((a, b) => VALUE[b.captured] - VALUE[a.captured]);
        if (caps.length) return caps[0];
        const checks = moves.filter(m => m.san.includes('+'));
        if (checks.length) return checks[0];
        return moves.find(m => 'nbq'.includes(m.piece)) ?? moves[0];
    };
    let st = await status();
    let plies = 0, mated = false;
    const deadline = Date.now() + 240000;
    while (Date.now() < deadline && plies < 60) {
        const fen = st.status.fen;
        const g = new Chess(fen);
        if (g.isGameOver()) { mated = g.isCheckmate(); break; }
        if (g.turn() !== 'w') { await page.waitForTimeout(500); st = await status(); continue; }
        const m = choose(fen);
        await SQ(m.from).click(); await page.waitForTimeout(150); await SQ(m.to).click();
        if (m.promotion) { await page.locator('.promotion-picker button').first().click().catch(() => {}); }
        plies++;
        // Wait for the coach to answer (or the game to end).
        const before = st.status.move_count;
        await page.waitForFunction(async n => {
            const s = await (await fetch('/api/status')).json();
            return s.status.move_count >= n + 2 || s.status.is_game_over;
        }, before, { timeout: 60000 }).catch(() => {});
        await page.waitForTimeout(300);
        st = await status();
    }
    check(`the game reached checkmate through the UI (${st.status.move_count} plies)`, mated, JSON.stringify(st.status).slice(0, 120));
    if (!mated) { console.log('cannot continue Part 1 without a finished game'); await page.close(); }
    else {
        await page.waitForSelector(`${PLAY} .board-endstate`, { timeout: 5000 });
        await page.waitForTimeout(400);
        const layer = page.locator(`${PLAY} .board-endstate`);
        const reset = layer.locator('.board-endstate-reset');
        const review = layer.locator('.board-endstate-secondary');
        check('checkmate shows New game', await reset.count() === 1);
        check('checkmate shows Review this game', await review.count() === 1 && /Review this game/.test(await review.textContent()));
        check('the hint is there', /Analyze the game you just played/.test(await layer.locator('.board-endstate-hint').textContent() ?? ''));
        check('the two buttons do not overlap', !overlap(await rect(reset), await rect(review)));
        check('both are inside the board frame', await review.evaluate(el => {
            const f = el.closest('.chess-board-wrapper').getBoundingClientRect(); const r = el.getBoundingClientRect();
            return r.left >= f.left && r.right <= f.right && r.top >= f.top && r.bottom <= f.bottom;
        }));
        await shot(page, 'handoff-endstate');

        const whoWon = st.status.turn === 'b' || st.status.turn === 'black' ? 'white' : 'black';
        await review.click();
        // The button says it is working, immediately.
        const busyText = await review.textContent().catch(() => '');
        check('the button shows a busy state immediately', /Opening/.test(busyText) || (await page.locator(`${PM}`).count()) > 0, busyText);
        await page.waitForSelector(`${PM} .pm-board-column`, { timeout: 20000 });
        await page.waitForTimeout(500);
        check('Review mode is active in the header', await page.locator('.app-mode[aria-current="page"]').textContent() === 'Review');
        check('no dropzone is shown', await page.locator(`${PM} .pm-drop`).count() === 0);
        const seats = await page.locator(`${PM} .pm-seat-name`).allTextContents();
        check('the seats are You and Gemini at its level', seats.includes('You') && seats.some(t => /Gemini \(Beginner \(~400\)\)/.test(t)), seats.join('|'));
        const subtitle = await page.locator(`${PM} .pm-subtitle`).textContent();
        check('the subtitle says it came from Play', /Zugzwang Play|against Gemini/.test(subtitle ?? ''), subtitle ?? '');
        const results = await page.locator(`${PM} .pm-seat-result`).allTextContents();
        check('the result is shown per seat', results.length === 2 && results.includes('won') && results.includes('lost'), results.join('|'));
        const youWon = await page.locator(`${PM} .pm-seat`, { hasText: 'You' }).locator('.pm-seat-result').textContent();
        check('and it is right for the human', (whoWon === 'white') === (youWon === 'won'), `${whoWon} won; You: ${youWon}`);
        // Orientation: the human was White, so a1 sits bottom-left.
        const a1 = await rect(page.locator(`${PM} [data-square="a1"]`));
        const h8 = await rect(page.locator(`${PM} [data-square="h8"]`));
        check('the board is drawn from the player\'s side', a1.t > h8.t && a1.l < h8.l);
        // Moves.
        await page.locator(`${PM} .rail-icon-btn, ${PM} [role=tab]`, { hasText: 'Moves' }).first().click().catch(() => {});
        await page.waitForTimeout(300);
        const moveCells = await page.locator(`${PM} .pm-move, ${PM} .pm-moves-row .pm-ply, ${PM} [class*=pm-move-]`).count();
        check('the move list carries the game', moveCells >= st.status.move_count, String(moveCells));
        // Navigation works.
        await page.keyboard.press('ArrowLeft');
        await page.waitForTimeout(300);
        const strip = await page.locator(`${PM} .pm-alert, ${PM} .pm-status`).allTextContents();
        check('stepping back works (the mate mark clears)', !strip.join(' ').toLowerCase().includes('checkmate'), strip.join('|'));
        // The scan finishes and the report is there.
        await page.locator(`${PM} [role=tab]`, { hasText: 'Report' }).first().click().catch(() => {});
        const readScan = () => page.evaluate(async () => {
            const id = localStorage.getItem('postmortem-game');
            return (await (await fetch(`/api/postmortem/game/${id}/analysis`)).json()).scan;
        });
        let scan = await readScan();
        for (let i = 0; i < 120 && scan.status !== 'done'; i++) {
            await page.waitForTimeout(1000);
            scan = await readScan();
        }
        check('the whole-game scan completed automatically', scan.status === 'done', JSON.stringify(scan));
        await shot(page, 'handoff-review');
        // Reload keeps the review.
        await page.reload({ waitUntil: 'networkidle' });
        await page.waitForTimeout(1500);
        check('a reload resumes the review', await page.locator(`${PM} .pm-board-column`).count() === 1);
        // And Play still has the finished game.
        await page.locator('.app-mode', { hasText: 'Play' }).click();
        await page.waitForTimeout(500);
        check('Play still shows the finished game', await page.locator(`${PLAY} .board-endstate`).count() === 1);
        check('no page errors', errors.length === 0, errors.join(' | '));
        await page.evaluate(() => fetch('/api/reset', { method: 'POST' }));
        await page.close();
    }
}

// ---------------------------------------------------------------- Part 2 ---
// Endings served, the way interaction.mjs does it: Play cannot be steered
// onto a stalemate, and the layout is what is under test here.
const STALEMATE_FEN = 'k7/8/1Q6/8/8/8/8/4K3 b - - 0 1'; // Black to move, king a8, queen b6: stalemate
const MATE_FEN2 = 'R3k3/8/4K3/8/8/8/8/8 b - - 0 1';       // Black to move, mated by Ra8
const DRAW_FEN = '4k3/8/8/8/8/8/8/4K3 w - - 0 1';          // bare kings: draw
for (const vp of [{ width: 1366, height: 768 }, { width: 1280, height: 720 }, { width: 1920, height: 1080 }, { width: 420, height: 860 }]) {
    console.log(`\n--- Part 2: served endings @ ${vp.width}x${vp.height} ---`);
    const page = await browser.newPage({ viewport: vp });
    let FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
    await page.route('**/api/status', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        success: true, status: { fen: FEN, move_count: 30 }, eval: { score: 0, mate_in: null },
        history: [], player_color: 'white', game_mode: 'human_vs_ai', chat_history: [] }) }));
    await page.addInitScript(() => { try { localStorage.setItem('chess-mode', 'game'); localStorage.removeItem('chess-active-section'); } catch {} });
    await page.goto(BASE, { waitUntil: 'networkidle' });
    for (const [name, fen] of [['checkmate', MATE_FEN2], ['stalemate', STALEMATE_FEN], ['draw', DRAW_FEN]]) {
        FEN = fen;
        await page.reload({ waitUntil: 'networkidle' });
        await page.waitForSelector(`${PLAY} .board-endstate`, { timeout: 8000 }).catch(() => {});
        await page.waitForTimeout(300);
        const layer = page.locator(`${PLAY} .board-endstate`);
        const cls = await layer.getAttribute('class').catch(() => '');
        check(`${name}: the layer is raised`, (cls ?? '').includes(`is-${name}`), cls ?? 'no layer');
        const reset = layer.locator('.board-endstate-reset'), review = layer.locator('.board-endstate-secondary');
        check(`${name}: Review this game is shown`, await review.count() === 1);
        if (await review.count() === 1) {
            check(`${name}: buttons do not overlap`, !overlap(await rect(reset), await rect(review)));
            check(`${name}: the button is whole and on screen`, await inView(page, review));
            check(`${name}: the card stays inside the board`, await layer.locator('.board-endstate-card').evaluate(el => {
                const f = el.closest('.chess-board-wrapper').getBoundingClientRect(); const r = el.getBoundingClientRect();
                return r.top >= f.top - 1 && r.bottom <= f.bottom + 1 && r.left >= f.left - 1 && r.right <= f.right + 1;
            }));
        }
        await shot(page, `handoff-${name}-${vp.width}`);
    }
    await page.close();
}

// ---------------------------------------------------------------- Part 3 ---
{
    console.log('\n--- Part 3: failure paths ---');
    const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    let FEN = MATE_FEN2;
    await page.route('**/api/status', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        success: true, status: { fen: FEN, move_count: 30 }, eval: { score: 0, mate_in: null },
        history: [], player_color: 'white', game_mode: 'human_vs_ai', chat_history: [] }) }));
    await page.route('**/api/postmortem/from-play', route => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'That game could not be replayed: simulated' }) }));
    await page.addInitScript(() => { try {
        if (sessionStorage.getItem('handoff-probe')) return;
        sessionStorage.setItem('handoff-probe', '1');
        localStorage.setItem('chess-mode', 'game'); localStorage.removeItem('postmortem-game');
    } catch {} });
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForSelector(`${PLAY} .board-endstate`, { timeout: 8000 });
    const review = page.locator(`${PLAY} .board-endstate-secondary`);
    await review.click();
    await page.waitForSelector(`${PLAY} .board-endstate-error`, { timeout: 5000 }).catch(() => {});
    const err = await page.locator(`${PLAY} .board-endstate-error`).textContent().catch(() => '');
    check('a failed handoff shows its error under the button', /could not be replayed/.test(err ?? ''), err ?? '');
    check('the button is usable again', !(await review.isDisabled()));
    check('Play is still the mode', await page.locator('.app-mode[aria-current="page"]').textContent() === 'Play');
    check('New game is still there', await page.locator(`${PLAY} .board-endstate-reset`).count() === 1);
    await shot(page, 'handoff-error');

    // A stale review id in storage lands on the dropzone, not on an error.
    await page.unroute('**/api/postmortem/from-play');
    await page.evaluate(() => { localStorage.setItem('postmortem-game', 'pm_does_not_exist'); localStorage.setItem('chess-mode', 'postmortem'); });
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    check('a stale review id falls back to the dropzone', await page.locator(`${PM} .pm-drop`).count() === 1);
    check('...and the stale id is cleared', (await page.evaluate(() => localStorage.getItem('postmortem-game'))) === '');
    await page.close();
}

await browser.close();
console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);

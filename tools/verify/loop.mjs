/**
 * The whole learning loop, from a game played here (CLAUDE.md §33):
 *
 *   Play (a deliberate blunder) -> Review this game -> the blunder is found
 *   -> Correct: state intent -> a correction card -> a fresh practice
 *   position -> hint -> an attempt.
 *
 * Needs the backend on :8081 with BETA_ACCESS_REQUIRED=false and a Gemini
 * key. The human plays 1.f3 2.g4 against the master profile: ...Qh4# is the engine's
 * top move, so the game ends in four plies with one certain human blunder
 * (g4) to find.
 *
 *     node tools/verify/loop.mjs [http://localhost:3001] [--shots out/]
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
const inView = async loc => loc.evaluate(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= window.innerWidth && r.bottom <= window.innerHeight;
});

const browser = await chromium.launch();
for (const vp of [{ width: 1366, height: 768 }, { width: 1280, height: 720 }]) {
    console.log(`\n--- the loop @ ${vp.width}x${vp.height} ---`);
    const errors = [];
    const page = await browser.newPage({ viewport: vp });
    page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
    await page.addInitScript(() => { try {
        if (sessionStorage.getItem('loop-probe')) return;
        sessionStorage.setItem('loop-probe', '1');
        localStorage.setItem('chess-mode', 'game');
        localStorage.removeItem('chess-active-section');
        localStorage.removeItem('postmortem-game');
        localStorage.setItem('postmortem-panel', 'chat');
    } catch {} });
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(800);
    await page.evaluate(async () => {
        await fetch('/api/reset', { method: 'POST' });
        await fetch('/api/difficulty', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile: 'master' }) });
        await fetch('/api/move-quality', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: true }) });
    });
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);

    const SQ = s => page.locator(`${PLAY} [data-square="${s}"]`);
    const status = () => page.evaluate(async () => (await (await fetch('/api/status')).json()));
    const humanMove = async (from, to) => {
        const before = (await status()).status.move_count;
        await SQ(from).click(); await page.waitForTimeout(150); await SQ(to).click();
        let s = await status();
        for (let i = 0; i < 120 && !(s.status.move_count >= before + 2 || s.status.is_game_over); i++) {
            await page.waitForTimeout(500);
            s = await status();
        }
        await page.waitForTimeout(400);
        return status();
    };

    // 1. The deliberate blunder - and then a quick loss.
    //
    // Whatever the coach replies, the human plays the move that leaves the
    // most valuable piece en prise for nothing (chess.js can count attackers),
    // which hangs the queen within a few moves; the first such move is the
    // blunder the review has to find. After that the same policy loses the
    // game fast, because a master-profile coach takes everything offered.
    const VALUE = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 };
    const freeGain = (fen) => {
        // The most Black can win for nothing in the position `fen` (Black to move).
        const c = new Chess(fen);
        let best = 0;
        for (const m of c.moves({ verbose: true })) {
            if (!m.captured) continue;
            const defended = c.attackers(m.to, 'w').length > 0;
            const gain = defended ? VALUE[m.captured] - VALUE[m.piece] : VALUE[m.captured];
            if (gain > best) best = gain;
        }
        return best;
    };
    const worstMove = (fen) => {
        const c = new Chess(fen);
        let pick = null, pickGain = -1;
        for (const m of c.moves({ verbose: true })) {
            const t = new Chess(fen); t.move(m);
            if (t.isGameOver()) continue;
            const gain = freeGain(t.fen());
            if (gain > pickGain) { pick = m; pickGain = gain; }
        }
        return { move: pick, gain: pickGain };
    };
    let st = await status();
    // The biggest hang of the game is the blunder under test: a queen or
    // rook must grade as a blunder; a minor piece as a blunder or a mistake.
    let blunderPly = null, blunderSan = null, blunderGain = -1;
    for (let i = 0; i < 40; i++) {
        const c = new Chess(st.status.fen);
        if (c.isGameOver()) break;
        if (c.turn() !== 'w') { await page.waitForTimeout(500); st = await status(); continue; }
        const { move, gain } = worstMove(st.status.fen);
        if (!move) break;
        const ply = st.status.move_count; // 0-based index of the move about to be played
        if (gain > blunderGain) { blunderPly = ply; blunderSan = move.san; blunderGain = gain; }
        st = await humanMove(move.from, move.to);
    }
    const g = new Chess(st.status.fen);
    check(`the game was lost (${st.status.move_count} plies)`, g.isCheckmate(), JSON.stringify(st.status).slice(0, 160));
    check(`a piece was hung on purpose (${blunderSan} at ply ${blunderPly}, worth ${blunderGain})`, blunderPly !== null && blunderGain >= 3);
    const expected = blunderGain >= 5 ? ['blunder'] : ['blunder', 'mistake'];
    // Wait for Play's own grade to land on that move.
    for (let i = 0; i < 40; i++) {
        st = await status();
        if (st.history?.[blunderPly]?.quality) break;
        await page.waitForTimeout(500);
    }
    const playGrade = st.history?.[blunderPly]?.quality;
    check(`Play graded ${blunderSan} as ${expected.join('/')} (engine-grounded)`, expected.includes(playGrade?.label), JSON.stringify(playGrade));
    check('Play\'s grade names the engine as its source and its depth', playGrade?.grade_source === 'stockfish' && typeof playGrade?.depth === 'number', JSON.stringify(playGrade));

    // 2. Hand it to Review.
    await page.waitForSelector(`${PLAY} .board-endstate-secondary`, { timeout: 5000 });
    await page.locator(`${PLAY} .board-endstate-secondary`).click();
    await page.waitForSelector(`${PM} .pm-board-column`, { timeout: 20000 });
    await page.waitForTimeout(500);
    const readScan = () => page.evaluate(async () => {
        const id = localStorage.getItem('postmortem-game');
        return (await (await fetch(`/api/postmortem/game/${id}/analysis`)).json());
    });
    let analysis = await readScan();
    for (let i = 0; i < 90 && analysis.scan.status !== 'done'; i++) { await page.waitForTimeout(1000); analysis = await readScan(); }
    check('the review scan completed', analysis.scan.status === 'done', JSON.stringify(analysis.scan));
    const row = analysis.moves.find(m => m.ply === blunderPly + 1);
    const q = row?.quality ?? row;
    check(`Review finds ${blunderSan} and grades it ${expected.join('/')}`, expected.includes(q?.label), JSON.stringify(row).slice(0, 240));
    check('Review\'s grade carries the depth that produced it', typeof q?.depth === 'number' && q.depth >= 10, JSON.stringify(q).slice(0, 200));
    check('Review names the move it was preferring', typeof q?.best_move === 'string' || typeof q?.best_san === 'string', JSON.stringify(q).slice(0, 200));
    await page.locator(`${PM} [role=tab]`, { hasText: 'Report' }).click();
    await page.waitForTimeout(600);
    const turning = await page.locator(`${PM} .pm-turning-item`).allTextContents();
    const sanRe = new RegExp(blunderSan.replace(/[+#]/g, '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    check(`"Worth a second look" lists ${blunderSan}`, turning.some(t => sanRe.test(t)), turning.join(' | '));
    await shot(page, `loop-report-${vp.width}`);

    // 3. Correct: pick the blunder, state intent, get a card.
    await page.locator(`${PM} .pm-turning-item`, { hasText: sanRe }).first().click();
    await page.waitForTimeout(500);
    await page.locator(`${PM} [role=tab]`, { hasText: 'Correct' }).click();
    await page.waitForTimeout(600);
    const corr = page.locator(`${PM} .corr-panel`);
    check('Correct opens on the picked move, not an empty state', await corr.count() === 1 && sanRe.test(await corr.locator('.corr-move').textContent() ?? ''), (await page.locator(PM).textContent())?.slice(0, 300));
    check('the intent question is asked', await corr.locator('.corr-question').first().textContent().then(t => /trying to accomplish/.test(t ?? '')));
    check('intent presets are offered', await corr.locator('.corr-chip').count() >= 2);
    check('nothing says the game is unsupported', !/unsupported|not supported|import a game/i.test(await corr.textContent() ?? ''));
    await corr.locator('.corr-chip').first().click();
    await corr.locator('.corr-textarea').fill('I wanted to grab space on the kingside');
    const submit = corr.locator('.corr-primary', { hasText: /Show me what I missed/ });
    check('the submit button is enabled', !(await submit.isDisabled()));
    await submit.click();
    await page.waitForSelector(`${PM} .corr-card`, { timeout: 90000 });
    await page.waitForTimeout(400);
    const card = corr.locator('.corr-card');
    check('a correction card is generated', await card.count() === 1);
    check('it names a theme', (await card.locator('.corr-theme').textContent() ?? '').trim().length > 2);
    check('it says what was missed and a rule for next time',
        (await card.locator('.corr-missed').count()) === 1 && (await card.locator('.corr-rule').count()) === 1);
    check('the engine caveat carries a depth', /depth \d+/.test(await card.locator('.corr-engine-caveat').textContent() ?? ''));
    // The card is taller than the panel at laptop heights and the panel
    // scrolls; what must hold is that it is not clipped sideways and that
    // the panel can actually scroll to the rest of it.
    check('the card is not clipped sideways', await card.evaluate(el => {
        const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth && r.width > 200;
    }));
    const scrollInfo = await card.evaluate(el => {
        const chain = [];
        let p = el.parentElement;
        while (p && p !== document.documentElement) {
            const cs = getComputedStyle(p);
            chain.push(`${p.className.toString().split(' ')[0]}:${cs.overflowY}:${p.scrollHeight}/${p.clientHeight}`);
            if (/(auto|scroll)/.test(cs.overflowY) && p.scrollHeight > p.clientHeight + 1) return { ok: true, chain };
            p = p.parentElement;
        }
        return { ok: el.getBoundingClientRect().bottom <= window.innerHeight || document.documentElement.scrollHeight > window.innerHeight, chain };
    });
    check('the panel around the card scrolls rather than hiding it', scrollInfo.ok, scrollInfo.chain.join(' > '));
    await shot(page, `loop-card-${vp.width}`);

    // 4. Practice: a fresh position, a hint, an attempt.
    await corr.locator('.corr-primary', { hasText: /fresh position/ }).click();
    await page.waitForSelector(`${PM} .corr-retest-board, ${PM} .corr-step:has-text("No practice position")`, { timeout: 60000 });
    await page.waitForTimeout(500);
    const hasBoard = await page.locator(`${PM} .corr-retest-board`).count() === 1;
    const noPractice = await page.locator(`${PM} .corr-question`, { hasText: 'No practice position' }).count() === 1;
    check('practice offers a fresh position (or says honestly why not)', hasBoard || noPractice);
    if (hasBoard) {
        check('it is labelled a different position, same idea', await page.locator(`${PM} .corr-question`, { hasText: 'different position' }).count() === 1);
        check('the practice board is fully on screen', await inView(page.locator(`${PM} .corr-retest-board`)));
        const fen = await page.evaluate(async () => {
            // The board is rendered from the position's FEN; read it back from the prompt's data if exposed, else from react-chessboard's squares.
            const squares = Array.from(document.querySelectorAll('.pm .corr-retest-board [data-square]'));
            return squares.length;
        });
        check('the practice board has 64 squares', fen === 64, String(fen));
        await page.locator(`${PM} .corr-link`, { hasText: 'Give me a hint' }).click();
        await page.waitForSelector(`${PM} .corr-note`, { timeout: 30000 });
        const hint = await page.locator(`${PM} .corr-note`).last().textContent();
        check('a hint arrives', /looking for|move|piece/i.test(hint ?? ''), hint ?? '');
        // Attempt: drag any legal piece. Use the hint's piece type if it says.
        const dragged = await page.evaluate(() => {
            const wrap = document.querySelector('.pm .corr-retest-board');
            return !!wrap;
        });
        // Play through the board with a click-move on any piece with targets:
        // RetestBoard supports click-to-select then click a target.
        const moved = await (async () => {
            const sqs = await page.locator(`${PM} .corr-retest-board [data-square]`).all();
            for (const sq of sqs) {
                const name = await sq.getAttribute('data-square');
                if (!(await sq.locator('[data-piece]').count())) continue;
                await sq.click();
                await page.waitForTimeout(150);
                // react-chessboard puts customSquareStyles on the square's inner div.
                const targets = await page.locator(`${PM} .corr-retest-board [data-square]`).evaluateAll(els => els.filter(e => /sq-(legal|capture)/.test(e.firstElementChild?.getAttribute('style') ?? '')).map(e => e.getAttribute('data-square')));
                if (targets.length) { await page.locator(`${PM} .corr-retest-board [data-square="${targets[0]}"]`).click(); return `${name}${targets[0]}`; }
            }
            return null;
        })();
        check('an attempt could be played on the practice board', !!moved && dragged, String(moved));
        await page.waitForSelector(`${PM} .corr-note[role=status]`, { timeout: 60000 }).catch(() => {});
        const verdict = await page.locator(`${PM} .corr-note[role=status]`).textContent().catch(() => '');
        check('the attempt is judged', /Correction complete|The move was/.test(verdict ?? ''), verdict ?? '');
        check('the practice tally is shown', /Practice on this correction/.test(await page.locator(`${PM} .corr-panel`).textContent() ?? ''));
        await shot(page, `loop-practice-${vp.width}`);
    }
    check('no page errors', errors.length === 0, errors.join(' | '));
    await page.evaluate(() => fetch('/api/reset', { method: 'POST' }));
    await page.close();
}
await browser.close();
console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);

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
    // The backend integration suite proves signed-in responses are actually
    // persisted. On the second viewport, flip only that proven response flag
    // to exercise the browser's account-saved copy without creating a test
    // account in the dev build's real database.
    if (vp.width === 1280) {
        await page.route('**/api/learning-loop/diagnose', async route => {
            const response = await route.fetch();
            const body = await response.json();
            if (body?.correction) body.correction.saved_to_account = true;
            await route.fulfill({ response, json: body });
        });
    }
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
    // On the second viewport, pick a tab by hand while the scan runs: that
    // choice must survive completion. On the first, touch nothing: the
    // finished scan must land on Report by itself.
    const manualTab = vp.width === 1280;
    if (manualTab) {
        // A short game scans in a couple of seconds; click at once, whether
        // the scan is still running or has just finished.
        await page.locator(`${PM} [role=tab]`, { hasText: 'Moves' }).click();
    }
    for (let i = 0; i < 90 && analysis.scan.status !== 'done'; i++) { await page.waitForTimeout(1000); analysis = await readScan(); }
    check('the review scan completed', analysis.scan.status === 'done', JSON.stringify(analysis.scan));
    await page.waitForTimeout(2500);
    const selected = await page.locator(`${PM} [role=tab][aria-selected="true"]`).innerText();
    check(manualTab ? 'a tab picked during the scan is kept when it finishes' : 'a finished scan lands on Report by itself',
          manualTab ? selected === 'Moves' : selected === 'Report', selected);
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
    // The key-decision card sits above everything else in the Report.
    const key = page.locator(`${PM} [data-testid="pm-key"]`);
    check('"Your biggest learning opportunity" card is shown', await key.count() === 1);
    const keyText = await key.innerText().catch(() => '');
    check('...it names the move, whose it was, the grade and the loss in pawns',
        /Move \d+\.{1,3} \S+/.test(keyText) && /(You were (White|Black)|opponent)/.test(keyText) && /graded/.test(keyText) && /(lost about \d+\.\d pawns|lost one)/.test(keyText), keyText.slice(0, 200));
    // The card picks the player's OWN worst judged decision by centipawn
    // loss - which is the blunder we played unless a later own move lost
    // more. Either way it must be one of White's moves.
    const keyMove = /Move (\d+)(\.{1,3}) (\S+)/.exec(keyText);
    check('...it prefers the player\'s own move', keyMove !== null && keyMove[2] === '.' && !/opponent/.test(keyText), keyText.slice(0, 120));
    const keySan = keyMove ? keyMove[3] : blunderSan;
    const keyRe = new RegExp(keySan.replace(/[+#]/g, '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    check('...and shows no FEN or PGN', !/KQkq|\[Event/.test(keyText));
    await key.locator('.pm-link').click();
    check('engine details disclose grade / cpl / preferred / depth', await page.locator(`${PM} [data-testid="pm-key-details"]`).count() === 1);
    const facts = await page.locator(`${PM} [data-testid="pm-facts"]`).innerText().catch(() => '');
    check('coverage, judged decisions and the engine caveat are three labelled facts',
        /COVERAGE|Coverage/.test(facts) && /judged/.test(facts) && /deeper analysis/.test(facts), facts.slice(0, 200));
    await shot(page, `loop-report-${vp.width}`);

    // 3. Correct: the card's primary action opens Correct on that move.
    await key.locator('[data-testid="pm-key-cta"]').click();
    await page.waitForTimeout(600);
    check('the primary CTA switched to the Correct tab', await page.locator(`${PM} [role=tab][aria-selected="true"]`, { hasText: 'Correct' }).count() === 1);
    const corr = page.locator(`${PM} .corr-panel`);
    check('Correct opens on the card\'s move, not an empty state', await corr.count() === 1 && keyRe.test(await corr.locator('.corr-move').textContent() ?? ''), (await corr.locator('.corr-move').textContent().catch(() => ''))?.slice(0, 100));
    check('the intent question is asked', await corr.locator('.corr-question').first().textContent().then(t => /trying to do/.test(t ?? '')));
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
    check('the card has intent / what mattered / coach / engine / rule zones',
        (await Promise.all(['intent', 'mattered', 'coach', 'engine', 'rule'].map(z => card.locator(`[data-testid="corr-zone-${z}"]`).count()))).every(n => n === 1));
    check('engine evidence carries a depth', /depth \d+/.test(await card.locator('[data-testid="corr-zone-engine"]').textContent() ?? ''));
    check('the saved-state chip matches the response', (await card.locator('[data-testid="corr-saved-chip"]').innerText()) === (vp.width === 1280 ? 'Saved — Zugzwang will remember this.' : 'Kept for this session'));
    // The card either offers Practise this idea, or the step under it says
    // honestly why there is nothing to practise - exactly one of the two.
    check('the card says whether practice exists', (await card.locator('[data-testid="corr-practice-available"]').count()) + (await corr.locator('[data-testid="corr-practice-unavailable"]').count()) === 1);
    check('the card shows no raw FEN or PGN', !/KQkq|\[Event/.test(await card.innerText()));
    // A saved lesson carries no storage fine print - the chip says it all;
    // a guest's says how to keep it.
    const storageCopy = (await card.locator('[data-testid="correction-storage-copy"]').count()) ? await card.locator('[data-testid="correction-storage-copy"]').textContent() ?? '' : '';
    check(vp.width === 1280 ? 'persisted-account response shows no session fine print'
                           : 'guest correction copy says how to keep the lesson',
        vp.width === 1280 ? storageCopy === ''
                          : /create an account to keep this lesson/i.test(storageCopy), storageCopy);
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

    // 4. Practice: a fresh position, a hint, an attempt. The card says up
    // front whether practice exists; the button only exists when it does.
    const practiceCta = corr.locator('.corr-primary', { hasText: /^Practise this idea$/ });
    const saysUnavailable = await corr.locator('[data-testid="corr-practice-unavailable"]').count() === 1;
    check('the card states practice availability before anything is clicked', (await practiceCta.count() === 1) !== saysUnavailable);
    if (saysUnavailable) {
        check('...and the unavailable copy carries a reason', /one clear fresh decision|position evidence|could not be verified/i.test(await corr.locator('[data-testid="corr-practice-unavailable"]').innerText()));
    }
    const boardSizeBeforePractice = await page.locator(`${PM}`).evaluate(el => getComputedStyle(el).getPropertyValue('--board-size').trim());
    if (await practiceCta.count()) await practiceCta.click();
    // Practice happens on the MAIN board (CorrectionPanel "One board"): the
    // frame takes the is-practice class and the panel becomes a session.
    await page.waitForSelector(`${PM} .pm-board-wrapper.is-practice, ${PM} .corr-step:has-text("Practice unavailable")`, { timeout: 60000 });
    // The panel smooth-scrolls the practice step into view; give it time to settle.
    await page.waitForTimeout(1500);
    const BOARD = `${PM} .pm-board-wrapper.is-practice`;
    const hasBoard = await page.locator(BOARD).count() === 1;
    const noPractice = saysUnavailable || await page.locator(`${PM} .corr-question`, { hasText: 'Practice unavailable' }).count() === 1;
    check('practice offers a fresh position (or says honestly why not)', hasBoard || noPractice);
    if (hasBoard) {
        check('it names whether practice is transferred or from the real game',
            await page.locator(`${PM} .corr-notyours`, { hasText: /different position|real position/ }).count() === 1);
        check('no embedded practice board is rendered', await page.locator(`${PM} .corr-retest-board`).count() === 0);
        check('the panel is a practice session with End practice', await page.locator(`${PM} [data-testid="corr-practice-session"]`).count() === 1
            && await page.locator(`${PM} [data-testid="corr-end-practice"]`).count() === 1);
        check('the strip says Practice mode', /Practice mode/.test(await page.locator(`${PM} .pm-where`).innerText()));
        check('review navigation is frozen', await page.locator(`${PM} .pm-nav-btn`).evaluateAll(els => els.every(e => e.disabled)));
        const beforeSize = await page.locator(`${PM}`).evaluate(el => getComputedStyle(el).getPropertyValue('--board-size').trim());
        check('the board did not change size for practice', beforeSize === boardSizeBeforePractice, `${boardSizeBeforePractice} -> ${beforeSize}`);
        const fen = await page.evaluate(() => document.querySelectorAll('.pm .pm-board-wrapper [data-square]').length);
        check('the practice board has 64 squares', fen === 64, String(fen));
        await page.locator(`${PM} .corr-link`, { hasText: 'Give me a hint' }).click();
        await page.waitForSelector(`${PM} .corr-note`, { timeout: 30000 });
        const hint = await page.locator(`${PM} .corr-note`).last().textContent();
        check('a hint arrives', /looking for|move|piece/i.test(hint ?? ''), hint ?? '');
        // Attempt: drag any legal piece. Use the hint's piece type if it says.
        const dragged = await page.evaluate(() => !!document.querySelector('.pm .pm-board-wrapper.is-practice'));
        // Play through the board with a click-move on any piece with targets:
        // RetestBoard supports click-to-select then click a target.
        const moved = await (async () => {
            const sqs = await page.locator(`${BOARD} [data-square]`).all();
            for (const sq of sqs) {
                const name = await sq.getAttribute('data-square');
                if (!(await sq.locator('[data-piece]').count())) continue;
                await sq.click();
                await page.waitForTimeout(150);
                // react-chessboard puts customSquareStyles on the square's inner div.
                const targets = await page.locator(`${BOARD} [data-square]`).evaluateAll(els => els.filter(e => /sq-(legal|capture)/.test(e.firstElementChild?.getAttribute('style') ?? '')).map(e => e.getAttribute('data-square')));
                if (targets.length) { await page.locator(`${BOARD} [data-square="${targets[0]}"]`).click(); return `${name}${targets[0]}`; }
            }
            return null;
        })();
        check('an attempt could be played on the practice board', !!moved && dragged, String(moved));
        await page.waitForSelector(`${PM} .corr-note[role=status]`, { timeout: 60000 }).catch(() => {});
        const verdict = await page.locator(`${PM} .corr-note[role=status]`).textContent().catch(() => '');
        check('the attempt is judged', /You found the idea|Not quite/.test(verdict ?? ''), verdict ?? '');
        check('the practice tally is shown', /Practice on this lesson/.test(await page.locator(`${PM} .corr-panel`).textContent() ?? ''));
        await shot(page, `loop-practice-${vp.width}`);
        // End practice: the review's own position and navigation come back.
        await page.locator(`${PM} [data-testid="corr-end-practice"]`).click();
        await page.waitForTimeout(600);
        check('End practice hands the board back', await page.locator(BOARD).count() === 0
            && !/Practice mode/.test(await page.locator(`${PM} .pm-where`).innerText()));
        check('...and the saved lesson is still there', await page.locator(`${PM} .corr-card`).count() === 1);
        check('...and navigation works again', await page.locator(`${PM} .pm-nav-btn`).evaluateAll(els => els.some(e => !e.disabled)));
    }
    check('no page errors', errors.length === 0, errors.join(' | '));
    await page.evaluate(() => fetch('/api/reset', { method: 'POST' }));
    await page.close();
}
await browser.close();
console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);

/**
 * Board interaction and game-state invariants, driven against the real app.
 *
 * The companion to tools/verify/ui.mjs, which owns layout. This one owns what
 * the board LETS YOU DO and what it SAYS ABOUT ITSELF:
 *
 *   1. Play    - drag and click, side by side, against the live backend
 *   2. Learn   - the chess matrix (pins, king safety, check evasion, castling,
 *                en passant, promotion) on real positions, because Learn is
 *                the only mode whose backend takes a start_fen
 *   3. Play    - the checked-king mark and the three end-state layers, with
 *                /api/status stubbed, since Play has no way to be put on a
 *                position and a real game cannot be steered into mate
 *   4. Review  - the mark on a replay, the layer on a branch, and never over
 *                the mainline
 *   5. Touch   - the same drag through react-dnd's TouchBackend, which is a
 *                different code path from the desktop HTML5 one
 *
 * Usage (dev server on :3001, or pass a base URL):
 *
 *     node tools/verify/interaction.mjs
 *     node tools/verify/interaction.mjs http://localhost:3000
 *
 * Playwright is installed globally on this machine and imported by absolute
 * path, exactly as ui.mjs does it and for the same reason.
 */
import { chromium, devices } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';

let passed = 0, failed = 0;
const check = (label, ok, detail = '') => {
    if (ok) { passed++; console.log(`PASS  ${label}`); }
    else { failed++; console.log(`FAIL  ${label}${detail ? ` - ${detail}` : ''}`); }
};

const browser = await chromium.launch();

/**
 * customSquareStyles land on the square's INNER div, not on [data-square].
 * Reading the wrong one returns the board colour for every square and makes
 * a working highlight look like a missing feature.
 */
const paintedIn = (page, root) => page.evaluate(sel => {
    const out = {};
    for (const el of document.querySelectorAll(`${sel} [data-square]`)) {
        const s = (el.firstElementChild && el.firstElementChild.getAttribute('style')) || '';
        if (/--sq-/.test(s)) out[el.dataset.square] = s;
    }
    return out;
}, root);
const hintedIn = async (page, root) => Object.entries(await paintedIn(page, root))
    .filter(([, v]) => /--sq-legal|--sq-capture/.test(v)).map(([k]) => k).sort();
const checkSquareIn = async (page, root) => (Object.entries(await paintedIn(page, root))
    .find(([, v]) => /--sq-check-mark/.test(v)) ?? [null])[0];

/** A pointer drag through whichever dnd backend the page picked. */
const dragIn = async (page, root, from, to, { release = true } = {}) => {
    const a = await page.locator(`${root} [data-square="${from}"]`).boundingBox();
    const z = await page.locator(`${root} [data-square="${to}"]`).boundingBox();
    const ac = { x: a.x + a.width / 2, y: a.y + a.height / 2 };
    const zc = { x: z.x + z.width / 2, y: z.y + z.height / 2 };
    await page.mouse.move(ac.x, ac.y);
    await page.mouse.down();
    await page.mouse.move(ac.x + 6, ac.y + 6, { steps: 3 });
    await page.mouse.move(zc.x, zc.y, { steps: 12 });
    if (release) { await page.mouse.up(); await page.waitForTimeout(500); }
};

// ============================================================ 1. Play: drag
console.log('\n=== Play: drag and click, against the live game ===');
{
    const PLAY = '.chess-board-wrapper';
    const errors = [];
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    await page.evaluate(() => fetch('/api/reset', { method: 'POST' }));
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1600);

    const SQ = s => page.locator(`${PLAY} [data-square="${s}"]`);
    const hinted = () => hintedIn(page, PLAY);
    const drag = (f, t, o) => dragIn(page, PLAY, f, t, o);
    const draggable = s => SQ(s).locator('[data-piece]').first()
        .evaluate(el => el.getAttribute('draggable')).catch(() => null);
    const moveCount = () => page.evaluate(async () => {
        const r = await fetch('/api/status'); return (await r.json()).status.move_count;
    });
    const pieceAt = s => SQ(s).locator('[data-piece]').count();

    check('a piece of yours with somewhere to go is draggable', await draggable('e2') === 'true');
    check("the opponent's pieces are not draggable", await draggable('e7') !== 'true');

    // Click-to-move is not replaced by drag; both are live at all times.
    await SQ('g1').click();
    await page.waitForTimeout(300);
    check('click-to-move still shows a knight its two squares',
        JSON.stringify(await hinted()) === JSON.stringify(['f3', 'h3']), JSON.stringify(await hinted()));
    await SQ('g1').click();
    await page.waitForTimeout(250);
    check('clicking the selected piece again clears the hints', (await hinted()).length === 0);

    // The destinations must be visible WHILE the piece is in flight - the
    // whole point is that an illegal square is never offered, not that an
    // illegal move is rejected after the fact.
    await drag('e2', 'e5', { release: false });
    const mid = await hinted();
    check('a drag lights the legal destinations while the piece is in flight',
        JSON.stringify(mid) === JSON.stringify(['e3', 'e4']), JSON.stringify(mid));
    check('an illegal square is never shown as a destination', !mid.includes('e5'));
    await page.mouse.up();
    await page.waitForTimeout(600);
    check('releasing on an illegal square makes no move', await moveCount() === 0);
    check('...and leaves no hints behind', (await hinted()).length === 0);

    await drag('d2', 'd2');
    check('dragging a piece back onto its own square makes no move', await moveCount() === 0);
    check('...and also leaves no hints behind', (await hinted()).length === 0);

    await drag('g1', 'f3');
    await page.waitForTimeout(700);
    check('a legal drag plays the move', await pieceAt('f3') === 1 && await pieceAt('g1') === 0);

    check('no console errors during the Play drag pass', errors.length === 0, errors.join(' | '));
    await page.close();
}

// ================================================= 2. Learn: the chess matrix
console.log('\n=== Learn: legality on real positions ===');
{
    const LEARN = '.sandbox-board-wrapper';
    const errors = [];
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);

    const SQ = s => page.locator(`${LEARN} [data-square="${s}"]`);
    const hinted = () => hintedIn(page, LEARN);
    const drag = (f, t) => dragIn(page, LEARN, f, t).then(() => page.waitForTimeout(400));
    const pieces = () => page.evaluate(sel => {
        const out = {};
        for (const el of document.querySelectorAll(`${sel} [data-square]`)) {
            const piece = el.querySelector('[data-piece]');
            if (piece) out[el.dataset.square] = piece.dataset.piece;
        }
        return out;
    }, LEARN);
    const at = async sq => (await pieces())[sq] ?? null;
    const overlay = () => page.locator(`${LEARN} .board-endstate`);
    const headline = async () => (await overlay().count())
        ? (await overlay().locator('.board-endstate-headline').innerText()).trim() : null;

    /**
     * Boot Learn on a position, with the student holding the board.
     *
     * POST /api/sandbox/session takes a start_fen that python-chess validates,
     * which makes this the only way in the app to put a real board on an
     * arbitrary position - and the legal move list that comes back is the same
     * generator the real game uses.
     */
    const openAt = async fen => {
        const id = await page.evaluate(async startFen => {
            const r = await fetch('/api/sandbox/session', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ start_fen: startFen, narration_enabled: false, title: 'verify' }),
            });
            const d = await r.json();
            return d.session_id ?? d.id ?? null;
        }, fen);
        if (!id) throw new Error('the backend refused this FEN: ' + fen);
        await page.evaluate(sid => {
            localStorage.setItem('sandbox-session', sid);
            localStorage.setItem('chess-mode', 'sandbox');
        }, id);
        await page.reload({ waitUntil: 'networkidle' });
        await page.waitForTimeout(1600);
        const btn = page.locator('.sandbox-takeover-btn');
        if ((await btn.getAttribute('aria-pressed')) !== 'true') {
            await btn.click();
            await page.waitForTimeout(350);
        }
    };

    // A rook pinned by a bishop has NO move along the pinning diagonal, so it
    // must offer nothing and select nothing rather than light an empty set of
    // hints, which reads as a broken board.
    await openAt('4k3/8/8/8/8/2b5/3R4/4K3 w - - 0 1');
    await SQ('d2').click(); await page.waitForTimeout(300);
    check('a rook pinned by a bishop offers no destination at all',
        (await hinted()).length === 0, JSON.stringify(await hinted()));
    await drag('d2', 'd7');
    check('...and dragging it anywhere leaves it where it was', await at('d2') === 'wR');

    await openAt('4r2k/8/8/8/8/8/4R3/4K3 w - - 0 1');
    await SQ('e2').click(); await page.waitForTimeout(300);
    check('a rook pinned along a file is offered that file and nothing else',
        JSON.stringify(await hinted()) === JSON.stringify(['e3', 'e4', 'e5', 'e6', 'e7', 'e8']),
        JSON.stringify(await hinted()));
    await drag('e2', 'a2');
    check('...and dragging it sideways off the pin makes no move', await at('e2') === 'wR');

    // The black rook on f2 covers the f-file and the second rank, so f1, e2
    // and d2 are all unavailable; d1 is safe and f2 is the undefended rook.
    await openAt('4k3/8/8/8/8/8/5r2/4K3 w - - 0 1');
    await SQ('e1').click(); await page.waitForTimeout(300);
    check('a king is offered only squares where it would not be in check',
        JSON.stringify(await hinted()) === JSON.stringify(['d1', 'f2']), JSON.stringify(await hinted()));
    await drag('e1', 'f1');
    check('...and dragging a king onto an attacked square makes no move', await at('e1') === 'wK');

    await openAt('4k3/8/8/8/8/8/4r3/4K3 w - - 0 1');
    check('the checked king gets the red square', await checkSquareIn(page, LEARN) === 'e1');
    await SQ('h1').click(); await page.waitForTimeout(250);
    check('in check, a piece that cannot help is not selectable', (await hinted()).length === 0);
    await SQ('e1').click(); await page.waitForTimeout(300);
    check('in check, the king is offered only the moves that end it',
        JSON.stringify(await hinted()) === JSON.stringify(['d1', 'e2', 'f1']), JSON.stringify(await hinted()));
    await drag('e1', 'e2');
    check('capturing the checking piece clears the mark', await checkSquareIn(page, LEARN) === null);

    await openAt('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1');
    await SQ('e1').click(); await page.waitForTimeout(300);
    check('castling is offered as a king move to g1 and c1',
        (await hinted()).includes('g1') && (await hinted()).includes('c1'), JSON.stringify(await hinted()));
    await drag('e1', 'g1');
    check('dragging the king two squares brings the rook with it',
        await at('g1') === 'wK' && await at('f1') === 'wR' && await at('h1') === null,
        JSON.stringify(await pieces()));

    await openAt('4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1');
    await SQ('e5').click(); await page.waitForTimeout(300);
    check('en passant appears as a destination', (await hinted()).includes('d6'),
        JSON.stringify(await hinted()));
    await drag('e5', 'd6');
    check('...and taking it removes the pawn from d5',
        await at('d6') === 'wP' && await at('d5') === null, JSON.stringify(await pieces()));

    // Promotion. This used to assert the opposite - that a drag auto-queened
    // with no dialog - and that was right while queen was the only option the
    // app had. The beta-hardening sprint made all four available, so what has
    // to hold now is that the question is asked the SAME WAY whichever way the
    // move was made (CLAUDE.md 19: drag and click are one interaction, and
    // trap 13 is what happens when a promotion forgets it), that the answer is
    // honoured, and that declining leaves the position untouched.
    const picker = () => page.locator(`${LEARN} .promotion-picker`);

    await openAt('4k3/P7/8/8/8/8/8/4K3 w - - 0 1');
    await drag('a7', 'a8');
    check('a promotion by DRAG asks which piece', await picker().count() === 1);
    check('...and has not moved the pawn while it asks',
        await at('a7') === 'wP' && await at('a8') === null, JSON.stringify(await pieces()));
    check('...offering all four pieces',
        await picker().locator('button').count() === 4);
    check('...with the queen focused, so Enter still queens in one keystroke',
        await page.evaluate(() => document.activeElement?.getAttribute('aria-label')) === 'Queen');

    // Escape cancels, and the board is exactly where it was.
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    check('Escape cancels the promotion', await picker().count() === 0);
    check('...leaving the pawn on its square and the move unplayed',
        await at('a7') === 'wP' && await at('a8') === null, JSON.stringify(await pieces()));

    // Underpromotion, which this app could not do at all before.
    await drag('a7', 'a8');
    await picker().locator('button[aria-label="Knight"]').click();
    await page.waitForTimeout(700);
    check('choosing the knight promotes to a knight, not a queen',
        await at('a8') === 'wN', JSON.stringify(await pieces()));

    // And the same question from a CLICK, which is the half trap 13 is about.
    await openAt('4k3/P7/8/8/8/8/8/4K3 w - - 0 1');
    await SQ('a7').click(); await page.waitForTimeout(250);
    await SQ('a8').click(); await page.waitForTimeout(400);
    check('a promotion by CLICK asks the same question', await picker().count() === 1);
    await picker().locator('button[aria-label="Rook"]').click();
    await page.waitForTimeout(700);
    check('...and honours the answer', await at('a8') === 'wR', JSON.stringify(await pieces()));

    console.log('\n--- Learn: the endings ---');
    await openAt('R6k/8/6K1/8/8/8/8/8 b - - 0 1');
    check('checkmate raises the end-state layer', await headline() === 'CHECKMATE', await headline());
    check('...in red', (await overlay().getAttribute('class')).includes('is-checkmate'));
    check('...over a board that is still there', await page.locator(`${LEARN} [data-piece]`).count() > 0);
    check('...with the mode\'s own reset under it',
        (await overlay().locator('.board-endstate-reset').innerText()).trim() === 'Reset board');
    check('...and the mated king still marked', await checkSquareIn(page, LEARN) === 'h8');
    check('...and the layer, not the board, is what a click lands on', await page.evaluate(sel => {
        const r = document.querySelector(`${sel} [data-square="h8"]`).getBoundingClientRect();
        return !!document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
            .closest('.board-endstate');
    }, LEARN));
    check('...and no piece is draggable any more', await page.evaluate(sel =>
        [...document.querySelectorAll(`${sel} [data-piece]`)]
            .every(el => el.getAttribute('draggable') !== 'true'), LEARN));

    await openAt('7k/5Q2/6K1/8/8/8/8/8 b - - 0 1');
    check('stalemate raises the layer', await headline() === 'STALEMATE', await headline());
    check('...in grey, not red', (await overlay().getAttribute('class')).includes('is-stalemate'));
    check('...and says it is a draw',
        (await overlay().locator('.board-endstate-detail').innerText()).toLowerCase().includes('draw'));
    check('...with no king marked in check', await checkSquareIn(page, LEARN) === null);

    await openAt('7k/8/6K1/8/8/8/8/8 w - - 0 1');
    check('a draw by insufficient material says DRAW', await headline() === 'DRAW', await headline());
    check('...and names the reason',
        (await overlay().locator('.board-endstate-detail').innerText()).toLowerCase().includes('material'));

    await openAt('k7/p6Q/6K1/8/8/8/8/8 b - - 0 1');
    check('a king with no square but a pawn that can move is no ending at all',
        await overlay().count() === 0);

    await openAt('R6k/8/6K1/8/8/8/8/8 b - - 0 1');
    await overlay().locator('.board-endstate-reset').click();
    await page.waitForTimeout(1500);
    check('the reset on the layer really resets', await overlay().count() === 0);

    check('no console errors during the Learn pass', errors.length === 0, errors.slice(0, 2).join(' | '));
    await page.close();
}

// ============================== 3. Play: the mark, the layers and the lock
console.log('\n=== Play: check, the three endings, and the interaction lock ===');
{
    const PLAY = '.chess-board-wrapper';
    const errors = [];
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));

    // Play has no endpoint that puts the board on a position, and a real game
    // cannot be steered into mate on demand, so the position is served here.
    // Everything downstream is the real component: ChessBoard loads the FEN
    // into chessService on mount and derives its state exactly as it does live.
    let FEN = '';
    const hits = [];
    await page.route('**/api/status', route => {
        hits.push('status');
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
            success: true, status: { fen: FEN, move_count: 24 }, eval: { score: 0, mate_in: null },
            history: [], player_color: 'white', game_mode: 'human_vs_ai' }) });
    });
    await page.route('**/api/reset', route => {
        hits.push('reset');
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true }) });
    });
    await page.addInitScript(() => { try { localStorage.setItem('chess-mode', 'game'); } catch {} });
    // Set before the first load: an empty FEN would reach chessService.load
    // and log an "Invalid FEN" error that has nothing to do with the app.
    FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
    await page.goto(BASE, { waitUntil: 'networkidle' });

    const openAt = async fen => {
        FEN = fen;
        await page.reload({ waitUntil: 'networkidle' });
        await page.waitForTimeout(1300);
    };
    const overlay = () => page.locator(`${PLAY} .board-endstate`);
    const headline = async () => (await overlay().count())
        ? (await overlay().locator('.board-endstate-headline').innerText()).trim() : null;

    await openAt('rnbqkbnr/ppp2ppp/8/1B1pp3/4P3/8/PPPP1PPP/RNBQK1NR b KQkq - 1 3');
    check('a check marks the checked king\'s square', await checkSquareIn(page, PLAY) === 'e8');
    check('...and the strip says Check', (await page.locator('.game-alert').innerText()).includes('Check'));
    check('...without raising an end-state layer', await overlay().count() === 0);
    check('...over a board that is still entirely visible',
        await page.locator(`${PLAY} [data-piece]`).count() > 20);

    await openAt('rnbqkbnr/ppp2ppp/8/3pp3/4P3/8/PPPPBPPP/RNBQK1NR b KQkq - 1 3');
    check('resolving the check clears the mark immediately', await checkSquareIn(page, PLAY) === null);
    check('...and the strip stops saying Check',
        !(await page.locator('.game-alert').innerText()).includes('Check'));

    await openAt('R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1');
    check('checkmate raises the layer', await headline() === 'CHECKMATE', await headline());
    check('...in red', (await overlay().getAttribute('class')).includes('is-checkmate'));
    check('...naming the winner',
        (await overlay().locator('.board-endstate-detail').innerText()).startsWith('White wins'));
    check('...over the final position, still readable',
        await page.locator(`${PLAY} [data-piece]`).count() === 6);
    check('...and the mated king still marked', await checkSquareIn(page, PLAY) === 'g8');
    check('...with the game\'s own reset under it, under its own name',
        (await overlay().locator('.board-endstate-reset').innerText()).trim() === 'New game');

    check('no piece is draggable once the game is over', await page.evaluate(sel =>
        [...document.querySelectorAll(`${sel} [data-piece]`)]
            .every(el => el.getAttribute('draggable') !== 'true'), PLAY));
    check('a click lands on the layer, not on a square', await page.evaluate(sel => {
        const r = document.querySelector(`${sel} [data-square="g8"]`).getBoundingClientRect();
        return !!document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
            .closest('.board-endstate');
    }, PLAY));
    // The lock has to live in the handler, not only in the layer's own
    // pointer capture: a click delivered straight to the square must do
    // nothing either.
    await page.locator(`${PLAY} [data-square="g8"]`).dispatchEvent('click');
    await page.waitForTimeout(250);
    check('a synthetic click straight at the board still selects nothing',
        (await hintedIn(page, PLAY)).length === 0);
    check('the "Make AI Move" control is gone', await page.locator('.ai-move-btn').count() === 0);

    hits.length = 0;
    await overlay().locator('.board-endstate-reset').click();
    await page.waitForTimeout(900);
    check('the layer\'s reset is the game\'s reset', hits.includes('reset'));

    await openAt('7k/5Q2/6K1/8/8/8/8/8 b - - 0 1');
    check('stalemate raises the layer', await headline() === 'STALEMATE', await headline());
    check('...in grey, not red', (await overlay().getAttribute('class')).includes('is-stalemate'));
    check('...and calls it a draw',
        (await overlay().locator('.board-endstate-detail').innerText()).toLowerCase().startsWith('draw'));
    check('...with no king marked in check', await checkSquareIn(page, PLAY) === null);

    await openAt('7k/8/6K1/8/8/8/8/8 w - - 0 1');
    check('an insufficient-material draw says DRAW', await headline() === 'DRAW', await headline());
    check('...in grey', (await overlay().getAttribute('class')).includes('is-draw'));

    // Trap 11: anything in the board column sized against something other than
    // the board ends up over the coaching panel at some viewport. The layer is
    // `inset: 0` inside the frame, so it can only ever be exactly the frame.
    console.log('\n--- the layer belongs to the board, at every width ---');
    await openAt('R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1');
    for (const [w, h] of [[1920, 1080], [1440, 900], [1280, 800], [1024, 768], [820, 1180], [390, 844]]) {
        await page.setViewportSize({ width: w, height: h });
        await page.waitForTimeout(700);
        const m = await page.evaluate(sel => {
            const r = e => { const b = e.getBoundingClientRect();
                return { l: b.left, t: b.top, r: b.right, b: b.bottom, h: b.height }; };
            return {
                wrap: r(document.querySelector(sel)),
                lay: r(document.querySelector('.board-endstate')),
                card: r(document.querySelector('.board-endstate-card')),
                btn: r(document.querySelector('.board-endstate-reset')),
            };
        }, PLAY);
        const inside = (a, o) => a.l >= o.l - 1 && a.t >= o.t - 1 && a.r <= o.r + 1 && a.b <= o.b + 1;
        check(`${w}: the layer is exactly the board frame`,
            inside(m.lay, m.wrap) && Math.abs(m.lay.l - m.wrap.l) < 2 && Math.abs(m.lay.r - m.wrap.r) < 2,
            JSON.stringify(m));
        check(`${w}: the message and its reset stay inside the layer`, inside(m.card, m.lay),
            JSON.stringify(m.card));
        check(`${w}: the reset is still a 44px touch target`, m.btn.h >= 43.5, String(m.btn.h));
        const of = await page.evaluate(() =>
            document.documentElement.scrollWidth - document.documentElement.clientWidth);
        check(`${w}: the layer causes no horizontal overflow`, of <= 1, String(of));
    }

    check('no console errors during the Play end-state pass', errors.length === 0, errors.slice(0, 2).join(' | '));
    await page.close();
}

// ================================================================ 4. Review
console.log('\n=== Review: the mark on a replay, the layer only on a branch ===');
{
    const PM = '.pm-board-wrapper';
    const OPERA = `[Event "Paris Opera"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
`;
    // A game whose final position has a mate in one that was not played, so a
    // branch can actually reach checkmate.
    const MISSED = `[Event "Missed mate"]
[White "A"]
[Black "B"]
[Result "*"]

1. f3 e5 2. g4 d5 *
`;
    const errors = [];
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));
    await page.addInitScript(() => { try { localStorage.setItem('chess-mode', 'postmortem'); } catch {} });

    const overlay = () => page.locator(`${PM} .board-endstate`);
    const importGame = async (pgn, name) => {
        await page.goto(BASE, { waitUntil: 'networkidle' });
        await page.waitForTimeout(1500);
        if (await page.locator('.pm-file-input').count() === 0) {
            await page.locator('.pm-identity button', { hasText: 'Close game' }).click();
            await page.waitForTimeout(900);
        }
        await page.setInputFiles('.pm-file-input', {
            name, mimeType: 'application/x-chess-pgn', buffer: Buffer.from(pgn),
        });
        await page.waitForSelector('.pm-board-column', { timeout: 30000 });
        await page.waitForTimeout(2500);
    };

    // Only ArrowLeft/ArrowRight are bound (there is no End key here), so walk
    // to the end of the game a ply at a time.
    const toEnd = async () => {
        for (let i = 0; i < 45; i++) { await page.keyboard.press('ArrowRight'); await page.waitForTimeout(130); }
        await page.waitForTimeout(900);
    };
    await importGame(OPERA, 'opera.pgn');
    await toEnd();
    check('the final position of a mated game marks the mated king',
        await checkSquareIn(page, PM) === 'e8', await checkSquareIn(page, PM));
    check('...but the mainline is NOT covered by an end-state layer', await overlay().count() === 0);
    check('...and the strip still names the result',
        (await page.locator('.pm-alert').innerText()).toLowerCase().includes('checkmate'));
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(900);
    check('stepping back off the mate clears the mark', await checkSquareIn(page, PM) === null);

    await importGame(MISSED, 'missed.pgn');
    await toEnd();
    await page.keyboard.press('ArrowLeft');   // back to the position where Qh4 is mate
    await page.waitForTimeout(900);
    await page.locator('.pm-explore-btn').click();
    await page.waitForTimeout(400);
    await dragIn(page, PM, 'd8', 'h4');
    await page.waitForTimeout(3500);

    check('a branch of your own that ends in mate DOES raise the layer',
        await overlay().count() === 1);
    if (await overlay().count() === 1) {
        check('...saying CHECKMATE',
            (await overlay().locator('.board-endstate-headline').innerText()).trim() === 'CHECKMATE');
        check('...with the branch\'s own way out under it',
            (await overlay().locator('.board-endstate-reset').innerText()).trim() === 'Back to the game');
        check('...and the layer inside the board frame', await page.evaluate(sel => {
            const w = document.querySelector(sel).getBoundingClientRect();
            const l = document.querySelector('.board-endstate').getBoundingClientRect();
            return l.left >= w.left - 1 && l.right <= w.right + 1
                && l.top >= w.top - 1 && l.bottom <= w.bottom + 1;
        }, PM));
        await overlay().locator('.board-endstate-reset').click();
        await page.waitForTimeout(2000);
        check('...and that reset puts you back on the game', await overlay().count() === 0);
    }
    check('no console errors during the Review pass', errors.length === 0, errors.slice(0, 2).join(' | '));
    await ctx.close();
}

// ================================================================= 5. Touch
console.log('\n=== Touch: the same drag through the other dnd backend ===');
{
    const PLAY = '.chess-board-wrapper';
    // react-chessboard chooses its backend from `'ontouchstart' in window`, so
    // a touch device takes react-dnd's TouchBackend and a desktop takes the
    // HTML5 one. Everything above tested the desktop path.
    const ctx = await browser.newContext({ ...devices['Pixel 5'] });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
    const cdp = await ctx.newCDPSession(page);
    const SQ = s => page.locator(`${PLAY} [data-square="${s}"]`);
    const centre = async s => {
        const r = await SQ(s).boundingBox();
        return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    };
    const touch = (type, points) => cdp.send('Input.dispatchTouchEvent', {
        type, touchPoints: points.map(q => ({ x: q.x, y: q.y, radiusX: 6, radiusY: 6, force: 1 })),
    });
    const swipe = async (from, to) => {
        const a = await centre(from), z = await centre(to);
        await touch('touchStart', [a]);
        await page.waitForTimeout(120);
        await touch('touchMove', [{ x: a.x + 8, y: a.y + 8 }]);
        await page.waitForTimeout(80);
        await touch('touchMove', [{ x: (a.x + z.x) / 2, y: (a.y + z.y) / 2 }]);
        await page.waitForTimeout(80);
        await touch('touchMove', [z]);
        await page.waitForTimeout(250);
    };
    const pieceAt = s => SQ(s).locator('[data-piece]').count();

    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await page.evaluate(() => fetch('/api/reset', { method: 'POST' }));
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(2200);

    await swipe('e2', 'e5');
    check('a touch drag lights the legal destinations',
        JSON.stringify(await hintedIn(page, PLAY)) === JSON.stringify(['e3', 'e4']),
        JSON.stringify(await hintedIn(page, PLAY)));
    await touch('touchEnd', []);
    await page.waitForTimeout(800);
    check('releasing a touch drag on an illegal square makes no move',
        await pieceAt('e2') === 1 && await pieceAt('e5') === 0);
    check('...and leaves no hints behind', (await hintedIn(page, PLAY)).length === 0);

    await swipe('d2', 'd4');
    await touch('touchEnd', []);
    await page.waitForTimeout(1400);
    check('a legal touch drag plays the move',
        await pieceAt('d4') === 1 && await pieceAt('d2') === 0);

    await page.waitForTimeout(2500);
    await SQ('g1').tap();
    await page.waitForTimeout(500);
    check('tapping a piece still shows its destinations, on touch too',
        (await hintedIn(page, PLAY)).length > 0);
    check('no page errors on touch', errors.length === 0, errors.slice(0, 2).join(' | '));
    await ctx.close();
}

// =============================================== 6. The transport's own ends
//
// `Back`/`Previous` and `Forward`/`Next` are the only controls in Learn and
// Review that can be pressed while there is provably nothing to do, and both
// forward buttons once could be: Learn's guarded on nothing at all, so it was
// live at the root of every fresh session, and Review's guarded only the
// mainline, so it was live at the tip of every branch. Neither could corrupt
// anything - the server no-ops - which is exactly why it needed a test rather
// than a bug report: a control that quietly does nothing looks like a control
// that is broken.
console.log('\n=== The transport: a step is offered only where there is one ===');
{
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
    const page = await ctx.newPage();
    await page.addInitScript(() => { try { localStorage.setItem('chess-mode', 'sandbox'); } catch {} });
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(3500);
    const fwd = page.locator('.sandbox button:text-is("Forward")');
    const back = page.locator('.sandbox button:text-is("Back")');
    check('Learn: Forward is off at the root of a fresh session', await fwd.isDisabled());
    check('Learn: Back is off there too', await back.isDisabled());
    await page.locator('.sandbox button:text-is("Take over")').click();
    await page.waitForTimeout(400);
    await page.locator('.sandbox [data-square="e2"]').click();
    await page.waitForTimeout(300);
    await page.locator('.sandbox [data-square="e4"]').click();
    await page.waitForTimeout(3000);
    check('Learn: Back is on after a move', !(await back.isDisabled()));
    check('Learn: Forward is still off at the new tip', await fwd.isDisabled());
    await back.click();
    await page.waitForTimeout(1800);
    check('Learn: Forward is on once there is a move ahead', !(await fwd.isDisabled()));
    await fwd.click();
    await page.waitForTimeout(1800);
    check('Learn: and off again at the tip it just reached', await fwd.isDisabled());
    await ctx.close();
}
{
    const PGN = '[Event "S"]\n[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *\n';
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
    const page = await ctx.newPage();
    await page.addInitScript(() => { try { localStorage.setItem('chess-mode', 'postmortem'); } catch {} });
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await page.setInputFiles('.pm-file-input', {
        name: 's.pgn', mimeType: 'application/x-chess-pgn', buffer: Buffer.from(PGN),
    });
    await page.waitForSelector('.pm-board-column', { timeout: 30000 });
    await page.waitForTimeout(2500);
    const next = page.locator('.pm-controls button:text-is("Next")');
    const prev = page.locator('.pm-controls button:text-is("Previous")');
    check('Review: Previous is off at the start of the game', await prev.isDisabled());
    check('Review: Next is on there', !(await next.isDisabled()));
    for (let i = 0; i < 8; i++) { await page.keyboard.press('ArrowRight'); await page.waitForTimeout(150); }
    await page.waitForTimeout(800);
    check('Review: Next is off at the last ply of the game', await next.isDisabled());
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(800);
    check('Review: Next is on again a ply back', !(await next.isDisabled()));
    // Branch, and stand at the tip of the what-if.
    await page.locator('.pm-explore-btn').click();
    await page.waitForTimeout(400);
    await page.locator('.pm-board-wrapper [data-square="g8"]').click();
    await page.waitForTimeout(300);
    await page.locator('.pm-board-wrapper [data-square="f6"]').click();
    await page.waitForTimeout(6000);
    check('Review: the branch was taken', await page.locator('.pm-where-branch').count() === 1);
    check('Review: Next is off at the tip of a branch', await next.isDisabled());
    await ctx.close();
}

await browser.close();
console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);

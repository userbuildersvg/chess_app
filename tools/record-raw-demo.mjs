/**
 * Ten-odd minutes of raw Zugzwang footage, for an editor to cut down.
 *
 * `record-demo.mjs` films the 60-second magic path. This films everything
 * worth having on the timeline: the homepage, the whole Review loop, Play,
 * Learn, and the improvement profile's honest signed-out state. It is raw
 * material - long holds, deliberate pauses, nothing clever - because an
 * editor can always cut, and cannot un-cut.
 *
 * WHAT MAKES IT USABLE AS FOOTAGE
 *
 *   - **Every segment is isolated.** A failure in Play does not cost the
 *     Review footage already in the can: each segment is caught, logged, and
 *     the camera moves on. One broken step must not lose ten minutes.
 *   - **A visible cursor**, injected by this script (the product knows
 *     nothing about it), because Playwright's video does not draw a mouse and
 *     clicks would otherwise look like the app moving by itself.
 *   - **Timestamps.** Each beat prints its offset into the recording, so the
 *     log doubles as an edit list.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *
 *   - No account. Signup on production needs a beta invitation, and minting
 *     one means production database access - so the profile is filmed in its
 *     signed-out state, which is a real state and an honest one.
 *   - No Gemini spam: about four provider calls across the whole take (one
 *     diagnosis, two AI moves, one coach question). If any of them fall back
 *     to the engine-backed answer, that is the product working and stays in.
 *
 * Usage:
 *
 *     node tools/record-raw-demo.mjs                      # the live site
 *     node tools/record-raw-demo.mjs http://localhost:3001
 *
 * Output: artifacts/zugzwang-raw-demo-web.webm, and .mp4 when ffmpeg is on
 * PATH.
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
// `--only a,b` films just those segments, and `--out <name>` writes them
// somewhere of their own - so a gap can be re-shot without paying for the
// whole take (and its provider calls) again.
const onlyAt = process.argv.indexOf('--only');
const ONLY = onlyAt > -1 ? new Set(process.argv[onlyAt + 1].split(',')) : null;
const outAt = process.argv.indexOf('--out');
const NAME = outAt > -1 ? process.argv[outAt + 1] : 'zugzwang-raw-demo-web';
const OUT = join(ARTIFACTS, `${NAME}.webm`);
const MP4 = join(ARTIFACTS, `${NAME}.mp4`);
const PGN_PATH = join(ARTIFACTS, 'demo-game.pgn');

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
mkdirSync(ARTIFACTS, { recursive: true });
if (!existsSync(PGN_PATH)) writeFileSync(PGN_PATH, PGN);

let t0 = 0;
const marks = [];
/** Print the offset into the recording, so the log is an edit list. */
const mark = label => {
    const s = (Date.now() - t0) / 1000;
    const stamp = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
    marks.push({ stamp, label });
    console.log(`  ${stamp}  ${label}`);
};

process.stdout.write('warming the backend... ');
try {
    const res = await fetch(HEALTH);
    const body = await res.json();
    console.log(`${res.status} (build ${body.version ?? '?'})`);
} catch (err) {
    console.log(`unreachable: ${err.message}`);
}

const browser = await chromium.launch();
const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: 'dark',
    recordVideo: { dir: ARTIFACTS, size: { width: 1440, height: 900 } },
});
await context.addInitScript(() => {
    try { localStorage.setItem('zugzwang-theme', 'dark'); } catch { /* private mode */ }
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
        addEventListener('mousedown', () => { dot.style.background = 'rgba(200,179,138,0.75)'; });
        addEventListener('mouseup', () => { dot.style.background = 'rgba(200,179,138,0.35)'; });
    };
    if (document.body) draw();
    else addEventListener('DOMContentLoaded', draw);
});

const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 160)));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 160)); });

async function show(target, { settle = 900, after = 1600 } = {}) {
    const locator = target.first();
    await locator.waitFor({ state: 'visible', timeout: 40000 });
    await locator.scrollIntoViewIfNeeded();
    await wait(400);
    const box = await locator.boundingBox();
    if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 22 });
    await wait(settle);
    await locator.click();
    await wait(after);
}

/** Scroll the page by a screenful or so, slowly enough to read. */
async function glide(px, steps = 14, pause = 130) {
    for (let i = 0; i < steps; i++) {
        await page.mouse.wheel(0, px / steps);
        await wait(pause);
    }
}

/**
 * Make sure the app is open in `mode` before a segment that assumes it.
 *
 * The full take arrives at each segment through the one before it. A segment
 * filmed on its own (`--only`) starts on a blank page, so it has to let
 * itself in - the mode is the app's own `chess-mode` key, seeded before the
 * first paint exactly as a returning visitor's browser would have it.
 */
async function ensureApp(mode) {
    if (!page.url().startsWith('http')) {
        await context.addInitScript(m => {
            try { localStorage.setItem('chess-mode', m); } catch { /* private mode */ }
        }, mode);
        await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 60000 });
        await wait(3500);
    }
}

const done = [];
const failed = [];
/** One segment. A failure here must not cost the footage already recorded. */
async function segment(name, fn) {
    if (ONLY && !ONLY.has(name)) return;
    mark(`── ${name}`);
    try {
        await fn();
        done.push(name);
    } catch (err) {
        const why = err.message.split('\n')[0].slice(0, 120);
        console.log(`        (cut short: ${why})`);
        failed.push(`${name}: ${why}`);
        await page.screenshot({ path: join(ARTIFACTS, `raw-fail-${name.replace(/\W+/g, '-')}.png`) }).catch(() => {});
    }
}

t0 = Date.now();

// ===================================================== 1. homepage / the pitch
await segment('homepage', async () => {
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.mouse.move(700, 430, { steps: 12 });
    await wait(5000);                       // the headline and the promise
    mark('hero: thesis + CTAs + board');
    await glide(700);
    await wait(3500);                       // "How Zugzwang works"
    mark('how it works: the six steps');
    await glide(800);
    await wait(3500);
    mark('what you get from one game');
    await glide(800);
    await wait(3500);
    mark('saved lessons / my improvement');
    await glide(750);
    await wait(3000);
    mark('guest, account, or Pro');
    await glide(-3000, 10, 90);             // back to the top for the CTA
    await wait(2500);
});

// ============================================== 2. the Review loop (the core)
await segment('review loop', async () => {
    await show(page.getByTestId('home-analyze')
        .or(page.getByRole('button', { name: /Analyze a game right now/i })), { after: 2600 });
    mark('focused Review: guest entry, no account asked for');
    await wait(2000);

    await page.locator('.pm-file-input').setInputFiles(PGN_PATH);
    mark('a game is uploaded');
    await page.waitForSelector('.pm-board-column', { timeout: 60000 });
    await wait(3000);
    mark('the game is on the board, seats and result named');

    process.stdout.write('        scanning');
    for (let i = 0; i < 90; i++) {
        const ok = await page.evaluate(async () => {
            const id = localStorage.getItem('postmortem-game');
            if (!id) return false;
            const r = await fetch(`/api/postmortem/game/${id}/analysis`);
            return r.ok && (await r.json()).scan?.status === 'done';
        }).catch(() => false);
        if (ok) break;
        process.stdout.write('.');
        await wait(1000);
    }
    console.log('');
    await wait(3000);
    mark('every move graded, Report lands by itself');

    // The report, read top to bottom.
    const key = page.getByTestId('pm-key').first();
    await key.waitFor({ state: 'visible', timeout: 40000 });
    await key.scrollIntoViewIfNeeded();
    await wait(5000);
    mark('"Your biggest learning opportunity"');
    await glide(420, 8);
    await wait(4000);
    mark('eval curve, coverage, engine caveat');
    await glide(-420, 8);
    await wait(1500);

    // The move list, so the grades are visible.
    await show(page.getByRole('tab', { name: 'Moves' }).or(page.locator('#pm-tab-moves')), { after: 2200 });
    mark('the scoresheet: every move graded');
    await wait(3500);
    await show(page.getByRole('tab', { name: 'Report' }).or(page.locator('#pm-tab-report')), { after: 1800 });

    await show(page.getByTestId('pm-key-cta')
        .or(page.getByRole('button', { name: /Work through this decision/i })), { after: 2400 });
    mark('Correct: the intent question, asked BEFORE any diagnosis');
    await wait(3000);

    await show(page.getByRole('button', { name: 'Improve a piece', exact: true })
        .or(page.locator('.corr-chip')), { after: 1800 });
    mark('intent chosen');

    await show(page.getByRole('button', { name: /Show me what I missed/i })
        .or(page.locator('.corr-primary', { hasText: /Show me what I missed/ })), { after: 800 });
    await page.waitForSelector('.corr-card', { timeout: 95000 });
    await page.locator('.corr-card').first().scrollIntoViewIfNeeded();
    await wait(6000);
    mark('the saved lesson is written');
    await glide(360, 8);
    await wait(5000);
    mark('the evidence: eval before/after, engine move, centipawn loss');
    await glide(-200, 5);
    await wait(1500);

    const practice = page.getByTestId('corr-practice-available')
        .or(page.getByRole('button', { name: /Practise this idea/i }));
    if (await practice.first().count()) {
        await show(practice, { after: 1500 });
        await page.waitForSelector('.pm-board-wrapper.is-practice', { timeout: 60000 });
        await page.locator('.pm-board-wrapper').first().scrollIntoViewIfNeeded();
        await wait(6000);
        mark('practice, on the MAIN board - navigation frozen');
        await glide(320, 6);
        await wait(3500);
        mark('the practice session panel');
        await show(page.getByTestId('corr-end-practice')
            .or(page.getByRole('button', { name: /End practice/i })), { after: 2500 });
        await page.locator('.pm-board-wrapper').first().scrollIntoViewIfNeeded();
        await wait(4000);
        mark('End practice: the reviewed game is back, untouched');
    }

    // Stepping the game, which is what a player does first.
    await show(page.getByRole('tab', { name: 'Moves' }).or(page.locator('#pm-tab-moves')), { after: 1800 });
    const next = page.locator('.pm-nav-btn', { hasText: 'Next' }).first();
    for (let i = 0; i < 4; i++) {
        if (await next.isEnabled().catch(() => false)) { await next.click(); await wait(1700); }
    }
    mark('stepping through the game, grade by grade');
    await wait(2500);

    // The switches, which is where the honest ones live.
    await show(page.getByRole('tab', { name: 'Actions' }).or(page.locator('#pm-tab-actions')), { after: 2400 });
    mark('Review actions: eval bar, move quality, board size, coach lens');
    await wait(3000);
    const evalToggle = page.getByTestId('pm-eval-toggle');
    if (await evalToggle.count()) {
        await show(evalToggle, { after: 2600 });
        mark('the eval bar, on - and the board does not move');
        await wait(2600);
    }
    const grades = page.getByTestId('pm-grades-toggle');
    if (await grades.count()) {
        await show(grades, { after: 2400 });
        mark('move quality off');
        await show(grades, { after: 2400 });
        mark('and back on');
    }
    await glide(320, 6);
    await wait(3000);
    mark('the coach lens, and saving the game to a profile');
    await glide(-320, 6);
    await wait(1500);

    // "What if I had played something else" - a branch, answered by the coach.
    await show(page.getByRole('tab', { name: 'Moves' }).or(page.locator('#pm-tab-moves')), { after: 1600 });
    const explore = page.locator('.pm-explore-btn').first();
    if (await explore.count() && await explore.isEnabled().catch(() => false)) {
        await show(explore, { after: 2200 });
        mark('"Play a different move" - a what-if, off the real game');
        const legal = await page.evaluate(async () => {
            const id = localStorage.getItem('postmortem-game');
            const r = await fetch(`/api/postmortem/game/${id}`);
            return r.ok ? (await r.json()).legal_moves ?? [] : [];
        }).catch(() => []);
        if (legal.length) {
            const uci = legal[Math.min(3, legal.length - 1)];
            const sq = s2 => page.locator(`.pm-board-wrapper [data-square="${s2}"]`).first();
            await show(sq(uci.slice(0, 2)), { settle: 700, after: 800 });
            await show(sq(uci.slice(2, 4)), { settle: 500, after: 2500 });
            mark('an alternative is played');
            await wait(12000);                 // the coach answers it
            mark('the coach replies as the opponent, and says why');
            await wait(5000);
            const back = page.locator('.pm-return-btn').first();
            if (await back.count()) {
                await show(back, { after: 2600 });
                mark('back to the game as it was actually played');
            }
        }
    }

    // The coach, holding the evidence.
    await show(page.getByRole('tab', { name: 'Chat' }).or(page.locator('#pm-tab-chat')), { after: 2200 });
    mark('the review coach');
    await wait(3000);
    const chat = page.locator('.pm-chat-input').first();
    if (await chat.count()) {
        await chat.click();
        await chat.type('Where did I start losing?', { delay: 55 });
        await wait(1200);
        mark('a question typed: "Where did I start losing?"');
        await show(page.locator('.pm-chat-send').first(), { after: 1000 });
        await wait(15000);                     // the answer, and the board jump
        mark('the coach answers and the board moves to that position');
        await wait(6000);
    }
});

// =========================================================== 3. Play mode
await segment('play', async () => {
    await ensureApp('game');
    const leave = page.getByRole('button', { name: /or explore the full app/i });
    if (await leave.count()) {
        await show(leave, { after: 2000 });
        mark('out of focused Review: the three modes appear');
    }
    await show(page.getByRole('button', { name: 'Play', exact: true }), { after: 3000 });
    mark('Play: the board, the coach panel, the opponent level');
    await wait(3000);

    // The opponent level picker.
    const level = page.locator('.level-picker-trigger, .ws-meta .level-picker button').first();
    if (await level.count()) {
        await show(level, { after: 2200 });
        mark('opponent levels: Beginner to Master-like, by rating');
        await wait(3500);
        await page.keyboard.press('Escape');
        await wait(1200);
    }

    // One move, and the coach's reply.
    const sq = s => page.locator(`.chess-board-wrapper [data-square="${s}"]`).first();
    if (await sq('e2').count()) {
        await show(sq('e2'), { settle: 700, after: 700 });
        await show(sq('e4'), { settle: 500, after: 2500 });
        mark('a move is played');
        await wait(11000);                  // the AI thinks, then explains
        mark('the coach replies and explains the move');
        await wait(5000);
    }

    // The Actions tab: the switches that exist.
    const actions = page.locator('.rail-icon-btn, .analysis-rail [role="tab"]').filter({ hasText: /Actions/ }).first();
    if (await actions.count()) {
        await show(actions, { after: 2500 });
        mark('Play actions: Guided Play, engine numbers, coordinates, board size, sound');
        await wait(4000);

        // Engine numbers: the eval bar appears beside the board.
        const engine = page.locator('.game-switch', { hasText: 'Engine numbers' }).locator('input').first();
        if (await engine.count()) {
            await show(engine, { after: 2800 });
            mark('engine numbers on: the eval bar, beside the board');
            await wait(2500);
        }
        // Guided Play: the coach says what to watch for before you reply.
        const guided = page.locator('.game-switch', { hasText: 'Guided Play' }).locator('input').first();
        if (await guided.count()) {
            await show(guided, { after: 2600 });
            mark('Guided Play on');
            await wait(2000);
        }
        await glide(300, 6);
        await wait(3000);
        mark('board size, coach style, sound');
        await glide(-300, 6);
    }

    // A second move, now with Guided Play watching.
    const sq2 = s2 => page.locator(`.chess-board-wrapper [data-square="${s2}"]`).first();
    if (await sq2('d2').count()) {
        await show(sq2('d2'), { settle: 700, after: 700 });
        await show(sq2('d4'), { settle: 500, after: 2500 });
        mark('a second move, with Guided Play on');
        await wait(13000);
        mark('the coach: what to watch for before replying');
        await wait(6000);
    }

    // The moves list, with its grading toggle.
    const movesPanel = page.locator('.moves-panel').first();
    if (await movesPanel.count()) {
        await movesPanel.scrollIntoViewIfNeeded();
        await wait(3000);
        mark('the moves list, graded as you play');
    }
});

// ======================================================= 4. Learn / Sandbox
await segment('learn', async () => {
    await ensureApp('sandbox');
    await show(page.getByRole('button', { name: 'Learn', exact: true }), { after: 4000 });
    mark('Learn: a sandbox board with its own coach');
    await wait(3000);

    // One question to the coach.
    const chip = page.locator('.sandbox-chat-chip').first();
    if (await chip.count()) {
        await show(chip, { after: 1500 });
        mark('one question asked of the coach');
        await wait(13000);                  // the answer arrives
        mark('the coach answers against the position and the engine');
        await wait(5000);
    }

    // The other panels.
    for (const tab of ['Line', 'Board', 'Actions']) {
        const t = page.locator('.sandbox-tab').filter({ hasText: new RegExp(`^${tab}$`) }).first();
        if (await t.count()) {
            await show(t, { after: 2600 });
            mark(`Learn / ${tab}`);
        }
    }
    await wait(2000);

    // Ask for a position in words - the thing people do not expect to work.
    await show(page.locator('.sandbox-tab').filter({ hasText: /^Chat$/ }).first(), { after: 1800 });
    const input = page.locator('.sandbox-chat-input').first();
    if (await input.count()) {
        await input.click();
        await input.type('a rook endgame where white is slightly better', { delay: 45 });
        await wait(1500);
        mark('a position asked for in plain words');
        await show(page.locator('.sandbox-chat-send').first(), { after: 1200 });
        // Waited for by the one signal that cannot lie: the board's own FEN
        // changing. Text on screen was tried first and filmed a spinner - the
        // "Building the position..." line leaves the DOM a moment before the
        // new position arrives, so the camera moved on too early. Building a
        // position is a model call AND a legality check, so it is given a
        // long window and the truth is reported either way.
        const fenNow = async () => page.evaluate(async () => {
            const id = localStorage.getItem('sandbox-session');
            if (!id) return null;
            const r = await fetch(`/api/sandbox/session/${id}`);
            return r.ok ? (await r.json()).fen : null;
        }).catch(() => null);
        const before = await fenNow();
        let built = false;
        for (let i = 0; i < 45; i++) {
            await wait(2000);
            const now = await fenNow();
            if (now && now !== before) { built = true; break; }
        }
        await wait(3500);
        mark(built
            ? 'the position is built, validated and put on the board'
            : 'the position request did not land in time - filmed as it stands');
        await wait(6000);
    }

    // Take the board over and play one move against the coach.
    const takeover = page.locator('.sandbox-takeover-btn').first();
    if (await takeover.count()) {
        await show(takeover, { after: 2200 });
        mark('taking the board over');
        await wait(2500);
    }
});

// ============================================= 4b. light mode, for a moment
await segment('light mode', async () => {
    const toggle = page.locator('.theme-toggle').first();
    if (await toggle.count()) {
        await show(toggle, { after: 3500 });
        mark('light mode: a different design, not an inverted one');
        await wait(4000);
        await show(toggle, { after: 3000 });
        mark('back to dark');
    }
});

// ================================== 5. My improvement, in its signed-out state
await segment('my improvement', async () => {
    await page.goto(`${BASE}/profile`, { waitUntil: 'networkidle', timeout: 60000 });
    await wait(5000);
    mark('My improvement: the honest signed-out state');
    await glide(400, 8);
    await wait(4000);
    mark('what an account adds, said plainly');
    await glide(-400, 8);
    await wait(2000);
});

// ================================================= 6. the same app on a phone
await segment('responsive', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    // `/` is the BOARD for anyone who has been in, and by now this browser
    // has. `?home` is the app's own way of asking for the public page.
    await page.goto(`${BASE}/?home`, { waitUntil: 'networkidle', timeout: 60000 });
    await wait(4500);
    mark('phone width: the homepage');
    await glide(900, 12);
    await wait(3500);
    mark('phone width: the loop, stacked');
    await show(page.getByTestId('home-analyze')
        .or(page.getByRole('button', { name: /Analyze a game right now/i })), { after: 3000 });
    await page.locator('.pm-file-input').setInputFiles(PGN_PATH);
    await page.waitForSelector('.pm-board-column', { timeout: 60000 });
    await wait(5000);
    mark('phone width: the board is the full width of the screen');
    await glide(700, 10);
    await wait(4000);
    mark('phone width: the panel sits under the board');
    await page.setViewportSize({ width: 1440, height: 900 });
    await wait(2500);
});

// ---------------------------------------------------------------- wrap up
const video = page.video();
await context.close();
await browser.close();
if (video) {
    const tmp = await video.path();
    if (existsSync(OUT)) renameSync(OUT, `${OUT}.previous`);
    renameSync(tmp, OUT);
}

let mp4 = null;
if (existsSync(OUT)) {
    try {
        execFileSync('ffmpeg', [
            '-y', '-loglevel', 'error', '-i', OUT,
            '-c:v', 'libx264', '-preset', 'slow', '-crf', '21',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart', MP4,
        ], { stdio: 'inherit' });
        mp4 = `${(statSync(MP4).size / 1024 / 1024).toFixed(1)} MB`;
    } catch { mp4 = null; }
}

const total = ((Date.now() - t0) / 1000 / 60).toFixed(1);
console.log('');
console.log(`webm         artifacts/${NAME}.webm  (${(statSync(OUT).size / 1048576).toFixed(1)} MB)`);
console.log(`mp4          ${mp4 ? `artifacts/${NAME}.mp4  (${mp4})` : 'not made - no ffmpeg'}`);
console.log(`length       ${total} minutes`);
console.log(`segments     ${done.join(', ') || 'none'}`);
if (failed.length) console.log(`cut short    ${failed.join(' | ')}`);
console.log(`console      ${errors.length === 0 ? 'no errors' : [...new Set(errors)].slice(0, 3).join(' | ')}`);
console.log('');
console.log('edit list:');
for (const m of marks) console.log(`  ${m.stamp}  ${m.label}`);

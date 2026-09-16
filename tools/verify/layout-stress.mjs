/**
 * Board/layout stability stress probe.
 *
 * Unlike ui.mjs and overlap.mjs, this watches every animation frame after
 * rapid board-size, viewport, panel, Guided Play, and orientation changes.
 * A layout that eventually looks right but keeps changing size therefore
 * fails here.
 *
 * Browser zoom changes the CSS-pixel viewport available to a page. The zoom
 * cases below preserve a 1440x900 physical window and drive Chromium through
 * the corresponding CSS viewport while also setting its device scale factor.
 *
 * It also audits READABILITY (CLAUDE.md §34): in every tab of every mode, at
 * every case, every visible control and text block must be reachable - on
 * screen or inside a container that actually scrolls - and not clipped by an
 * `overflow: hidden` ancestor it has outgrown, and once scrolled to, not
 * covered by anything else. That is the check that catches "the correction
 * card is slightly out of view".
 *
 *     node tools/verify/layout-stress.mjs [http://localhost:3001] [--quick]
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';

const OPERA = `[Event "Paris Opera"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
`;

const VIEWPORTS = [
    [1920, 1080], [1536, 864], [1440, 900], [1366, 768], [1280, 720],
    [1024, 768], [900, 700], [768, 900], [430, 932], [390, 844],
].map(([width, height]) => ({ label: `${width}x${height}`, width, height, scale: 1 }));

const ZOOMS = [80, 90, 100, 110, 125, 150].map(percent => ({
    label: `1440x900 @ ${percent}%`,
    width: Math.round(1440 / (percent / 100)),
    height: Math.round(900 / (percent / 100)),
    scale: percent / 100,
}));

const QUICK = process.argv.includes('--quick');
const CASES = QUICK
    ? [VIEWPORTS[3], VIEWPORTS[4], VIEWPORTS[8], ZOOMS[4], ZOOMS[5]]
    : [...VIEWPORTS, ...ZOOMS.filter(item => item.label !== '1440x900 @ 100%')];
const MODES = {
    Play: {
        button: 'Play', root: '.chess-container', board: '.chess-board-wrapper',
        tab: '.rail-icon-btn', actions: 'Actions', rotate: null,
    },
    Learn: {
        button: 'Learn', root: '.sandbox', board: '.sandbox-board-wrapper',
        tab: '.sandbox-tab', actions: 'Actions', rotate: '.sandbox-rotate-btn',
    },
    Review: {
        button: 'Review', root: '.pm', board: '.pm-board-wrapper',
        tab: '.pm-tab', actions: 'Actions', rotate: '.pm-rotate-btn',
    },
};

let passed = 0;
let failed = 0;
const failures = [];
function check(label, ok, detail = '') {
    if (ok) {
        passed += 1;
        console.log(`PASS  ${label}`);
    } else {
        failed += 1;
        const line = `${label}${detail ? ` - ${detail}` : ''}`;
        failures.push(line);
        console.log(`FAIL  ${line}`);
    }
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
const consoleErrors = [];
page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
});
page.on('pageerror', error => consoleErrors.push(`pageerror: ${String(error)}`));

await page.addInitScript(() => {
    try {
        localStorage.setItem('chess-mode', 'game');
        localStorage.setItem('zugzwang-board-size', 'auto');
    } catch {}
});
await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('.chess-container .chess-board-wrapper');

// Mount and retain all three modes, matching normal tester behaviour. Review
// receives a real move list so its long-content and navigation layout are live.
await page.getByRole('button', { name: 'Learn', exact: true }).click();
await page.waitForSelector('.sandbox [data-square="e2"]', { timeout: 20000 });
await page.getByRole('button', { name: 'Review', exact: true }).click();
if (await page.locator('.pm-board-wrapper').count() === 0) {
    await page.setInputFiles('.pm-file-input', {
        name: 'opera-layout-stress.pgn',
        mimeType: 'application/x-chess-pgn',
        buffer: Buffer.from(OPERA),
    });
    await page.waitForSelector('.pm-board-wrapper', { timeout: 30000 });
}
await page.waitForTimeout(800);

async function setMetrics({ width, height, scale }) {
    await cdp.send('Emulation.setDeviceMetricsOverride', {
        width, height, deviceScaleFactor: scale, mobile: false,
        screenWidth: Math.round(width * scale),
        screenHeight: Math.round(height * scale),
    });
    await page.waitForTimeout(120);
}

async function frameSamples(rootSelector) {
    return page.evaluate(async root => {
        const samples = [];
        for (let index = 0; index < 48; index += 1) {
            await new Promise(resolve => requestAnimationFrame(resolve));
            const node = document.querySelector(root);
            if (!node) break;
            const value = Number.parseFloat(getComputedStyle(node).getPropertyValue('--board-size'));
            samples.push(Number.isFinite(value) ? value : -1);
        }
        return samples;
    }, rootSelector);
}

/**
 * The readability audit for one panel root.
 *
 * Every visible control (button, input, select, textarea, link) and every
 * visible text block inside `root` is scrolled to with the browser's own
 * `scrollIntoView` and then hit-tested at its centre. It passes when the
 * element it lands on is itself or one of its descendants. It fails when:
 *   - the element is clipped: it sticks out of an ancestor whose overflow is
 *     hidden/clip with no scrolling ancestor in between (scrollIntoView
 *     cannot reach it, and neither can a person);
 *   - the element is covered: something else sits on top of its centre.
 * Elements that are intentionally truncated (text-overflow: ellipsis on a
 * one-line label) are judged on their box, not their text, so they pass.
 * Returns the failures as short descriptions.
 */
function reachabilityScript(root) {
        const rootNode = document.querySelector(root);
        if (!rootNode) return ['no root'];
        const problems = [];
        const visible = el => {
            const cs = getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        };
        const label = el => (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().replace(/\s+/g, ' ').slice(0, 40);
        const candidates = [...rootNode.querySelectorAll('button, input, select, textarea, a[href], p, h1, h2, h3, h4, li, strong, .chat-message, .corr-card, .corr-step, .pm-turning-item, .sandbox-chat-msg, .review-accuracy-card, .actions-item')]
            .filter(el => el.closest('[hidden]') === null && visible(el))
            // Only leaf-ish text: a <p> inside a <li> is checked once, as the <li>.
            .filter((el, _, all) => !all.some(other => other !== el && other.contains(el) && el.tagName !== 'BUTTON' && el.tagName !== 'INPUT' && el.tagName !== 'SELECT' && el.tagName !== 'TEXTAREA'));
        for (const el of candidates) {
            const own = getComputedStyle(el);
            // A one-line label that ellipsises is allowed to be narrower than its text.
            const truncates = own.textOverflow === 'ellipsis' && own.whiteSpace === 'nowrap';
            // 1. Clipped by a hidden ancestor with no scroller in between?
            let node = el.parentElement, scroller = null, clipper = null;
            while (node && node !== document.documentElement) {
                const cs = getComputedStyle(node);
                // The page itself (body/html) is not an inner scroller: scrollIntoView handles it.
                const scrolls = node !== document.body && /(auto|scroll)/.test(cs.overflowY) && node.scrollHeight > node.clientHeight + 1;
                if (scrolls && !scroller) scroller = node;
                if (/(hidden|clip)/.test(cs.overflowY) || /(hidden|clip)/.test(cs.overflowX)) {
                    const a = node.getBoundingClientRect(), r = el.getBoundingClientRect();
                    const outY = /(hidden|clip)/.test(cs.overflowY) && (r.top < a.top - 2 || r.bottom > a.bottom + 2);
                    const outX = /(hidden|clip)/.test(cs.overflowX) && (r.left < a.left - 2 || r.right > a.right + 2) && !truncates;
                    if ((outY || outX) && !scroller) { clipper = node; break; }
                }
                node = node.parentElement;
            }
            if (clipper) {
                problems.push(`clipped: "${label(el)}" by .${(clipper.className.toString().split(' ')[0]) || clipper.tagName}`);
                continue;
            }
            // 2. Reachable and not covered once scrolled to. The scroller the
            //    element lives in is brought onto the page first (the page's
            //    scroll-padding keeps it clear of the sticky header), then the
            //    element inside it - which is what a person does.
            if (scroller) {
                // 'nearest' counts the strip under the sticky header as visible; if the
                // scroller's top is under it, bring the scroller to the page's
                // scroll-padding line instead - what a person scrolling up gets.
                const headerBottom = document.querySelector('.app-header')?.getBoundingClientRect().bottom ?? 0;
                scroller.scrollIntoView({ block: scroller.getBoundingClientRect().top < headerBottom ? 'start' : 'nearest', behavior: 'instant' });
            }
            el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
            const r = el.getBoundingClientRect();
            if (r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth) {
                problems.push(`off screen after scrollIntoView: "${label(el)}"`);
                continue;
            }
            // Hit-test inside the part of the element that is actually showing:
            // a block taller than its scroller (a long explanation) is fine
            // as long as some of it is visible and the rest scrolls.
            const headerBottom = document.querySelector('.app-header')?.getBoundingClientRect().bottom ?? 0;
            let top = Math.max(r.top, headerBottom), bottom = Math.min(r.bottom, window.innerHeight);
            let left = Math.max(r.left, 0), right = Math.min(r.right, window.innerWidth);
            if (scroller) {
                const sr = scroller.getBoundingClientRect();
                top = Math.max(top, sr.top); bottom = Math.min(bottom, sr.bottom);
                left = Math.max(left, sr.left); right = Math.min(right, sr.right);
            }
            if (bottom - top < 4 || right - left < 4) {
                problems.push(`not visible after scrollIntoView: "${label(el)}"`);
                continue;
            }
            const cx = left + Math.min(right - left, 40) / 2;
            const cy = top + Math.min(bottom - top, 24) / 2;
            const hit = document.elementFromPoint(cx, cy);
            if (!hit) continue;
            if (hit === el || el.contains(hit) || hit.contains(el)) continue;
            // A label wrapping its own control, or a control wrapping its text, is not cover.
            if (hit.closest('label') && hit.closest('label').contains(el)) continue;
            problems.push(`covered: "${label(el)}" by .${(hit.className.toString().split(' ')[0]) || hit.tagName}`);
        }
        return [...new Set(problems)];
}
async function reachabilityOn(target, rootSelector) {
    return target.evaluate(reachabilityScript, rootSelector);
}
async function reachability(rootSelector) {
    return reachabilityOn(page, rootSelector);
}
async function auditOn(target, label, root) {
    const found = await reachabilityOn(target, root);
    check(`stress ${label}: everything readable and reachable`, found.length === 0, found.slice(0, 4).join('; '));
}
/** One element: scrolled to and hit-tested, the same test the audit applies. */
async function reachableOn(target, selector) {
    return target.evaluate(sel => {
        const el = document.querySelector(sel);
        if (!el) return false;
        let p = el.parentElement;
        while (p && p !== document.documentElement) {
            const cs = getComputedStyle(p);
            if (p !== document.body && /(auto|scroll)/.test(cs.overflowY) && p.scrollHeight > p.clientHeight + 1) {
                const headerBottom = document.querySelector('.app-header')?.getBoundingClientRect().bottom ?? 0;
                p.scrollIntoView({ block: p.getBoundingClientRect().top < headerBottom ? 'start' : 'nearest', behavior: 'instant' });
                break;
            }
            p = p.parentElement;
        }
        el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.bottom < 0 || r.top > window.innerHeight) return false;
        const headerBottom = document.querySelector('.app-header')?.getBoundingClientRect().bottom ?? 0;
        const top = Math.max(r.top, headerBottom), bottom = Math.min(r.bottom, window.innerHeight);
        if (bottom - top < 4) return false;
        const cx = r.left + Math.min(r.width, 40) / 2, cy = top + Math.min(bottom - top, 24) / 2;
        const hit = document.elementFromPoint(cx, cy);
        return !!hit && (hit === el || el.contains(hit) || hit.contains(el));
    }, selector);
}

async function geometry(rootSelector, boardSelector) {
    return page.evaluate(({ rootSelector: root, boardSelector: board }) => {
        const rootNode = document.querySelector(root);
        const frame = document.querySelector(`${root} ${board}`);
        const squares = [...document.querySelectorAll(`${root} [data-square]`)]
            .map(square => square.getBoundingClientRect())
            .filter(rect => rect.width > 0 && rect.height > 0);
        if (!rootNode || !frame || squares.length !== 64) {
            return { missing: true, squareCount: squares.length };
        }
        const frameRect = frame.getBoundingClientRect();
        const minX = Math.min(...squares.map(rect => rect.left));
        const maxX = Math.max(...squares.map(rect => rect.right));
        const minY = Math.min(...squares.map(rect => rect.top));
        const maxY = Math.max(...squares.map(rect => rect.bottom));
        const squareMin = Math.min(...squares.map(rect => Math.min(rect.width, rect.height)));
        const squareMax = Math.max(...squares.map(rect => Math.max(rect.width, rect.height)));
        // The document deliberately suppresses horizontal scrolling, so
        // scrollWidth alone cannot tell us whether a control was pushed into
        // the clipped area. Measure every visible control directly as well.
        const escapedControls = [...document.querySelectorAll('button, input, select, textarea, a[href]')]
            .filter(control => {
                const rect = control.getBoundingClientRect();
                const style = getComputedStyle(control);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden'
                    && (rect.left < -1 || rect.right > window.innerWidth + 1);
            })
            .map(control => {
                const rect = control.getBoundingClientRect();
                return `${control.getAttribute('aria-label') || control.textContent?.trim() || control.tagName}`
                    + ` [${Math.round(rect.left)}, ${Math.round(rect.right)}]`;
            });
        return {
            missing: false,
            boardWidth: maxX - minX,
            boardHeight: maxY - minY,
            frameWidth: frameRect.width,
            frameHeight: frameRect.height,
            squareSpread: squareMax - squareMin,
            horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            rootRight: rootNode.getBoundingClientRect().right,
            viewportWidth: window.innerWidth,
            escapedControls,
        };
    }, { rootSelector, boardSelector });
}

for (const viewport of CASES) {
    await setMetrics(viewport);
    for (const [modeName, mode] of Object.entries(MODES)) {
        await page.getByRole('button', { name: mode.button, exact: true }).click();
        await page.waitForSelector(`${mode.root}:not([hidden])`);

        // Move through different panel-height/content shapes before sizing,
        // auditing each tab's readability on the way.
        const tabs = page.locator(`${mode.root} ${mode.tab}`);
        const tabCount = await tabs.count();
        for (let index = 0; index < tabCount; index += 1) {
            await tabs.nth(index).click();
            await page.waitForTimeout(80);
            const tabName = (await tabs.nth(index).textContent())?.trim() ?? String(index);
            const problems = await reachability(mode.root);
            check(`${modeName} ${viewport.label} [${tabName}]: everything readable and reachable`,
                problems.length === 0, problems.slice(0, 4).join('; '));
        }
        await page.locator(`${mode.root} ${mode.tab}`, { hasText: mode.actions }).click();

        const size = page.locator(`${mode.root} select[aria-label="Board size"]`);
        // Deliberately change faster than a person can. The final Auto must
        // settle even if ResizeObserver callbacks from earlier sizes are queued.
        for (const value of ['large', 'small', 'medium', 'large', 'auto']) {
            await size.selectOption(value);
            await page.waitForTimeout(18);
        }

        if (modeName === 'Play') {
            const guided = page.locator(`${mode.root} .guided-play-switch input`);
            await guided.click();
            await guided.click();
        } else if (mode.rotate) {
            const rotate = page.locator(`${mode.root} ${mode.rotate}`);
            await rotate.click();
            await rotate.click();
        }

        const samples = await frameSamples(mode.root);
        const tail = samples.slice(-20);
        const tailRange = tail.length ? Math.max(...tail) - Math.min(...tail) : Infinity;
        const changes = samples.reduce((count, value, index) => (
            index > 0 && value !== samples[index - 1] ? count + 1 : count
        ), 0);
        check(`${modeName} ${viewport.label}: board settles`, tailRange <= 1,
            `tail range ${tailRange}px; ${changes} frame changes; ${samples.join(',')}`);

        const box = await geometry(mode.root, mode.board);
        check(`${modeName} ${viewport.label}: board is present`, !box.missing,
            `${box.squareCount ?? 0} visible squares`);
        if (!box.missing) {
            check(`${modeName} ${viewport.label}: board remains square`,
                Math.abs(box.boardWidth - box.boardHeight) <= 1 && box.squareSpread <= 1,
                `${box.boardWidth.toFixed(2)}x${box.boardHeight.toFixed(2)}, square spread ${box.squareSpread.toFixed(2)}`);
            check(`${modeName} ${viewport.label}: no horizontal page overflow`,
                box.horizontalOverflow <= 1 && box.rootRight <= box.viewportWidth + 1,
                `overflow ${box.horizontalOverflow}px, root right ${box.rootRight}, viewport ${box.viewportWidth}`);
            check(`${modeName} ${viewport.label}: no visible control is clipped sideways`,
                box.escapedControls.length === 0, box.escapedControls.join('; '));
        }

        // Exercise the native popover without leaving it open over the next mode.
        await size.focus();
        await page.keyboard.press('Alt+ArrowDown').catch(() => {});
        await page.keyboard.press('Escape');

    }
}

// ---------------------------------------------------------------------------
// Long content and the correction flow (CLAUDE.md §34).
//
// The states a tester hits that the sweep above cannot reach without Gemini:
// a long coach explanation with a Guided Play section in Play's chat, and the
// whole correction card lifecycle in Review - intent, a long card, the
// evidence open, a fresh practice position, a hint, an attempt - each audited
// for readability, and each re-audited with the board at Large.
// The model's answers are served here so the shapes are deterministic; the
// rest is the real app.
// ---------------------------------------------------------------------------
const LONG = 'Lorem ipsum is not chess, so: the knight on f6 eyes e4 and g4, the bishop pair keeps the long diagonal, and the pawn on d5 is the hinge of the whole structure. '.repeat(6);
const CARD = {
    id: 'corr_stress', theme: 'THREAT_IGNORED', theme_label: "The opponent's threat went unanswered",
    player_intent: 'I wanted to grab space', missed_factor: 'Black was already creating a decisive threat. ' + LONG,
    diagnosis: LONG + LONG, correction_rule: 'Ask what the opponent\'s last move attacks or prepares before choosing your own. ' + LONG,
    confidence: 0.8, uncertainty: 'If you played this believing it was a forced defence, this diagnosis would be inaccurate. ' + LONG,
    status: 'open', created_at: 0, last_seen_at: 0, occurrence_count: 2,
    saved_to_account: false, practice_available: true, practice_unavailable_reason: null,
    practice_summary: { attempted: 0, passed: 0, rate: null, hints_used: 0, last_passed: null },
    evidence: [{ game_id: 'x', node_id: 'y', source_name: 'opera-layout-stress.pgn', white: 'Paul Morphy', black: 'Duke Karl / Count Isouard',
        ply: 8, san: 'Nf6', uci: 'g8f6', color: 'black', phase: 'opening',
        fen_before: 'rn1qkbnr/ppp2ppp/3p4/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 1 6', fen_after: 'rn1qkb1r/ppp2ppp/3p1n2/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 2 7',
        eval_before: { score: 120, mate_in: null }, eval_after: { score: 340, mate_in: null }, best_move: 'd8f6', best_san: 'Qf6',
        pv_san: ['Qf6', 'Qxf6', 'Nxf6', 'Nc3', 'Be7', 'd3', 'O-O'], cpl: 220, quality: { label: 'mistake', cpl: 220 }, depth: 12,
        intent: 'I wanted to grab space', diagnosis_source: 'coach', at: 0 }],
    attempts: [],
};
const PRACTICE = { available: true, theme: 'THREAT_IGNORED', attempt_index: 1, check: 'Did you see what the last move attacked?',
    position: { fen: 'r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 4 4', prompt: 'White is threatening mate on f7. Find the move that keeps the position together. ' + LONG.slice(0, 200) } };

const STRESS_CASES = QUICK ? [VIEWPORTS[3], VIEWPORTS[8], ZOOMS[5]] : [VIEWPORTS[0], VIEWPORTS[3], VIEWPORTS[4], VIEWPORTS[6], VIEWPORTS[8], VIEWPORTS[9], ZOOMS[4], ZOOMS[5]];
{
    const page2 = await context.newPage();
    const cdp2 = await context.newCDPSession(page2);
    page2.on('pageerror', error => consoleErrors.push(`pageerror: ${String(error)}`));
    await page2.addInitScript(() => { try { localStorage.setItem('chess-mode', 'game'); localStorage.removeItem('chess-active-section'); localStorage.setItem('postmortem-panel', 'chat'); } catch {} });
    // Play: the real status, with a long coach turn and a Watch out section on the end.
    await page2.route('**/api/status', async route => {
        const real = await route.fetch();
        const body = await real.json();
        const watch = 'Is the piece I just moved loose, and is your e4 pawn still defended twice? ' + LONG.slice(0, 260);
        body.chat_history = [
            { role: 'model', text: '**e4** \u2014 ' + LONG, move: 'e4' },
            { role: 'user', text: 'why?' },
            { role: 'model', text: LONG },
            { role: 'model', text: '**Nf3** \u2014 ' + LONG + '\n\nWatch out: ' + watch, move: 'Nf3', watch_out: watch },
        ];
        await route.fulfill({ response: real, body: JSON.stringify(body), headers: { ...real.headers(), 'content-type': 'application/json' } });
    });
    await page2.route('**/api/learning-loop/diagnose', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        correction: CARD, recurred: true, diagnosis_source: 'coach', practice_available: true, best_move: 'd8f6', best_san: 'Qf6', node_id: 'y' }) }));
    await page2.route('**/api/learning-loop/practice/start', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(PRACTICE) }));
    await page2.route('**/api/learning-loop/practice/hint', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ hint: 'The move you are looking for: it is a queen move, and it defends f7 while developing. ' + LONG.slice(0, 120) }) }));
    await page2.route('**/api/learning-loop/practice/attempt', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        passed: false, best_san: 'Qe7', best_uci: 'd8e7', played_san: 'g6', correction: { ...CARD, practice_summary: { attempted: 1, passed: 0, rate: 0, hints_used: 1, last_passed: false } } }) }));
    await page2.route('**/api/learning-loop/corrections', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ corrections: [CARD, { ...CARD, id: 'corr_2', theme_label: 'A tactic was missed or allowed' }], count: 2 }) }));
    await page2.route('**/api/learning-loop/correction/*/status', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ correction: CARD }) }));

    await page2.goto(BASE, { waitUntil: 'networkidle' });
    await page2.waitForSelector('.chess-container .chess-board-wrapper');
    await page2.getByRole('button', { name: 'Review', exact: true }).click();
    if (await page2.locator('.pm-board-wrapper').count() === 0) {
        await page2.setInputFiles('.pm-file-input', { name: 'opera-stress.pgn', mimeType: 'application/x-chess-pgn', buffer: Buffer.from(OPERA) });
        await page2.waitForSelector('.pm-board-wrapper', { timeout: 30000 });
    }
    await page2.waitForTimeout(600);

    for (const viewport of STRESS_CASES) {
        await cdp2.send('Emulation.setDeviceMetricsOverride', { width: viewport.width, height: viewport.height, deviceScaleFactor: viewport.scale, mobile: false, screenWidth: Math.round(viewport.width * viewport.scale), screenHeight: Math.round(viewport.height * viewport.scale) });
        await page2.waitForTimeout(150);

        // --- Play, long chat + Watch out --------------------------------
        await page2.getByRole('button', { name: 'Play', exact: true }).click();
        await page2.locator('.chess-container .rail-icon-btn', { hasText: 'Chat' }).click();
        await page2.waitForTimeout(200);
        await auditOn(page2, `${viewport.label} Play chat, long explanation`, '.chess-container');
        check(`stress ${viewport.label}: the Watch out block is reachable`, await reachableOn(page2, '.chess-container .chat-watchout'));
        check(`stress ${viewport.label}: the composer is reachable under a long chat`, await reachableOn(page2, '.chess-container .chat-input'));
        const chatScrolls = await page2.locator('.chess-container .chat-messages').evaluate(el => el.scrollHeight > el.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(el).overflowY));
        check(`stress ${viewport.label}: the long chat scrolls inside its list`, chatScrolls || viewport.width < 1101);

        // --- Review, the correction lifecycle ----------------------------
        // Twice per case: with the board at Auto and at Large. The size is
        // set before the flow starts because leaving the Correct tab abandons
        // a correction in progress - the flow is not crossed mid-way.
        for (const boardSize of ['auto', 'large']) {
            const tag = `${viewport.label}${boardSize === 'large' ? ' +Large board' : ''}`;
            await page2.getByRole('button', { name: 'Review', exact: true }).click();
            await page2.locator('.pm .pm-tab', { hasText: 'Actions' }).click();
            await page2.locator('.pm select[aria-label="Board size"]').selectOption(boardSize);
            await page2.waitForTimeout(200);
            await page2.locator('.pm .pm-tab', { hasText: 'Moves' }).click();
            await page2.waitForTimeout(150);
            await page2.locator('.pm .pm-move').nth(8).click();
            await page2.waitForTimeout(250);
            await page2.locator('.pm .pm-tab', { hasText: 'Correct' }).click();
            await page2.waitForTimeout(250);
            await auditOn(page2, `${tag} Correct: intent`, '.pm');
            const chip = page2.locator('.pm .corr-chip').first();
            if (await chip.count()) await chip.click();
            await page2.locator('.pm .corr-textarea').fill('I wanted to grab space ' + LONG.slice(0, 200));
            await page2.locator('.pm .corr-primary', { hasText: /Show me what I missed/ }).click();
            await page2.waitForSelector('.pm .corr-card', { timeout: 10000 });
            await page2.waitForTimeout(250);
            await auditOn(page2, `${tag} Correct: long card`, '.pm');
            await page2.locator('.pm .corr-primary', { hasText: 'Practice this' }).scrollIntoViewIfNeeded();
            check(`stress ${tag}: the card's primary action is reachable`, await reachableOn(page2, '.pm .corr-primary'));
            await page2.locator('.pm .corr-link', { hasText: 'Why do you think this' }).click();
            await page2.waitForTimeout(200);
            await auditOn(page2, `${tag} Correct: evidence open`, '.pm');
            await page2.locator('.pm .corr-chip', { hasText: "That's not what I was doing" }).click().catch(() => {});
            await page2.waitForTimeout(150);
            // Practice, hint, attempt.
            await page2.locator('.pm .corr-primary', { hasText: 'Practice this' }).click();
            await page2.waitForSelector('.pm .corr-retest-board', { timeout: 10000 });
            await page2.waitForTimeout(250);
            await auditOn(page2, `${tag} Correct: practice`, '.pm');
            check(`stress ${tag}: the practice board is reachable`, await reachableOn(page2, '.pm .corr-retest-board'));
            await page2.locator('.pm .corr-link', { hasText: 'Give me a hint' }).click();
            await page2.waitForTimeout(250);
            await auditOn(page2, `${tag} Correct: practice with hint`, '.pm');
            // An attempt through the board: click a piece with targets, then a target.
            const played = await page2.evaluate(async () => {
                const squares = [...document.querySelectorAll('.pm .corr-retest-board [data-square]')];
                for (const sq of squares) {
                    if (!sq.querySelector('[data-piece]')) continue;
                    sq.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    await new Promise(r => setTimeout(r, 60));
                    const target = squares.find(t => /sq-(legal|capture)/.test(t.firstElementChild?.getAttribute('style') ?? ''));
                    if (target) { target.dispatchEvent(new MouseEvent('click', { bubbles: true })); return true; }
                }
                return false;
            });
            if (played) {
                await page2.waitForSelector('.pm .corr-note[role=status]', { timeout: 10000 }).catch(() => {});
                await page2.waitForTimeout(250);
                await auditOn(page2, `${tag} Correct: after an attempt`, '.pm');
                check(`stress ${tag}: "Another one" is reachable after an attempt`, await reachableOn(page2, '.pm .corr-practice-actions .action-btn'));
            } else {
                check(`stress ${tag}: an attempt could be played`, false, 'no piece with targets');
            }
            // Leave the flow (which abandons it) so the next pass starts clean.
            await page2.locator('.pm .pm-tab', { hasText: 'Chat' }).click();
            await page2.waitForTimeout(100);
        }
        await page2.locator('.pm .pm-tab', { hasText: 'Actions' }).click();
        await page2.locator('.pm select[aria-label="Board size"]').selectOption('auto');
    }
    await cdp2.send('Emulation.clearDeviceMetricsOverride');
    await page2.close();
}

const meaningfulErrors = [...new Set(consoleErrors)].filter(message =>
    !message.includes('favicon')
);
check('stress run: no console or ResizeObserver errors', meaningfulErrors.length === 0,
    meaningfulErrors.slice(0, 4).join(' | '));

await cdp.send('Emulation.clearDeviceMetricsOverride');
await browser.close();

console.log(`\n${passed}/${passed + failed} passed`);
if (failures.length) {
    console.log('\nFailures:');
    for (const failure of failures) console.log(`- ${failure}`);
}
process.exit(failed ? 1 : 0);

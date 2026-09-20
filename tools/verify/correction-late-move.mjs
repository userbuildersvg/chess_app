/**
 * The correction form on a late move of a real multi-game file - the shape a
 * production report came in as: Sindarov-Aronian, Sinquefield Cup 2026,
 * first of the games in the file, "Working on 16. Nb3".
 *
 *   Correct tab on 16. Nb3 -> click "Show me what I missed" with no intention
 *   -> a visible line asks for one (never a button that does nothing) ->
 *   pick an intention -> click -> pending state is shown -> a card or a
 *   visible error follows. The form never comes back identical and silent.
 *
 * Needs the backend on :8081 with BETA_ACCESS_REQUIRED=false and a Gemini key.
 *
 *     node tools/verify/correction-late-move.mjs [http://localhost:3001]
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';
import { readFileSync } from 'node:fs';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';
const PGN = readFileSync(new URL('./fixtures/sincup26.pgn', import.meta.url), 'utf8');
const PM = '.pm';

let passed = 0, failed = 0;
const check = (label, ok, detail = '') => {
    if (ok) { passed++; console.log(`PASS  ${label}`); }
    else { failed++; console.log(`FAIL  ${label}${detail ? ` - ${detail}` : ''}`); }
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
const diagnoses = [];
page.on('response', r => { if (/learning-loop\/diagnose/.test(r.url())) diagnoses.push(r.status()); });

await page.goto(BASE, { waitUntil: 'networkidle' });
const imported = await page.evaluate(async pgn => {
    const r = await fetch('/api/postmortem/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pgn, source_name: 'sincup26.pgn' }) });
    const j = await r.json();
    await fetch(`/api/postmortem/game/${j.game_id}/analyse`, { method: 'POST' });
    localStorage.setItem('chess-mode', 'postmortem');
    localStorage.setItem('postmortem-game', j.game_id);
    localStorage.setItem('postmortem-panel', 'chat');
    return j;
}, PGN);
const gid = imported.game_id;
check('the multi-game file imports its first game', imported.game_count === 2 && imported.headers?.White === 'Sindarov, Javokhir', JSON.stringify({ count: imported.game_count, headers: imported.headers }).slice(0, 200));
await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector(`${PM} .pm-board-column`, { timeout: 20000 });
const scan = () => page.evaluate(async id => (await (await fetch(`/api/postmortem/game/${id}/analysis`)).json()).scan, gid);
for (let i = 0; i < 180 && (await scan()).status !== 'done'; i++) await page.waitForTimeout(1000);
check('the scan of a 57-move game completed', (await scan()).status === 'done', JSON.stringify(await scan()));

const tab = name => page.locator(`${PM} [role=tab]`, { hasText: name });
const corr = page.locator(`${PM} .corr-panel`);
const state = () => page.evaluate(() => {
    const p = document.querySelector('.pm .corr-panel');
    const b = p?.querySelector('.corr-primary');
    return p && {
        move: p.querySelector('.corr-move')?.textContent?.trim(),
        card: !!p.querySelector('.corr-card'),
        button: b?.textContent?.trim(), disabled: b?.disabled,
        status: p.querySelector('[role=status]')?.textContent?.trim(),
        alert: p.querySelector('[role=alert]')?.textContent?.trim() ?? null,
    };
});

await tab('Moves').click();
await page.locator(`${PM} button`, { hasText: /^Nb3$/ }).first().click();
await tab('Correct').click();
await page.waitForSelector(`${PM} .corr-panel .corr-question`, { timeout: 10000 });
let s = await state();
check('Correct is working on 16. Nb3', s.move === '16. Nb3', s.move);
check('the submit button is clickable before an intention is picked', !s.disabled && /Show me what I missed/.test(s.button ?? ''), JSON.stringify(s));

// --- a click with nothing chosen must say so ------------------------------
await corr.locator('.corr-primary').click();
await page.waitForTimeout(300);
s = await state();
check('clicking with no intention shows a visible line asking for one', /say what you were going for/i.test(s.alert ?? ''), JSON.stringify(s));
check('...and sends no request', diagnoses.length === 0);
const inPanelView = sel => page.locator(`${PM} .corr-panel ${sel}`).first().evaluate(el => {
    let sc = el.parentElement;
    while (sc && sc !== document.documentElement && !/(auto|scroll)/.test(getComputedStyle(sc).overflowY)) sc = sc.parentElement;
    const r = el.getBoundingClientRect(), p = (sc ?? document.documentElement).getBoundingClientRect();
    return r.height > 0 && r.top >= p.top - 1 && r.top < p.bottom && r.top < innerHeight;
});
check('the line is inside the visible part of the panel, next to the button',
    await inPanelView('[role=alert]') && await corr.locator('[role=alert] + .corr-primary').count() === 1);
const contrast = await corr.locator('[role=alert]').evaluate(el => {
    // Relative luminance of text vs. the note's own background, after the
    // wash is composited over the panel (light theme once painted white on beige).
    const rgb = s => s.match(/[\d.]+/g).map(Number);
    const lum = ([r, g, b]) => [r, g, b].map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }).reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
    const cs = getComputedStyle(el);
    const [r, g, b, a = 1] = rgb(cs.backgroundColor);
    let under = el.parentElement, pr = 255, pg = 255, pb = 255;
    for (; under; under = under.parentElement) {
        const c = rgb(getComputedStyle(under).backgroundColor);
        if ((c[3] ?? 1) > 0) { [pr, pg, pb] = c; break; }
    }
    const bg = [r * a + pr * (1 - a), g * a + pg * (1 - a), b * a + pb * (1 - a)];
    const l1 = lum(rgb(cs.color)), l2 = lum(bg);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
});
check(`...and is readable (contrast ${contrast.toFixed(1)}:1, needs 4.5)`, contrast >= 4.5);

// --- a pressed chip has to look pressed --------------------------------------
const chipLook = () => corr.locator('.corr-chip', { hasText: "I wasn't sure" }).evaluate(el => {
    const cs = getComputedStyle(el);
    return { pressed: el.getAttribute('aria-pressed'), bg: cs.backgroundColor, color: cs.color, mark: getComputedStyle(el, '::before').content };
});
check('the form says what to do', /Not sure\? That is useful too/.test(await corr.locator('[data-testid="corr-hint"]').innerText()));
const before = await chipLook();
await corr.locator('.corr-chip', { hasText: "I wasn't sure" }).click();
await page.waitForTimeout(400);
const after = await chipLook();
check('"I wasn\'t sure" reports pressed', after.pressed === 'true' && before.pressed === 'false');
check('...and its fill and text colour actually change', after.bg !== before.bg && after.color !== before.color, JSON.stringify({ before, after }));
check('...and it carries a check mark', /✓/.test(after.mark) && !/✓/.test(before.mark), after.mark);
check('the hint changes once an intention is chosen', /Then continue/.test(await corr.locator('[data-testid="corr-hint"]').innerText()));

// --- with an intention, the request is observed and answered --------------
await corr.locator('.corr-primary').click();
s = await state();
check('the click is acknowledged at once: pending button and status line', /Preparing correction/.test(s.button ?? '') && /Preparing correction/.test(s.status ?? ''), JSON.stringify(s));
await page.waitForFunction(() => {
    const p = document.querySelector('.pm .corr-panel');
    return p && (p.querySelector('.corr-card') || p.querySelector('[role=alert]'));
}, null, { timeout: 90000 }).catch(() => {});
s = await state();
check('one diagnose request was sent', diagnoses.length === 1, JSON.stringify(diagnoses));
check('a card or a visible error followed - never the same silent form', s.card || s.alert !== null, JSON.stringify(s));
check('the card is for 16. Nb3', !s.card || s.move === '16. Nb3', s.move);
check('the top of the card is in view - the panel scrolled to it', !s.card || await inPanelView('.corr-card'));
check('no console or page errors', errors.length === 0, errors.join(' | '));
await browser.close();
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

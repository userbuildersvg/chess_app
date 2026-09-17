/**
 * A diagnosis that is still running when the player moves on.
 *
 *   Correct: "Show me what I missed" -> click another move before the coach
 *   answers -> the new move gets a usable intent form and a one-line notice,
 *   and the old answer never lands under the wrong move. Same again when the
 *   Correct tab is left and reopened while the request is in flight.
 *
 * Needs the backend on :8081 with BETA_ACCESS_REQUIRED=false and a Gemini key.
 *
 *     node tools/verify/correction-pending.mjs [http://localhost:3001]
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';
const PGN = '[Event "pending"]\n[White "me"]\n[Black "opp"]\n[Result "0-1"]\n\n1. e4 e5 2. Nf3 Nc6 3. d4 exd4 4. Nxd4 Nf6 5. Qd3 d5 6. Qb5 a6 7. Qa4 Bd7 8. Qb3 Nxd4 9. Qxd5 Nxd5 0-1';
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

await page.goto(BASE, { waitUntil: 'networkidle' });
const gid = await page.evaluate(async pgn => {
    const r = await fetch('/api/postmortem/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pgn, source_name: 'pending.pgn' }) });
    const j = await r.json();
    await fetch(`/api/postmortem/game/${j.game_id}/analyse`, { method: 'POST' });
    localStorage.setItem('chess-mode', 'postmortem');
    localStorage.setItem('postmortem-game', j.game_id);
    localStorage.setItem('postmortem-panel', 'chat');
    return j.game_id;
}, PGN);
await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector(`${PM} .pm-board-column`, { timeout: 20000 });
const scan = () => page.evaluate(async id => (await (await fetch(`/api/postmortem/game/${id}/analysis`)).json()).scan, gid);
for (let i = 0; i < 90 && (await scan()).status !== 'done'; i++) await page.waitForTimeout(1000);
check('the review scan completed', (await scan()).status === 'done');

const tab = name => page.locator(`${PM} [role=tab]`, { hasText: name });
const corr = page.locator(`${PM} .corr-panel`);
const submit = () => corr.locator('.corr-primary', { hasText: /Show me what I missed|Preparing correction/ });
const state = () => page.evaluate(() => {
    const p = document.querySelector('.pm .corr-panel');
    return p && {
        move: p.querySelector('.corr-move')?.textContent?.trim(),
        card: !!p.querySelector('.corr-card'),
        button: p.querySelector('.corr-primary')?.textContent?.trim(),
        disabled: p.querySelector('.corr-primary')?.disabled,
        notice: p.querySelector('.corr-progress')?.textContent?.trim(),
        alert: p.querySelector('[role=alert]')?.textContent?.trim() ?? null,
    };
});

await tab('Report').click();
await page.locator(`${PM} [data-testid="pm-key-cta"]`).click();
await page.waitForSelector(`${PM} .corr-panel .corr-question`, { timeout: 10000 });
const firstMove = (await state()).move;
check(`Correct opened on the key decision (${firstMove})`, /^\d+\.+ \S+$/.test(firstMove ?? ''));

// --- 1. another move is picked while the coach is still answering -----------
await corr.locator('.corr-chip').first().click();
await submit().click();
let s = await state();
check('the request shows a pending state', /Preparing correction/.test(s.button ?? '') && /Preparing correction/.test(s.notice ?? ''), JSON.stringify(s));
await tab('Moves').click();
await page.locator(`${PM} button`, { hasText: /^Qd3$/ }).first().click();
await tab('Correct').click();
await page.waitForTimeout(600);
s = await state();
check('the panel now names the new move', s.move !== firstMove && /Qd3/.test(s.move ?? ''), s.move);
check('the new move gets a usable form, not a disabled "Preparing…" button', /Show me what I missed/.test(s.button ?? ''), JSON.stringify(s));
check('a notice says the earlier correction was set aside and names its move',
    /set aside/.test(s.notice ?? '') && s.notice.includes(firstMove), s.notice);
// Wait past the old request. It must not land here.
await page.waitForTimeout(25000);
s = await state();
check('the old answer never appears under the new move', !s.card && /Qd3/.test(s.move ?? ''), JSON.stringify(s));
check('...and no error is shown for it', s.alert === null, s.alert);
check('the notice is still there to read', /set aside/.test(s.notice ?? ''), s.notice);

// --- 2. stepping back and asking again still works -------------------------
await tab('Report').click();
await page.locator(`${PM} [data-testid="pm-key-cta"]`).click();
await page.waitForSelector(`${PM} .corr-panel .corr-question`, { timeout: 10000 });
s = await state();
check('back on the first move the notice is gone and the form is fresh', s.move === firstMove && !/set aside/.test(s.notice ?? '') && !s.card, JSON.stringify(s));
await corr.locator('.corr-chip').first().click();
await submit().click();
await page.waitForSelector(`${PM} .corr-card`, { timeout: 90000 });
s = await state();
check('asking again produces the card for that move', s.card && s.move === firstMove, JSON.stringify(s));

// --- 3. the Correct tab is left while the coach is answering ---------------
await tab('Moves').click();
await page.locator(`${PM} button`, { hasText: /^Qb5$/ }).first().click();
await tab('Correct').click();
await page.waitForSelector(`${PM} .corr-panel .corr-question`, { timeout: 10000 });
const thirdMove = (await state()).move;
await corr.locator('.corr-chip').first().click();
await submit().click();
await tab('Report').click();
await page.waitForTimeout(1500);
await tab('Correct').click();
await page.waitForTimeout(600);
s = await state();
check('reopening Correct mid-request shows the form for the same move', s.move === thirdMove && /Show me what I missed/.test(s.button ?? ''), JSON.stringify(s));
check('...with the set-aside notice rather than a blank form', /set aside/.test(s.notice ?? '') && s.notice.includes(thirdMove), s.notice);
await page.waitForTimeout(25000);
s = await state();
check('the abandoned answer does not surface later', !s.card && s.alert === null, JSON.stringify(s));

check('no console or page errors', errors.length === 0, errors.join(' | '));
await browser.close();
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

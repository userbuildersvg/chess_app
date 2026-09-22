/**
 * "Where did I start losing?" in Review chat, in a real browser against :3001.
 *
 *     node tools/verify/turning-point.mjs
 *
 * Imports a Scholar's mate, waits for the scan, asks the question, and checks
 * that the answer names the engine's turning point (3...Nf6, the move that
 * allows Qxf7#), that the board jumped to the position before it, that the
 * answer's rows offer Jump to position and Correct this decision, and that
 * Correct opens on that exact move. One real coach reply is requested; if the
 * model is down the deterministic fallback must still name the move.
 * test_turning_point.py proves the selection itself.
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';
import { requireLive } from './live.mjs';

const BASE = process.env.BASE || 'http://localhost:3001';
let pass = 0, fail = 0;
const check = (label, cond, detail) => {
    if (cond) { pass++; console.log('PASS  ' + label); }
    else { fail++; console.log('FAIL  ' + label + (detail !== undefined ? ' - ' + JSON.stringify(detail).slice(0, 300) : '')); }
};

const PGN = `[Event "Casual"]
[White "Them"]
[Black "You"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
`;

requireLive('"Where did I start losing?" (a real coach answer)');

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await page.addInitScript(() => { try {
    if (sessionStorage.getItem('tp-init')) return;
    sessionStorage.setItem('tp-init', '1');
    localStorage.setItem('chess-mode', 'postmortem');
    localStorage.removeItem('postmortem-game');
    localStorage.setItem('postmortem-panel', 'chat');
} catch { /* fine */ } });

const gameId = () => page.evaluate(() => localStorage.getItem('postmortem-game'));
const state = async () => (await page.request.get(`${BASE}/api/postmortem/game/${await gameId()}`)).json();
const ask = async (text) => {
    const before = await page.locator('.pm-chat-model:not(.is-pending)').count();
    await page.fill('.pm-chat-input', text);
    await page.locator('.pm-chat-send').click();
    await page.waitForFunction(n => document.querySelectorAll('.pm-chat-model:not(.is-pending)').length > n, before, { timeout: 60000 });
    return page.locator('.pm-chat-model:not(.is-pending)').last();
};

try {
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.setInputFiles('.pm-file-input', { name: 'scholars.pgn', mimeType: 'application/x-chess-pgn', buffer: Buffer.from(PGN) });
    await page.waitForSelector('.pm-board-column', { timeout: 20000 });
    // The scan starts on import; wait it out through the API.
    let s = await state();
    for (let i = 0; i < 120 && s.scan?.status !== 'done' && s.scan?.status !== 'failed'; i++) { await page.waitForTimeout(500); s = await state(); }
    check('the scan finished', s.scan?.status === 'done', s.scan);
    // Stand on the final position so the jump is observable.
    await page.request.post(`${BASE}/api/postmortem/game/${await gameId()}/goto`, { data: { node_id: s.moves[s.moves.length - 1].node_id } });
    await page.reload({ waitUntil: 'networkidle' });
    // Import lands on Report; the question is asked in Chat.
    await page.locator('.pm [role=tab]', { hasText: 'Chat' }).click();
    await page.waitForSelector('.pm-chat-input', { timeout: 20000 });
    check('standing on the last move before asking', (await state()).ply === 7, (await state()).ply);

    const reply = await ask('Where did I start losing?');
    const text = await reply.innerText();
    check('the answer names the engine-selected turning point (Nf6)', /Nf6/.test(text), text.slice(0, 300));
    const rows = reply.locator('[data-testid="pm-turning-actions"] .pm-chat-turning-row');
    check('the answer carries action rows', await rows.count() >= 1, await rows.count());
    check('the first row is 3...Nf6', /3\.\.\.\s*Nf6/.test(await rows.first().locator('.pm-chat-turning-move').innerText()), await rows.first().innerText());
    check('it offers Jump to position and Correct this decision',
          await rows.first().locator('button', { hasText: 'Jump to position' }).count() === 1
          && await rows.first().locator('button', { hasText: 'Correct this decision' }).count() === 1);

    await page.waitForTimeout(600);
    s = await state();
    check('the board jumped to the position before the turning point (ply 5)', s.ply === 5, s.ply);
    check('the turning-point answer stays in Chat instead of opening Report or Correct',
          (await page.locator('.pm [role=tab][aria-selected="true"]').innerText()).trim() === 'Chat');

    await page.request.post(`${BASE}/api/postmortem/game/${await gameId()}/goto`, { data: { node_id: s.moves[s.moves.length - 1].node_id } });
    await page.waitForTimeout(300);
    await rows.first().locator('button', { hasText: 'Jump to position' }).click();
    await page.waitForTimeout(800);
    check('Jump to position returns to ply 5 from elsewhere', (await state()).ply === 5, (await state()).ply);

    await rows.first().locator('button', { hasText: 'Correct this decision' }).click();
    await page.waitForSelector('.pm [role=tab][aria-selected="true"]', { timeout: 10000 });
    check('Correct tab opened', (await page.locator('.pm [role=tab][aria-selected="true"]').innerText()).trim().startsWith('Correct'));
    check('Correct is on the turning-point move', /Nf6/.test(await page.locator('.pm .corr-panel .corr-move').innerText().catch(() => '')),
          await page.locator('.pm .corr-panel').innerText().catch(() => '').then(t => t.slice(0, 200)));
    check('the board is on the position after Nf6 (ply 6), which Correct diagnoses', (await state()).ply === 6, (await state()).ply);

    // Remount keeps the rows: the answer lives in the transcript, not in React state.
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.pm [role=tab]', { hasText: 'Chat' }).click();
    await page.waitForSelector('[data-testid="pm-turning-actions"]', { timeout: 10000 });
    check('after a reload the answer still carries its buttons', await page.locator('[data-testid="pm-turning-actions"] button').count() >= 2);

    // Report graph selection is navigation, not consent to start Correct.
    await page.locator('.pm [role=tab]', { hasText: 'Report' }).click();
    await page.waitForSelector('.pm-curve-hit');
    await page.locator('.pm-curve-hit').nth(2).click();
    await page.waitForSelector('[data-testid="pm-selected-decision"]');
    check('a graph point stays in Report and shows its selected decision',
          (await page.locator('.pm [role=tab][aria-selected="true"]').innerText()).trim() === 'Report'
          && /Selected decision/.test(await page.locator('[data-testid="pm-selected-decision"]').innerText()));
    const selectedPly = (await state()).ply;
    await page.locator('[data-testid="pm-selected-cta"]').click();
    check('only the selected decision CTA opens Correct',
          (await page.locator('.pm [role=tab][aria-selected="true"]').innerText()).trim().startsWith('Correct'));
    check('the graph-selected move is the move Correct receives', (await state()).ply === selectedPly, { selectedPly, actual: (await state()).ply });

    // An ordinary question does not get the rows.
    await page.locator('.pm [role=tab]', { hasText: 'Chat' }).click();
    const plain = await ask('Why was that a mistake?').catch(() => null);
    if (plain) check('an ordinary question has no turning-point rows', await plain.locator('[data-testid="pm-turning-actions"]').count() === 0);
    else console.log('SKIP  ordinary question (coach unavailable)');

    check('no page errors', errors.length === 0, errors);
} finally {
    await browser.close();
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

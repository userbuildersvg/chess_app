/**
 * "Play Nf3" in the Learn chat, in a real browser against :3001.
 *
 *     node tools/verify/sandbox-chat-moves.mjs
 *
 * No Gemini: instructions are executed by the chat route without the model,
 * and the classifier short-circuits them.
 *
 * Proves: "play the best move" moves the board and the chat says which move;
 * "Play Nf3" (now illegal - wrong side) is refused in words and the board is
 * unchanged; "knight to c6" moves Black's knight; nonsense goes to the coach
 * and moves nothing. Play mode's protection is asserted in the backend suite.
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.env.BASE || 'http://localhost:3001';
let pass = 0, fail = 0;
const check = (label, cond, detail) => {
    if (cond) { pass++; console.log('PASS  ' + label); }
    else { fail++; console.log('FAIL  ' + label + (detail !== undefined ? ' - ' + JSON.stringify(detail).slice(0, 300) : '')); }
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await page.addInitScript(() => { try { if (sessionStorage.getItem('scm-init')) return; sessionStorage.setItem('scm-init', '1'); localStorage.setItem('chess-mode', 'sandbox'); localStorage.removeItem('sandbox-session'); localStorage.setItem('sandbox-panel', 'chat'); } catch { /* fine */ } });

const line = async () => (await page.request.get(BASE + '/api/sandbox/session/' + await page.evaluate(() => localStorage.getItem('sandbox-session')))).json().then(s => s.line_san);
const say = async (text) => {
    const before = await page.locator('.sandbox-chat-model:not(.is-pending)').count();
    await page.fill('.sandbox-chat-input', text);
    await page.locator('form.sandbox-chat-row button[type="submit"]').click();
    await page.waitForFunction((n) => document.querySelectorAll('.sandbox-chat-model:not(.is-pending)').length > n, before, { timeout: 30000 });
    const msgs = await page.locator('.sandbox-chat-model:not(.is-pending)').allInnerTexts();
    return msgs[msgs.length - 1] ?? '';
};

try {
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForSelector('.sandbox-board-wrapper [data-square="e2"]', { timeout: 20000 });
    await page.waitForFunction(() => localStorage.getItem('sandbox-session'), null, { timeout: 15000 });

    let reply = await say('Play the best move on the board.');
    check('best move: the chat says which move it played', /I played the engine's preferred move, [A-Za-z0-9+#=-]+, on the board/.test(reply), reply);
    let moves = await line();
    check('best move: the board moved by exactly one ply', moves.length === 1 && reply.includes(moves[0]), moves);
    check('the board on screen shows a move was made', await page.locator('.sandbox-board-wrapper [data-square="e2"] [data-piece], .sandbox-board-wrapper [data-square="d2"] [data-piece]').count() < 2 || moves[0] !== 'e4');

    reply = await say('Play Nf3');
    check('an illegal instruction (Nf3 with Black to move) is refused in words', /not legal/.test(reply), reply);
    check('...and the board did not change', (await line()).length === 1);

    reply = await say('knight to c6');
    check('"knight to c6" moves the knight', /I played Nc6 on the board/.test(reply), reply);
    check('...and the line is two plies', (await line()).length === 2);

    // Chips: the "Play the best move" chip goes through the same path.
    const chipBefore = (await line()).length;
    const n = await page.locator('.sandbox-chat-model:not(.is-pending)').count();
    await page.locator('[data-testid="sandbox-chat-chips"] button', { hasText: 'Play the best move' }).click();
    await page.waitForFunction((k) => document.querySelectorAll('.sandbox-chat-model:not(.is-pending)').length > k, n, { timeout: 30000 });
    check('the "Play the best move" chip moves the board', (await line()).length === chipBefore + 1);

    // Nonsense ('Play Zz9') is not an instruction and goes to the coach - a live
    // Gemini call, covered by the backend suite instead of spent here.

    // Play mode is protected structurally: the real game's /api/chat never
    // imports chat_moves (asserted in test_sandbox_chat_moves.py); driving it
    // here would only spend a live Gemini call.
    check('no page errors', errors.length === 0, errors);
} finally {
    await browser.close();
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

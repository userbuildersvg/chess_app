/**
 * The unified Chat + Actions panel, driven live (the UI/UX sprint that
 * removed the Coach tabs). Same harness as the other tools here; needs the
 * backend on :8081 with BETA_ACCESS_REQUIRED=false and a real Gemini key,
 * because the thing under test is the coach speaking.
 *
 *     node tools/verify/chat.mjs [http://localhost:3001] [--shots out/]
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';
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

const browser = await chromium.launch();
const PLAY = '.chess-container', LEARN = '.sandbox', PM = '.pm';

const OPERA = `[Event "Paris Opera"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
`;

// ---------------------------------------------------------------- Play ---
{
    console.log('\n--- Play ---');
    const errors = [];
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
    await page.addInitScript(() => { try { localStorage.setItem('chess-mode', 'game'); localStorage.removeItem('chess-active-section'); } catch {} });
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    await page.evaluate(() => fetch('/api/reset', { method: 'POST' }));
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);

    const tabs = async () => page.locator(`${PLAY} .rail-icon-btn .rail-label`).allTextContents();
    const labels = await tabs();
    check('Play has no Coach tab', !labels.includes('Coach'), labels.join(','));
    check('Play has Chat and Actions tabs', labels.includes('Chat') && labels.includes('Actions'), labels.join(','));
    check('Play lands on Chat', await page.locator(`${PLAY} .rail-icon-btn.active .rail-label`).textContent() === 'Chat');

    const transport = await page.locator(`${PLAY} .game-transport button`).allTextContents();
    check('Make AI move and Play as Black stay under the board',
        transport.some(t => /AI move/.test(t)) && transport.some(t => /Play as/.test(t)), transport.join('|'));
    check('Watch AI play left the transport row', !transport.some(t => /Watch/.test(t)), transport.join('|'));
    const meta = await page.locator(`${PLAY} .ws-meta`).textContent();
    check('the opponent level stays in the meta row', /Master-like|Club|about|—|-/.test(meta), meta);
    check('Engine numbers, Coordinates and Board size left the meta row',
        !/Engine numbers|Eval|Coordinates|Coords|Board/.test(meta) && await page.locator(`${PLAY} .ws-meta .ws-board-size`).count() === 0, meta);
    check('New game stays above the board', await page.locator(`${PLAY} .ws-exit`, { hasText: 'New game' }).count() === 1);

    const SQ = s => page.locator(`${PLAY} [data-square="${s}"]`);
    const bubbles = () => page.locator(`${PLAY} .chat-message:not(.chat-message-pending)`);
    const coachBubbles = () => page.locator(`${PLAY} .chat-message-coach`);

    await SQ('e2').click(); await page.waitForTimeout(200); await SQ('e4').click();
    await page.waitForSelector(`${PLAY} .chat-message-pending`, { timeout: 5000 }).catch(() => {});
    check('the pending dots show in Chat while the coach chooses', await page.locator(`${PLAY} .chat-message-pending`).count() === 1);
    await page.waitForSelector(`${PLAY} .chat-message-coach`, { timeout: 40000 });
    await page.waitForTimeout(500);
    const first = await coachBubbles().first().textContent();
    check('the coach explains its move in Chat', (await coachBubbles().count()) === 1 && first.length > 20, first.slice(0, 80));
    check('the explanation leads with the move it is about', /^[A-Za-z][a-h1-8x+#=O\-]*\s/.test(first.trim()) , first.slice(0, 30));
    check('the pending dots are gone once it has moved', await page.locator(`${PLAY} .chat-message-pending`).count() === 0);
    await shot(page, 'play-coach-in-chat');

    // Follow-up in the same conversation.
    await page.locator(`${PLAY} .chat-input`).fill('why?');
    await page.locator(`${PLAY} .chat-send-btn`).click();
    await page.waitForSelector(`${PLAY} .chat-message-pending`, { timeout: 3000 }).catch(() => {});
    // The chat endpoint waits for the engine's evidence, and the engine is
    // busy grading the move just played - so the answer can take a while.
    await page.waitForSelector(`${PLAY} .chat-message-pending`, { state: 'detached', timeout: 90000 });
    await page.waitForTimeout(500);
    const texts = await bubbles().allTextContents();
    check('the question and the answer follow the explanation', texts.length === 3 && texts[1] === 'why?' && texts[2].length > 10, texts.map(t => t.slice(0, 30)).join(' | '));
    check('the explanation is still there after the reply', await coachBubbles().count() === 1);

    // Persist across tabs, moves and a reload.
    await page.locator(`${PLAY} .rail-icon-btn`, { hasText: 'Review' }).click();
    await page.waitForTimeout(300);
    await page.locator(`${PLAY} .rail-icon-btn`, { hasText: 'Chat' }).click();
    await page.waitForTimeout(300);
    check('switching tabs keeps the conversation', await bubbles().count() === 3);
    await SQ('d2').click(); await page.waitForTimeout(200); await SQ('d4').click();
    await page.waitForFunction(sel => document.querySelectorAll(sel).length >= 2, `${PLAY} .chat-message-coach`, { timeout: 40000 });
    await page.waitForTimeout(400);
    check('the next move appends rather than replaces', await bubbles().count() === 4 && await coachBubbles().count() === 2, String(await bubbles().count()));
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1800);
    check('a reload brings the whole conversation back, explanations included', await bubbles().count() === 4 && await coachBubbles().count() === 2, String(await bubbles().count()));
    check('the unread dot is not on Chat while Chat is open', await page.locator(`${PLAY} .rail-icon-btn.active .rail-unread-dot`).count() === 0);

    // Actions tab.
    await page.locator(`${PLAY} .rail-icon-btn`, { hasText: 'Actions' }).click();
    await page.waitForTimeout(300);
    await shot(page, 'play-actions');
    const actionsText = await page.locator(`${PLAY} .actions-list`).textContent();
    check('Actions holds Watch AI play, Engine numbers, Coordinates, Board size and Clear chat',
        /Watch AI play/.test(actionsText) && /Engine numbers/.test(actionsText) && /Coordinates/.test(actionsText)
        && /Clear chat/.test(actionsText) && await page.locator(`${PLAY} .actions-list .ws-board-size select`).count() === 1, actionsText.slice(0, 160));
    // Engine numbers from Actions still drives the eval strip.
    const evalOff = await page.locator(`${PLAY} .eval-bar`).evaluate(el => el.classList.contains('is-off'));
    await page.locator(`${PLAY} .actions-list .game-switch input`).first().click();
    await page.waitForTimeout(300);
    check('the Engine numbers switch in Actions still toggles the eval strip',
        (await page.locator(`${PLAY} .eval-bar`).evaluate(el => el.classList.contains('is-off'))) !== evalOff);
    await page.locator(`${PLAY} .actions-list .game-switch input`).first().click();
    await page.waitForTimeout(200);
    const coordsBefore = await page.locator(`${PLAY} [data-square="a1"]`).evaluate(el => el.parentElement?.parentElement?.textContent ?? '');
    const notation = () => page.locator(`${PLAY} .chess-board-wrapper`).evaluate(el => Array.from(el.querySelectorAll('[data-square] div')).some(d => /^[a-h1-8]$/.test(d.textContent?.trim() ?? '')));
    const wasOn = await notation();
    await page.locator(`${PLAY} .actions-list input[type=checkbox]`).nth(1).click();
    await page.waitForTimeout(300);
    check('the Coordinates switch in Actions still toggles the board labels', (await notation()) !== wasOn, `before=${wasOn}`);
    await page.locator(`${PLAY} .actions-list input[type=checkbox]`).nth(1).click();
    await page.locator(`${PLAY} .actions-clear-chat`).click();
    await page.waitForTimeout(600);
    await page.locator(`${PLAY} .rail-icon-btn`, { hasText: 'Chat' }).click();
    await page.waitForTimeout(300);
    check('Clear chat empties the conversation', await bubbles().count() === 0);
    const serverHistory = await page.evaluate(async () => (await (await fetch('/api/status')).json()).chat_history);
    check('and the server forgot it too', Array.isArray(serverHistory) && serverHistory.length === 0, JSON.stringify(serverHistory).slice(0, 80));
    check('the board was not touched by clearing the chat', await page.evaluate(async () => (await (await fetch('/api/status')).json()).status.move_count) === 4);

    // A new game clears the conversation.
    await SQ('g1').click(); await page.waitForTimeout(200); await SQ('f3').click();
    await page.waitForSelector(`${PLAY} .chat-message-coach`, { timeout: 40000 });
    await page.locator(`${PLAY} .ws-exit`, { hasText: 'New game' }).click();
    await page.waitForTimeout(1200);
    check('New game clears the conversation', await bubbles().count() === 0);
    check('New game shows the empty state again', await page.locator(`${PLAY} .chat-messages .empty-state, ${PLAY} .chat-messages [class*=empty]`).count() >= 1);

    // Watch AI play, from Actions, still works.
    await page.locator(`${PLAY} .rail-icon-btn`, { hasText: 'Actions' }).click();
    await page.waitForTimeout(200);
    await page.locator(`${PLAY} .ai-vs-ai-btn`).click();
    await page.waitForTimeout(800);
    const t2 = await page.locator(`${PLAY} .game-transport button`).allTextContents();
    check('Watch AI play from Actions starts AI-vs-AI (Pause appears under the board)', t2.some(t => /Pause/.test(t)), t2.join('|'));
    await page.locator(`${PLAY} .game-transport button`, { hasText: 'Pause' }).click();
    await page.waitForTimeout(400);
    await page.locator(`${PLAY} .game-transport button`, { hasText: /Exit|Stop|Leave/ }).first().click().catch(() => {});
    await page.waitForTimeout(600);

    check('no page errors in Play', errors.length === 0, errors.join(' | '));
    await page.close();
}

// --------------------------------------------------------------- Learn ---
{
    console.log('\n--- Learn ---');
    const errors = [];
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
    await page.addInitScript(() => { try { localStorage.setItem('chess-mode', 'sandbox'); localStorage.removeItem('sandbox-session'); localStorage.removeItem('sandbox-panel'); } catch {} });
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForSelector(`${LEARN} [data-square="e2"]`, { timeout: 20000 });
    await page.waitForTimeout(1200);

    const labels = await page.locator(`${LEARN} .sandbox-tab`).allTextContents();
    check('Learn has no Coach tab', !labels.includes('Coach'), labels.join(','));
    check('Learn has Chat, Line, Board, Actions', ['Chat', 'Line', 'Board', 'Actions'].every(l => labels.includes(l)), labels.join(','));
    const controls = await page.locator(`${LEARN} .sandbox-controls:not(.sandbox-controls-secondary) button`).allTextContents();
    check('AI move / Take over / Back / Forward stay under the board', controls.length === 4, controls.join('|'));
    const second = await page.locator(`${LEARN} .sandbox-controls-secondary button`).allTextContents();
    check('Reset board and View as Black are the second transport row, two buttons like Play\'s',
        second.length === 2 && /Reset/.test(second[0]) && /View as/.test(second[1]), second.join('|'));
    check('the opponent level is alone on the meta row', /Opponent level/.test(await page.locator(`${LEARN} .ws-meta`).textContent())
        && await page.locator(`${LEARN} .ws-meta select`).count() === 1);
    check('Eval bar, Coach my moves and board size left the board column',
        await page.locator(`${LEARN} .sandbox-board-column input[type=checkbox], ${LEARN} .sandbox-board-column .ws-board-size`).count() === 0);
    await page.locator(`${LEARN} .sandbox-controls-secondary button`, { hasText: 'View as' }).click();
    await page.waitForTimeout(300);
    check('View as Black from the new row still turns the board',
        /View as White/.test((await page.locator(`${LEARN} .sandbox-controls-secondary button`).allTextContents())[1]));
    await page.locator(`${LEARN} .sandbox-controls-secondary button`, { hasText: 'View as' }).click();

    await page.locator(`${LEARN} .sandbox-tab`, { hasText: 'Chat' }).click();
    const coach = () => page.locator(`${LEARN} .sandbox-chat-coach`);
    await page.locator(`${LEARN} .sandbox-controls button`, { hasText: 'AI move' }).click();
    await page.waitForSelector(`${LEARN} .sandbox-chat-coach`, { timeout: 40000 });
    await page.locator(`${LEARN} .sandbox-controls button`, { hasText: 'Stop' }).click().catch(() => {});
    await page.waitForTimeout(800);
    const n = await coach().count();
    check('an AI move in Learn lands in Chat as a coach message', n >= 1, String(n));
    const head = await coach().first().locator('.sandbox-ply-head').textContent();
    check('the message is headed by the move and whose it was', /1/.test(head) && /AI/.test(head), head);
    await page.waitForFunction(sel => Array.from(document.querySelectorAll(sel)).some(e => e.querySelector('.sandbox-ply-narration')), `${LEARN} .sandbox-chat-coach`, { timeout: 40000 }).catch(() => {});
    check('the narration arrives inside the same message', await page.locator(`${LEARN} .sandbox-chat-coach .sandbox-ply-narration`).count() >= 1);
    await shot(page, 'learn-coach-in-chat');

    await page.locator(`${LEARN} .sandbox-controls button`, { hasText: 'Back' }).click();
    await page.waitForTimeout(500);
    check('stepping back does not delete the coach message', await coach().count() === n);
    await page.locator(`${LEARN} .sandbox-controls button`, { hasText: 'Forward' }).click();
    await page.waitForTimeout(500);
    check('stepping forward does not repeat it', await coach().count() === n);

    await page.locator(`${LEARN} .sandbox-tab`, { hasText: 'Line' }).click();
    await page.waitForTimeout(200);
    await page.locator(`${LEARN} .sandbox-tab`, { hasText: 'Chat' }).click();
    await page.waitForTimeout(200);
    check('switching tabs keeps the Learn conversation', await coach().count() === n);

    await page.locator(`${LEARN} .sandbox-tab`, { hasText: 'Actions' }).click();
    await page.waitForTimeout(200);
    check('Learn Actions holds Clear chat', await page.locator(`${LEARN} .actions-clear-chat`).count() === 1);
    const learnActions = await page.locator(`${LEARN} .actions-list`).textContent();
    check('Learn Actions holds Eval bar, Coach my moves and Board size',
        /Eval bar/.test(learnActions) && /Coach my moves/.test(learnActions) && await page.locator(`${LEARN} .actions-list .ws-board-size select`).count() === 1, learnActions.slice(0, 120));
    const evalHidden = () => page.locator(`${LEARN} .eval-bar`).evaluate(el => getComputedStyle(el).visibility);
    const evBefore = await evalHidden();
    await page.locator(`${LEARN} .actions-list input[type=checkbox]`).first().click();
    await page.waitForTimeout(400);
    check('the Eval bar switch in Actions still shows the eval row', (await evalHidden()) !== evBefore, `${evBefore} -> ${await evalHidden()}`);
    await page.locator(`${LEARN} .actions-list input[type=checkbox]`).first().click();
    await page.waitForTimeout(200);
    await page.locator(`${LEARN} .actions-clear-chat`).click();
    await page.waitForTimeout(500);
    await page.locator(`${LEARN} .sandbox-tab`, { hasText: 'Chat' }).click();
    await page.waitForTimeout(200);
    check('Clear chat empties the Learn conversation', await coach().count() === 0 && await page.locator(`${LEARN} .sandbox-chat-msg`).count() === 0);
    check('the line is untouched by clearing the chat', await page.locator(`${LEARN} .sandbox-meta-line`).count() === 1);

    check('no page errors in Learn', errors.length === 0, errors.join(' | '));
    await page.close();
}

// -------------------------------------------------------------- Review ---
{
    console.log('\n--- Review ---');
    const errors = [];
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
    await page.addInitScript(() => { try { localStorage.setItem('chess-mode', 'postmortem'); } catch {} });
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    if (await page.locator('.pm-file-input').count() === 0) {
        await page.locator('.pm-identity button', { hasText: 'Close game' }).click();
        await page.waitForTimeout(900);
    }
    await page.setInputFiles('.pm-file-input', { name: 'opera.pgn', mimeType: 'application/x-chess-pgn', buffer: Buffer.from(OPERA) });
    await page.waitForSelector('.pm-board-column', { timeout: 30000 });
    await page.waitForTimeout(2000);

    const labels = await page.locator(`${PM} .pm-tab`).allTextContents();
    check('Review calls the conversation Chat', labels.includes('Chat') && !labels.includes('Coach'), labels.join(','));
    check('Review has an Actions tab', labels.includes('Actions'), labels.join(','));
    const controls = await page.locator(`${PM} .pm-controls button`).allTextContents();
    check('Back / Forward / Try a move stay under the board', controls.length >= 3, controls.join('|'));
    check('View as stays in the footer row, board size does not',
        /View as/.test(await page.locator(`${PM} .pm-footer-row`).textContent()) && await page.locator(`${PM} .pm-footer-row .ws-board-size`).count() === 0);

    await page.locator(`${PM} .pm-tab`, { hasText: 'Chat' }).click();
    await page.keyboard.press('ArrowRight'); await page.waitForTimeout(300);
    await page.locator(`${PM} .pm-chat-input`).fill('was that a good move?');
    await page.locator(`${PM} .pm-chat-send`).click();
    await page.waitForFunction(sel => document.querySelectorAll(sel).length >= 2, `${PM} .pm-chat-msg:not(.is-pending)`, { timeout: 40000 });
    await page.waitForTimeout(300);
    check('the Review coach answers in Chat', await page.locator(`${PM} .pm-chat-model:not(.is-pending)`).count() === 1);
    await page.locator(`${PM} .pm-tab`, { hasText: 'Moves' }).click(); await page.waitForTimeout(200);
    await page.locator(`${PM} .pm-tab`, { hasText: 'Chat' }).click(); await page.waitForTimeout(200);
    check('switching tabs keeps the Review conversation', await page.locator(`${PM} .pm-chat-msg`).count() === 2);
    await page.locator(`${PM} .pm-tab`, { hasText: 'Actions' }).click(); await page.waitForTimeout(200);
    await shot(page, 'review-actions');
    check('Review Actions holds Board size', await page.locator(`${PM} .actions-list .ws-board-size select`).count() === 1);
    await page.locator(`${PM} .actions-clear-chat`).click(); await page.waitForTimeout(500);
    await page.locator(`${PM} .pm-tab`, { hasText: 'Chat' }).click(); await page.waitForTimeout(200);
    check('Clear chat empties the Review conversation', await page.locator(`${PM} .pm-chat-msg`).count() === 0);
    check('the review stays open', await page.locator('.pm-board-column').count() === 1);

    check('no page errors in Review', errors.length === 0, errors.join(' | '));
    await page.close();
}

await browser.close();
console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);

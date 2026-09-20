/**
 * Guided Play, driven live: the toggle in Actions, its shortcut in Chat, the
 * "Watch out" block under the coach's explanation, and that none of it moves
 * the board or clips a control. Needs the backend on :8081 with
 * BETA_ACCESS_REQUIRED=false and a real Gemini key - the coaching text under
 * test is the model's.
 *
 *     node tools/verify/guided.mjs [http://localhost:3001] [--shots out/]
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

// The product rule, as a regex: attention prompts, never the answer.
const SPOONFEED = /\b(best move|you should play|the only (good )?move|play (the )?[a-h][1-8]|play [NBRQK][a-h]?[1-8]?x?[a-h][1-8]|I recommend|your move is)\b/i;

const browser = await chromium.launch();
const PLAY = '.chess-container';
const inView = async (page, loc) => loc.evaluate(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0
        && r.right <= window.innerWidth && r.bottom <= window.innerHeight;
});

for (const vp of [{ width: 1366, height: 768 }, { width: 1280, height: 720 }]) {
    console.log(`\n--- Play @ ${vp.width}x${vp.height} ---`);
    const errors = [];
    const page = await browser.newPage({ viewport: vp });
    page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
    // Runs on every navigation, including the reload below whose whole point
    // is that the preference survives - so the clearing happens once per tab.
    await page.addInitScript(() => { try {
        localStorage.setItem('chess-mode', 'game');
        localStorage.removeItem('chess-active-section');
        if (!sessionStorage.getItem('guided-probe')) {
            localStorage.removeItem('chess-guided-play');
            sessionStorage.setItem('guided-probe', '1');
        }
    } catch {} });
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    await page.evaluate(() => fetch('/api/reset', { method: 'POST' }));
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);

    const SQ = s => page.locator(`${PLAY} [data-square="${s}"]`);
    const coach = () => page.locator(`${PLAY} .chat-message-coach`);
    const watch = () => page.locator(`${PLAY} .chat-watchout`);
    const boardSize = () => page.locator(PLAY).evaluate(el => getComputedStyle(el).getPropertyValue('--board-size').trim());

    // Difficulty discoverability: the label says what the control is, and
    // the select is whole and on screen.
    const metaLabel = await page.locator(`${PLAY} .ws-meta .game-difficulty .ws-label-full`).textContent();
    check('the strength control is labelled "Opponent level"', /Opponent level/.test(metaLabel ?? ''), metaLabel ?? '');
    check('the opponent level select is fully on screen', await inView(page, page.locator(`${PLAY} .ws-meta .game-difficulty .level-picker-trigger`)));
    const sub = await page.locator(`${PLAY} .game-subtitle`).textContent();
    check('the subtitle names the opponent level', /Master-like|Expert|Advanced|Club|Improving|Casual|Beginner/.test(sub ?? '') && /about \d+/.test(sub ?? ''), sub ?? '');
    const strip = await page.locator(`${PLAY} .player-strip .player-sub`).allTextContents();
    check('the AI\'s player strip names the level with its Elo, not a number alone', strip.some(t => /\(~\d+\+?\)/.test(t)) && !strip.some(t => /difficulty|strength \d/.test(t)), strip.join('|'));

    // Chat carries the shortcut, and it starts Off.
    const hint = page.locator(`${PLAY} .chat-guided-hint`);
    check('Chat shows the Guided Play shortcut', await hint.count() === 1);
    check('it reads Off before anything is toggled', /Off/.test(await hint.textContent() ?? ''), await hint.textContent());
    check('the shortcut is on screen and does not crowd the input', await inView(page, hint));

    // Guided off: the explanation is what it always was.
    await SQ('e2').click(); await page.waitForTimeout(200); await SQ('e4').click();
    await page.waitForSelector(`${PLAY} .chat-message-coach`, { timeout: 45000 });
    await page.waitForTimeout(400);
    check('guided off: the coach explains its move', await coach().count() === 1);
    check('guided off: no Watch out block', await watch().count() === 0);
    await shot(page, `guided-off-${vp.width}`);

    // Toggle in Actions.
    const sizeBefore = await boardSize();
    await page.locator(`${PLAY} .rail-icon-btn`, { hasText: 'Actions' }).click();
    await page.waitForTimeout(300);
    const sw = page.locator(`${PLAY} .actions-list .guided-play-switch`);
    check('Actions holds the Guided Play switch', await sw.count() === 1);
    check('the switch is fully on screen', await inView(page, sw));
    check('it carries its subtitle', /After the AI moves, show what to watch for before your reply/.test(await sw.textContent() ?? ''), await sw.textContent());
    const overflow = await page.locator(`${PLAY} .actions-list`).evaluate(el => el.scrollWidth - el.clientWidth);
    check('the Actions list does not overflow sideways', overflow <= 0, String(overflow));
    await sw.locator('input').click();
    await page.waitForTimeout(300);
    check('the switch turns on', await sw.locator('input').isChecked());
    check('the preference is stored locally', await page.evaluate(() => localStorage.getItem('chess-guided-play')) === 'true');
    check('toggling moved nothing: the board is the same size', await boardSize() === sizeBefore, `${sizeBefore} -> ${await boardSize()}`);
    await shot(page, `guided-actions-${vp.width}`);

    await page.locator(`${PLAY} .rail-icon-btn`, { hasText: 'Chat' }).click();
    await page.waitForTimeout(300);
    check('the Chat shortcut now reads On', /On/.test(await hint.textContent() ?? ''), await hint.textContent());

    // Guided on: the next explanation carries a Watch out block.
    await SQ('d2').click(); await page.waitForTimeout(200); await SQ('d4').click();
    await page.waitForFunction(sel => document.querySelectorAll(sel).length >= 2, `${PLAY} .chat-message-coach`, { timeout: 45000 });
    await page.waitForTimeout(400);
    check('guided on: a Watch out block appears under the new explanation', await watch().count() === 1);
    const wo = (await watch().textContent()) ?? '';
    check('the block has real content', wo.replace(/Watch out/i, '').trim().length > 20, wo.slice(0, 120));
    check('it trains attention rather than naming the reply', !SPOONFEED.test(wo), wo.slice(0, 200));
    check('the first explanation was not rewritten', await coach().first().locator('.chat-watchout').count() === 0);
    check('the block does not run past the panel', await watch().evaluate(el => {
        const p = el.closest('.chat-messages'); const r = el.getBoundingClientRect(); const pr = p.getBoundingClientRect();
        return r.right <= pr.right + 1 && r.left >= pr.left - 1;
    }));
    await shot(page, `guided-on-${vp.width}`);

    // Reload: preference and block both survive.
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1800);
    check('a reload keeps Guided Play on', /On/.test(await hint.textContent() ?? ''));
    check('a reload brings the Watch out block back with the transcript', await watch().count() === 1);

    // Off again from the Chat shortcut.
    await hint.locator('button, input').first().click();
    await page.waitForTimeout(300);
    check('the Chat shortcut turns it off', /Off/.test(await hint.textContent() ?? '') && await page.evaluate(() => localStorage.getItem('chess-guided-play')) === 'false');

    check('no page errors', errors.length === 0, errors.join(' | '));
    await page.close();
}

await browser.close();
console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);

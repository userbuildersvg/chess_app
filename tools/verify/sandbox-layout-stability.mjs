/**
 * The Learn board does not shrink because text grew, in a real browser
 * against :3001.
 *
 *     node tools/verify/sandbox-layout-stability.mjs
 *
 * Contract: the board's bounding box changes by at most 4px after chat
 * replies, a practice intro, first-move grading or a chat-executed move.
 * Measured at 1366x768, 820x1180 and 390x844. Uses seed_profile.py for the
 * practice flow; no Gemini (chat instructions are model-free).
 */
import { execFileSync } from 'node:child_process';
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.env.BASE || 'http://localhost:3001';
const ROOT = new URL('../..', import.meta.url).pathname;
const TOL = 4;
let pass = 0, fail = 0;
const check = (label, cond, detail) => {
    if (cond) { pass++; console.log('PASS  ' + label); }
    else { fail++; console.log('FAIL  ' + label + (detail !== undefined ? ' - ' + JSON.stringify(detail).slice(0, 300) : '')); }
};
const box = async (page) => {
    const b = await page.locator('.sandbox-board-wrapper [data-square="a1"]').first().evaluate(el => {
        const board = el.closest('[data-boardid], .sandbox-board-wrapper');
        const r = (board ?? el).getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height) };
    });
    return b;
};
const stable = (label, before, after) =>
    check(`${label}: board ${before.w}x${before.h} -> ${after.w}x${after.h} (within ${TOL}px)`,
          Math.abs(before.w - after.w) <= TOL && Math.abs(before.h - after.h) <= TOL);
const noHorizontalOverflow = async (page, label) =>
    check(`${label}: no horizontal page overflow`, await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1));

const env = { ...process.env };
for (const line of execFileSync('bash', ['-c', `set -a; . ${ROOT}/.env; set +a; env`], { encoding: 'utf8' }).split('\n')) { const i = line.indexOf('='); if (i > 0) env[line.slice(0, i)] = line.slice(i + 1); }

const browser = await chromium.launch();
try {
    for (const vp of [{ width: 1366, height: 768 }, { width: 820, height: 1180 }, { width: 390, height: 844 }]) {
        console.log(`\n--- ${vp.width}x${vp.height} ---`);
        const ctx = await browser.newContext({ viewport: vp });
        const page = await ctx.newPage();
        const errors = [];
        page.on('pageerror', e => errors.push(String(e).slice(0, 200)));

        // 1. Plain Learn: a chat-executed move, then two more turns of text.
        await page.addInitScript(() => { try { if (sessionStorage.getItem('sls')) return; sessionStorage.setItem('sls', '1'); localStorage.setItem('chess-mode', 'sandbox'); localStorage.removeItem('sandbox-session'); localStorage.setItem('sandbox-panel', 'chat'); } catch { /* fine */ } });
        await page.goto(BASE, { waitUntil: 'networkidle' });
        await page.waitForSelector('.sandbox-board-wrapper [data-square="a1"]', { timeout: 20000 });
        await page.waitForTimeout(600);
        const b0 = await box(page);
        const say = async (text) => {
            const before = await page.locator('.sandbox-chat-model:not(.is-pending)').count();
            await page.fill('.sandbox-chat-input', text);
            await page.locator('form.sandbox-chat-row button[type="submit"]').click();
            await page.waitForFunction((n) => document.querySelectorAll('.sandbox-chat-model:not(.is-pending)').length > n, before, { timeout: 30000 });
            await page.waitForTimeout(400);
        };
        await say('Play the best move on the board.');
        stable('chat-executed best move', b0, await box(page));
        await say('Play Nf3');
        await say('knight to c6');
        await say('Play Nf3');
        stable('four chat turns later', b0, await box(page));
        await noHorizontalOverflow(page, 'Learn');

        // 2. Profile practice: intro, first-move grading, next-step line.
        const user = 'verify_sls_' + Math.floor(Math.random() * 1e6);
        await page.goto(BASE + '/signup', { waitUntil: 'networkidle' });
        await page.fill('#signup-user', user); await page.fill('#signup-email', `${user}@example.com`); await page.fill('#signup-pw', 'verify-password-12345');
        await page.click('button[type="submit"]'); await page.waitForURL(BASE + '/', { timeout: 15000 }); await page.waitForTimeout(800);
        execFileSync('/tmp/chessapp/bin/python', [`${ROOT}/tools/verify/seed_profile.py`, user, '10'], { env, encoding: 'utf8' });
        await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
        await page.waitForSelector('[data-testid="pf-practice"]', { timeout: 15000 });
        await page.locator('[data-testid="pf-practice"]').first().click();
        await page.waitForSelector('[data-testid="sandbox-practice"]', { timeout: 20000 });
        await page.waitForTimeout(800);
        const p0 = await box(page);
        // The intro is already there; a fresh Learn board at this viewport is the reference.
        check(`practice opens at the same board size as plain Learn (${b0.w} vs ${p0.w})`, Math.abs(b0.w - p0.w) <= TOL, { b0, p0 });
        await page.locator('.sandbox-board-wrapper [data-square="d2"]').click();
        await page.locator('.sandbox-board-wrapper [data-square="d4"]').click();
        await page.waitForSelector('[data-testid="sandbox-practice-result"]', { timeout: 20000 });
        await page.waitForTimeout(600);
        stable('first-move grading appears', p0, await box(page));
        await say('Play Nf3');
        stable('chat reply after grading', p0, await box(page));
        await noHorizontalOverflow(page, 'practice');
        check('board controls are not clipped', await page.locator('.sandbox-board-column button', { hasText: /Back/ }).first().isVisible());
        check('no page errors', errors.length === 0, errors);
        await ctx.close();
    }
} finally {
    await browser.close();
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

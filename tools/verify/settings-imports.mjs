/**
 * Account Settings, the imported-game library and SPA navigation, in a real
 * browser against :3001.
 *
 *     node tools/verify/settings-imports.mjs
 *
 * Seeds one imported game through seed_profile.py (no engine wait).
 *
 * Proves: the Settings sections exist in the intended order; the import
 * section carries the public-only / no-password and profile-effect lines;
 * an imported game row shows source, players, date, result and status with
 * Review first and the rest under More; View PGN opens a readable panel
 * with a close; Copy PGN gives feedback in place; Remove asks with the
 * game's name and the consequence and is worded apart from account
 * deletion; route and screen agree through the navigation loop Barry
 * flagged; nothing overflows at 390px; no console errors.
 */
import { execFileSync } from 'node:child_process';
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.env.BASE || 'http://localhost:3001';
const ROOT = new URL('../..', import.meta.url).pathname;
let pass = 0, fail = 0;
const check = (label, cond, detail) => {
    if (cond) { pass++; console.log('PASS  ' + label); }
    else { fail++; console.log('FAIL  ' + label + (detail !== undefined ? ' - ' + JSON.stringify(detail).slice(0, 300) : '')); }
};
const noOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

const env = { ...process.env };
for (const line of execFileSync('bash', ['-c', `set -a; . ${ROOT}/.env; set +a; env`], { encoding: 'utf8' }).split('\n')) { const i = line.indexOf('='); if (i > 0) env[line.slice(0, i)] = line.slice(i + 1); }

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error' && !/favicon|401|403/.test(m.text())) errors.push(m.text().slice(0, 200)); });

try {
    const user = 'verify_set_' + Math.floor(Math.random() * 1e6);
    await page.goto(BASE + '/signup', { waitUntil: 'networkidle' });
    await page.fill('#signup-user', user); await page.fill('#signup-email', `${user}@example.com`); await page.fill('#signup-pw', 'verify-password-12345');
    await page.click('button[type="submit"]'); await page.waitForURL(BASE + '/', { timeout: 15000 }); await page.waitForTimeout(800);
    execFileSync('/tmp/chessapp/bin/python', [`${ROOT}/tools/verify/seed_profile.py`, user, '2'], { env, encoding: 'utf8' });

    // --- Settings sections -------------------------------------------------
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-section="account"]', { timeout: 15000 });
    const order = await page.locator('[data-section]').evaluateAll(els => els.map(e => e.getAttribute('data-section')));
    check('Settings sections are ordered account → subscription → board → import → security → data → admin → danger',
          order.join(',') === 'account,subscription,board,import,security,data,admin,danger', order);
    check('the Account card holds sign out', await page.locator('[data-section="account"] button', { hasText: 'Sign out' }).count() === 1);
    check('import copy: public-only, no password', /only imports public games/.test(await page.locator('[data-testid="import-trust"]').innerText()) && /No chess-site password/.test(await page.locator('[data-testid="import-trust"]').innerText()));
    check('import copy: what imports do for the profile', /contribute to your/.test(await page.locator('[data-testid="import-effect"]').innerText()));
    check('deletion lives in the danger zone and is told apart from removing a game', /not one imported game/.test(await page.locator('[data-section="danger"]').innerText()));
    check('admin tools say nothing is there without an invite', /admin invite code/i.test(await page.locator('[data-section="admin"]').innerText()));
    check('data & privacy does not overclaim backups', /does not normally restore deleted accounts/.test(await page.locator('[data-section="data"]').innerText()) && !/every server backup/i.test(await page.locator('[data-section="data"]').innerText()));

    // --- an imported game row -----------------------------------------------
    const row = page.locator('[data-testid="imported-game"]').first();
    await row.scrollIntoViewIfNeeded();
    const rowText = await row.innerText();
    check('a row shows players, date, result, you-played and status', /vs/.test(rowText) && /2026/.test(rowText) && /(won|lost|drew|1-0|0-1)/.test(rowText) && /you played/.test(rowText) && /(analyzed|not analyzed|analysing)/.test(rowText), rowText.slice(0, 200));
    check('Review is the primary row action', await row.locator('button.acct-btn-primary', { hasText: 'Review this game' }).count() === 1);
    check('PGN is not shown until asked', await row.locator('[data-testid="imported-pgn"]').count() === 0);
    await row.locator('[data-testid="imported-more"]').click();
    check('More reveals View PGN, Copy PGN and Remove', await row.locator('button', { hasText: 'View PGN' }).count() === 1 && await row.locator('button', { hasText: 'Copy PGN' }).count() === 1 && await row.locator('button.ig-remove').count() === 1);
    await row.locator('button', { hasText: 'View PGN' }).click();
    await row.locator('[data-testid="imported-pgn"]').waitFor({ timeout: 10000 });
    check('View PGN opens a readable panel with the moves and a close', /1\. e4/.test(await row.locator('[data-testid="imported-pgn"] pre').innerText()) && await row.locator('button', { hasText: 'Close PGN' }).count() === 1);
    check('...without leaving the page', page.url().endsWith('/settings'));
    await row.locator('button', { hasText: 'Close PGN' }).click();
    check('Close hides it again', await row.locator('[data-testid="imported-pgn"]').count() === 0);
    await row.locator('button', { hasText: 'Copy PGN' }).click();
    await page.waitForTimeout(400);
    check('Copy PGN confirms in place', /Copied/.test(await row.innerText()));
    check('...and the clipboard holds the PGN', /1\. e4/.test(await page.evaluate(() => navigator.clipboard.readText()).catch(() => '')));
    await row.locator('button.ig-remove').click();
    const dialog = page.locator('[data-testid="confirm-dialog"]');
    await dialog.waitFor({ timeout: 5000 });
    const dtext = await dialog.innerText();
    check('Remove names the game and the consequence, apart from account deletion',
          /Remove this imported game\?/.test(dtext) && /vs/.test(dtext) && /no longer count toward your improvement profile/i.test(dtext) && /Your account and everything else stay/.test(dtext) && /cannot be undone/.test(dtext), dtext);
    await page.keyboard.press('Escape');
    check('Escape cancels and keeps the game', await dialog.count() === 0 && await page.locator('[data-testid="imported-game"]').count() === 2);

    // --- route / screen agreement ------------------------------------------
    const screen = async () => ({ url: new URL(page.url()).pathname, h1: (await page.locator('h1').first().innerText().catch(() => '')).trim(), mode: await page.evaluate(() => localStorage.getItem('chess-mode')) });
    await page.locator('a', { hasText: 'Back to the board' }).first().click();
    await page.waitForURL(BASE + '/');
    await page.waitForTimeout(500);
    check('Settings → Back to the board shows the board at /', (await screen()).url === '/' && await page.locator('.chess-board-wrapper, .sandbox-board-wrapper, .pm-board-column').count() >= 1);
    await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
    check('/profile shows the Improvement profile', /My improvement/i.test((await screen()).h1));
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
    check('/settings shows Account', /Account/.test((await screen()).h1));
    await page.goBack(); await page.waitForTimeout(500);
    check('browser back returns to /profile with its screen', (await screen()).url === '/profile' && /My improvement/i.test((await screen()).h1));
    await page.goForward(); await page.waitForTimeout(500);
    check('browser forward returns to /settings', (await screen()).url === '/settings');
    await page.locator('[data-section="account"] button', { hasText: 'Sign out' }).click();
    await page.waitForURL(BASE + '/', { timeout: 15000 }); await page.waitForTimeout(800);
    check('sign out lands on the board as a guest', (await (await page.request.get(BASE + '/api/auth/me')).json()).signed_in === false);
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' }); await page.waitForTimeout(600);
    check('a guest at /settings is sent to sign in, not shown a stale account page', page.url().includes('/signin'));

    // --- 390px ------------------------------------------------------------------
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BASE + '/signin', { waitUntil: 'networkidle' });
    await page.fill('#signin-id', user); await page.fill('#signin-pw', 'verify-password-12345'); await page.click('button[type="submit"]'); await page.waitForURL(BASE + '/');
    for (const path of ['/settings', '/profile']) {
        await page.goto(BASE + path, { waitUntil: 'networkidle' });
        await page.waitForSelector('.settings-card', { timeout: 15000 });
        check(`${path} at 390px has no horizontal overflow`, await noOverflow(page));
    }
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
    const r390 = page.locator('[data-testid="imported-game"]').first();
    await r390.locator('[data-testid="imported-more"]').click();
    await r390.locator('button', { hasText: 'View PGN' }).click();
    await r390.locator('[data-testid="imported-pgn"]').waitFor({ timeout: 10000 });
    check('the PGN panel at 390px does not overflow', await noOverflow(page));
    const head = page.locator('.settings-head').first();
    check('the page head fits at 390px', (await head.boundingBox())?.width <= 390);

    check('no console or page errors', errors.length === 0, errors);
} finally {
    await browser.close();
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

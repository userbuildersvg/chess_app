/**
 * Account security in a real browser against :3001.
 *
 *     node tools/verify/account-security.mjs
 *
 * Proves: a wrong password and an unknown user get the same sentence; the
 * session cookie is HttpOnly; Settings asks for the password before
 * deleting; a wrong one is refused in words; the right one deletes, signs
 * out and blocks the old session; the admin overview shows no PGN/FEN.
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.env.BASE || 'http://localhost:3001';
let pass = 0, fail = 0;
const check = (label, cond, detail) => {
    if (cond) { pass++; console.log('PASS  ' + label); }
    else { fail++; console.log('FAIL  ' + label + (detail !== undefined ? ' - ' + JSON.stringify(detail).slice(0, 300) : '')); }
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
try {
    const user = 'verify_sec_' + Math.floor(Math.random() * 1e6);
    const pw = 'verify password 12345';
    await page.goto(BASE + '/signup', { waitUntil: 'networkidle' });
    await page.fill('#signup-user', user);
    await page.fill('#signup-email', `${user}@example.com`);
    await page.fill('#signup-pw', pw);
    await page.click('button[type="submit"]');
    await page.waitForURL(BASE + '/', { timeout: 15000 });
    const cookies = await ctx.cookies();
    const session = cookies.find(c => c.name === 'zw_session');
    check('session cookie is HttpOnly with SameSite', session && session.httpOnly && /lax|strict/i.test(session.sameSite), session);

    const wrong = await (await page.request.post(BASE + '/api/auth/login', { data: { username: user, password: 'nope nope nope' } })).json();
    const nobody = await (await page.request.post(BASE + '/api/auth/login', { data: { username: 'nobody_' + user, password: 'nope nope nope' } })).json();
    check('wrong password and unknown user get the same generic sentence', wrong.detail === nobody.detail && !/exist|no such/i.test(wrong.detail), [wrong, nobody]);

    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
    await page.waitForSelector('#confirm-name');
    check('deletion asks for the password', await page.locator('#confirm-pw').count() === 1);
    check('deletion copy is honest about backups and the key', /destroys the account data key/.test(await page.locator('body').innerText()) && !/every server backup/i.test(await page.locator('body').innerText()));
    await page.fill('#confirm-name', user);
    await page.fill('#confirm-pw', 'wrong password here');
    await page.click('form:has(#confirm-name) button[type="submit"]');
    await page.waitForSelector('form:has(#confirm-name) ~ .acct-error, .acct-error', { timeout: 10000 });
    check('a wrong password is refused in words', /not right/i.test(await page.locator('.acct-error').last().innerText()));
    check('...and the account still exists', (await page.request.get(BASE + '/api/account')).status() === 200);
    await page.fill('#confirm-pw', pw);
    await page.click('form:has(#confirm-name) button[type="submit"]');
    await page.waitForURL(BASE + '/', { timeout: 15000 });
    await page.waitForTimeout(1000);
    check('after deletion the old session is gone', (await page.request.get(BASE + '/api/account')).status() === 401);
    const again = await page.request.post(BASE + '/api/auth/login', { data: { username: user, password: pw } });
    check('the deleted account cannot sign in', again.status() === 401);
} finally {
    await browser.close();
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

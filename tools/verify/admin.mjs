/**
 * The read-only admin panel at /admin, in a real browser against :3001.
 *
 *     node tools/verify/admin.mjs
 *
 * Needs the dev backend to carry ADMIN_EMAILS=admin@zugzwang.test. Pass one
 * unused dev invite code as ADMIN_INVITE_CODE to also drive the redemption
 * flow (it is consumed by the run). Creates
 * the throwaway admin account `admin_smoke` on first run (sign-in afterwards)
 * and one fresh normal account per run.
 *
 * Proves: a guest is blocked with a sign-in prompt, a normal account is
 * blocked with the permission message and sees no Settings link, the admin
 * sees the overview with the four tiles and the Settings link, and nothing on
 * the page or in the JSON is a PGN, FEN or secret.
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.env.BASE || 'http://localhost:3001';
let pass = 0, fail = 0;
const check = (label, cond, detail) => {
    if (cond) { pass++; console.log('PASS  ' + label); }
    else { fail++; console.log('FAIL  ' + label + (detail !== undefined ? ' - ' + JSON.stringify(detail).slice(0, 300) : '')); }
};

let user = '';
const ADMIN = { user: 'admin_smoke', email: 'admin@zugzwang.test', pw: 'admin-smoke-password-1' };
const browser = await chromium.launch();

async function fresh() {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    return { ctx, page };
}
async function signup(page, user, email, pw) {
    await page.goto(BASE + '/signup', { waitUntil: 'networkidle' });
    await page.fill('#signup-user', user);
    await page.fill('#signup-email', email);
    await page.fill('#signup-pw', pw);
    await page.click('button[type="submit"]');
    await page.waitForURL(BASE + '/', { timeout: 15000 });
}
async function signin(page, user, pw) {
    await page.goto(BASE + '/signin', { waitUntil: 'networkidle' });
    await page.fill('#signin-id', user);
    await page.fill('#signin-pw', pw);
    await page.click('button[type="submit"]');
    await page.waitForURL(BASE + '/', { timeout: 15000 });
}
const state = (page) => page.waitForSelector('[data-admin-state="blocked"], [data-admin-state="overview"]', { timeout: 15000 })
    .then((el) => el.getAttribute('data-admin-state'));

try {
    // --- guest ---------------------------------------------------------------
    {
        const { ctx, page } = await fresh();
        await page.goto(BASE + '/admin', { waitUntil: 'networkidle' });
        check('guest /admin is blocked', await state(page) === 'blocked');
        const text = await page.locator('.settings-shell').innerText();
        check('guest sees "Sign in required"', /sign in required/i.test(text), text);
        check('guest is offered the sign-in link', await page.locator('a[href="/signin"]').count() >= 1);
        const r = await page.request.get(BASE + '/api/admin/overview');
        check('guest API -> 401 account_required', r.status() === 401 && (await r.json()).error === 'account_required');
        await ctx.close();
    }

    // --- normal account ------------------------------------------------------
    {
        const { ctx, page } = await fresh();
        user = 'verify_adm_' + Math.floor(Math.random() * 1e6);
        await signup(page, user, `${user}@example.com`, 'verify-password-12345');
        await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
        await page.waitForSelector('.settings-card');
        check('normal account sees no Admin panel link in Settings', await page.locator('[data-testid="admin-link"]').count() === 0);
        await page.goto(BASE + '/admin', { waitUntil: 'networkidle' });
        check('normal account /admin is blocked', await state(page) === 'blocked');
        const text = await page.locator('.settings-shell').innerText();
        check('normal account sees the permission message', /do not have permission/i.test(text), text);
        const r = await page.request.get(BASE + '/api/admin/overview');
        check('normal account API -> 403 admin_required', r.status() === 403 && (await r.json()).error === 'admin_required');
        await ctx.close();
    }

    // --- become admin by invite code ------------------------------------------
    // Reads one dev code from ADMIN_INVITE_CODE (never from the repo); the
    // section is skipped, not failed, when it is not provided.
    if (process.env.ADMIN_INVITE_CODE) {
        const { ctx, page } = await fresh();
        const user = 'verify_inv_' + Math.floor(Math.random() * 1e6);
        await signup(page, user, `${user}@example.com`, 'verify-password-12345');
        await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
        await page.waitForSelector('.settings-card');
        const panel = page.locator('[data-testid="become-admin"]');
        check('signed-in non-admin sees the Become admin panel', await panel.count() === 1);
        check('the panel is folded by default', !(await panel.evaluate((el) => el.open)));
        await panel.locator('summary').click();
        await page.fill('#admin-invite', 'zz-admin-NOPE-NOPE-NOPE');
        await panel.locator('button[type="submit"]').click();
        await page.waitForSelector('[data-testid="become-admin"] .acct-error');
        check('an invalid code shows the clean shared error', /invalid or has already been used/i.test(await panel.locator('.acct-error').innerText()));
        check('the input is cleared after a failure', await page.inputValue('#admin-invite') === '');
        await page.fill('#admin-invite', process.env.ADMIN_INVITE_CODE);
        await panel.locator('button[type="submit"]').click();
        await page.waitForSelector('[data-testid="become-admin-ok"]', { timeout: 15000 });
        check('a valid code enables admin', /admin access enabled/i.test(await page.locator('[data-testid="become-admin-ok"]').innerText()));
        check('the panel folds away once admin', await panel.count() === 0);
        check('the Admin panel link appears without re-login', await page.locator('[data-testid="admin-link"]').count() === 1);
        await page.goto(BASE + '/admin', { waitUntil: 'networkidle' });
        check('/admin loads after redemption', await state(page) === 'overview');
        const body = await (await page.request.get(BASE + '/api/admin/overview')).text();
        check('the overview carries no code or hash', !body.includes(process.env.ADMIN_INVITE_CODE) && !/code_hash|[0-9a-f]{64}/.test(body));
        await ctx.close();
    } else {
        console.log('SKIP  become-admin flow (set ADMIN_INVITE_CODE to one unused dev code)');
    }

    // --- admin ---------------------------------------------------------------
    {
        const { ctx, page } = await fresh();
        const probe = await page.request.post(BASE + '/api/auth/login', { data: { username: ADMIN.user, password: ADMIN.pw } });
        if (probe.status() === 200) await signin(page, ADMIN.user, ADMIN.pw);
        else await signup(page, ADMIN.user, ADMIN.email, ADMIN.pw);
        await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
        await page.waitForSelector('.settings-card');
        const link = page.locator('[data-testid="admin-link"]');
        check('admin sees the Admin panel link in Settings', await link.count() === 1);
        await link.click();
        await page.waitForURL(BASE + '/admin');
        check('admin /admin renders the overview', await state(page) === 'overview');
        check('four metric tiles', await page.locator('.admin-tile').count() === 4);
        const text = await page.locator('.settings-shell').innerText();
        for (const h of ['Beta funnel', 'Recent accounts', 'Import health', 'AI / provider health',
                         'Review / analysis health', 'Improvement profile evidence',
                         'Corrections and learning loop', 'Failures', 'Recent failures']) {
            check(`section "${h}"`, await page.locator(`[data-admin-section="${h}"]`).count() === 1);
        }
        check('funnel has nine steps', await page.locator('.admin-funnel-step').count() === 9);
        const table = page.locator('[data-testid="recent-accounts"]');
        check('recent accounts table renders with 11 columns', await table.locator('th').count() === 11);
        // The account this run created a moment ago is always among the newest.
        check('recent accounts table lists the account created this run', (await table.innerText()).includes(`${user}@example.com`));
        check('untracked metrics display as "not tracked"', text.includes('not tracked'));
        check('evidence is worded as observed, not confirmed', /observed evidence/i.test(text) && !/confirmed weakness/i.test(text));
        check('no stack traces on the page', !/Traceback|at .*\.js:\d+/.test(text));
        check('no PGN on the page', !/\[Event |1\. e4/.test(text));
        check('no FEN on the page', !/KQkq|\/8\//.test(text));
        const r = await page.request.get(BASE + '/api/admin/overview');
        const body = await r.text();
        check('admin API -> 200', r.status() === 200);
        check('JSON carries no stack traces', !/Traceback/.test(body));
        check('JSON carries no pgn/fen/token/hash keys', !/"(pgn|fen[a-z_]*|token[a-z_]*|[a-z_]*hash|password[a-z_]*)"/.test(body), body.match(/"(pgn|fen[a-z_]*|token[a-z_]*|[a-z_]*hash|password[a-z_]*)"/)?.[0]);
        await page.screenshot({ path: '/tmp/admin-overview.png', fullPage: true });
        await ctx.close();
    }
} finally {
    await browser.close();
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

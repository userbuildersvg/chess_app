/**
 * Settings > Subscription, driven in a real browser against the dev stack.
 *
 *     node tools/verify/billing.mjs [http://localhost:3001]
 *
 * Signs up a throwaway account, opens Settings, asserts the Subscription
 * section renders the server's answer, clicks "See plans", and then reports
 * every CSP violation and console error the RevenueCat paywall produced.
 * The CSP list is the point: the shipping policy has to allow exactly what
 * the SDK needs and nothing more, and the only way to learn that list is
 * to run it.
 *
 * Needs VITE_REVENUECAT_PUBLIC_API_KEY in chess-frontend/.env; without it the
 * button is disabled and the paywall half is skipped (reported, not failed).
 */
import { execFileSync } from 'node:child_process';
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const SHOTS = '/tmp/claude-1000/-home-david111/9bd3506f-0b9e-4992-a3da-1b71abee95a3/scratchpad';
const ROOT = new URL('../..', import.meta.url).pathname.replace(/\/$/, '');
const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';
let passed = 0, failed = 0;
const check = (label, ok, detail) => {
    ok ? passed++ : failed++;
    console.log(`${ok ? '  ok ' : ' FAIL'} ${label}${ok || detail === undefined ? '' : '  <- ' + JSON.stringify(detail).slice(0, 300)}`);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
const csp = [], errors = [];
page.on('console', (m) => {
    const t = m.text();
    if (/Content Security Policy|Refused to/.test(t)) csp.push(t);
    else if (m.type() === 'error') errors.push(t);
});
page.on('pageerror', (e) => errors.push(String(e)));

try {
    const user = 'verify_bill_' + Math.floor(Math.random() * 1e6);
    await page.goto(BASE + '/signup', { waitUntil: 'networkidle' });
    await page.fill('#signup-user', user);
    await page.fill('#signup-email', `${user}@example.com`);
    await page.fill('#signup-pw', 'verify password 12345');
    await page.click('button[type="submit"]');
    await page.waitForURL(BASE + '/', { timeout: 15000 });

    const status = await (await page.request.get(BASE + '/api/billing/status')).json();
    check('signed-in status carries an app_user_id', /^zw-user-\d+$/.test(status.app_user_id), status);
    check('a fresh account is not Pro', status.pro === false, status);
    // The header (AccountMenu) is mounted by the app shell only; the account
    // pages carry their own chrome, so this is asked on "/" where we landed.
    check('header shows Upgrade for a Free account',
        await page.getByTestId('header-upgrade').waitFor({ timeout: 5000 }).then(() => true, () => false));

    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
    check('Subscription is in the section nav', await page.locator('.settings-nav a[href="#subscription"]').count() === 1);
    const plan = page.getByTestId('billing-plan');
    await plan.waitFor();
    const planText = await plan.textContent();
    check('a non-Pro plan row calmly reads Free', /Free/.test(planText), planText);
    check('an unverified answer is a quiet refresh warning, not the plan headline',
        status.verified
            ? await page.getByTestId('billing-refresh-warning').count() === 0
            : /Could not refresh subscription status just now/.test(await page.getByTestId('billing-refresh-warning').innerText()),
        { status, planText });

    check('Settings sells it as Zugzwang Pro with See plans', /Zugzwang Pro/.test(await page.locator('#subscription').innerText()));
    check('plan copy says "includes", never "remaining"', /Pro includes/.test(await page.getByTestId('billing-plans').innerText()) && !/remaining/i.test(await page.locator('#subscription').innerText()));
    check('Data & privacy and Danger zone are untouched by billing', await page.locator('#data').count() === 1 && await page.locator('#danger').count() === 1);

    // The way out of a stale or conflicted subscription state, by hand. It has
    // to work without a purchase, which is the only state this run can reach.
    const refreshBtn = page.getByTestId('billing-refresh');
    check('Settings offers a manual Refresh status', await refreshBtn.count() === 1);
    if (await refreshBtn.count()) {
        await refreshBtn.click();
        await page.waitForFunction(
            () => !/Checking/.test(document.querySelector('[data-testid="billing-refresh"]')?.textContent ?? ''),
            null, { timeout: 15000 }).catch(() => {});
        check('Refresh leaves the plan row on the server\'s answer, not an error',
            /^(Free|Pro)/.test((await page.getByTestId('billing-plan').textContent()) ?? ''),
            await page.getByTestId('billing-plan').textContent());
    }

    // --- Free on the Improvement Profile: card, and the one enforced gate ------
    const env = { ...process.env };
    for (const line of execFileSync('bash', ['-c', `set -a; . ${ROOT}/.env; set +a; env`], { encoding: 'utf8' }).split('\n')) {
        const i = line.indexOf('='); if (i > 0) env[line.slice(0, i)] = line.slice(i + 1);
    }
    const seeded = execFileSync('/tmp/chessapp/bin/python', [`${ROOT}/tools/verify/seed_profile.py`, user, '10', 'TACTICAL_OVERLOOK,ENDGAME_CONVERSION'], { env, encoding: 'utf8' }).trim().split(',');
    check('ten games seeded with two recurring themes', seeded.length === 10);
    await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
    await page.getByTestId('pf-pro-card').waitFor({ timeout: 15000 });
    check('Free profile shows the Pro card with Upgrade to Pro', await page.getByTestId('pf-upgrade').count() === 1 && /full recurring-pattern history/.test(await page.getByTestId('pf-pro-card').innerText()));
    await page.waitForSelector('.pf-finding', { timeout: 15000 });
    const openCards = await page.locator('.pf-finding:not(.pf-finding-locked)').count();
    const lockedCards = await page.locator('[data-testid="pf-locked"]').count();
    check('Free sees the strongest theme in full and the other as a locked preview', openCards === 1 && lockedCards === 1, { openCards, lockedCards });
    check('the locked preview says what to do and shows no evidence', /Upgrade to Pro for full recurring-pattern history/.test(await page.getByTestId('pf-locked').innerText()) && await page.locator('[data-testid="pf-locked"] [data-testid="pf-show-evidence"]').count() === 0);
    check('the visible theme still has Show evidence (Review buttons not hidden)', await page.locator('.pf-finding:not(.pf-finding-locked) [data-testid="pf-show-evidence"]').count() === 1);
    const prof = await (await page.request.get(BASE + '/api/profile')).json();
    check('/api/profile enforces it server-side: 1 finding, 1 locked, pro:false', prof.findings.length === 1 && prof.locked_findings.length === 1 && prof.pro === false);
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });

    const btn = page.getByTestId('billing-upgrade');
    check('upgrade button is present for a non-Pro account', await btn.count() === 1);
    if (await btn.isDisabled()) {
        console.log('  -- VITE_REVENUECAT_PUBLIC_API_KEY unset: paywall half skipped');
    } else {
        await btn.click();
        // Either the paywall mounts, or an error is shown in words. Both are
        // findings; a silent nothing is the failure.
        const outcome = await Promise.race([
            page.locator('[data-testid="billing-error"]').waitFor({ timeout: 20000 }).then(() => 'error'),
            page.locator('iframe, [class*="rcb"], [class*="paywall"], [id*="revenuecat"], [class*="rc-"]').first().waitFor({ timeout: 20000 }).then(() => 'paywall'),
        ]).catch(() => 'nothing');
        check('clicking See plans does something visible', outcome !== 'nothing', outcome);
        if (outcome === 'error') {
            const text = await page.getByTestId('billing-error').textContent();
            console.log('  error shown:', text);
            check('a failure to open is a named setup gap, not a generic network error', /Setup needed/.test(text), text);
        }
        await page.waitForTimeout(4000);
        await page.screenshot({ path: SHOTS + '/billing-paywall.png' });
        if (outcome === 'paywall') {
            const body = await page.locator('body').innerText();
            check('paywall lists monthly, yearly and lifetime', /monthly/i.test(body) && /yearly/i.test(body) && /lifetime/i.test(body));
            // Into the sandbox checkout. Selectors are the paywall's own, so
            // this half is best-effort: every step reports what it saw.
            await page.getByRole('button', { name: /^continue$/i }).first().click().catch(() => {});
            await page.waitForTimeout(6000);
            await page.screenshot({ path: SHOTS + '/billing-checkout.png' });
            const frames = page.frames().map(f => f.url()).filter(u => u && u !== 'about:blank');
            console.log('  frames after Continue:', frames.map(u => new URL(u).host));
            check('a checkout surface appeared (iframe or checkout form)', frames.length > 1 || /card number|email/i.test(await page.locator('body').innerText()));
            // Sandbox email + Stripe test card, if the form is on the page.
            const email = page.locator('input[type="email"]').first();
            if (await email.count()) { await email.fill('verify-billing@example.com'); await page.keyboard.press('Enter'); await page.waitForTimeout(5000); }
            let filled = false;
            for (const f of page.frames()) {
                const num = f.locator('input[name="cardnumber"], input[name="number"], [placeholder*="1234"]').first();
                if (await num.count()) {
                    await num.fill('4242424242424242');
                    await f.locator('input[name="exp-date"], input[name="expiry"], [placeholder*="MM"]').first().fill('12/34').catch(() => {});
                    await f.locator('input[name="cvc"], [placeholder*="CVC"]').first().fill('123').catch(() => {});
                    await f.locator('input[name="postal"], input[name="postalCode"], [placeholder*="ZIP"], [placeholder*="Postal"]').first().fill('12345').catch(() => {});
                    filled = true; break;
                }
            }
            console.log('  test card filled:', filled);
            if (filled) {
                await page.getByRole('button', { name: /pay|subscribe|complete|confirm/i }).first().click().catch(() => {});
                await page.waitForTimeout(12000);
                await page.screenshot({ path: SHOTS + '/billing-after-pay.png' });
                check('checkout reports Payment complete', /payment complete/i.test(await page.locator('body').innerText()));
                // The closing Continue resolves presentPaywall(); the component
                // then asks the server with fresh=1 and shows "Welcome to Pro".
                await page.getByRole('button', { name: /^continue$/i }).last().click().catch(() => {});
                const ok = await page.getByTestId('billing-ok').waitFor({ timeout: 20000 }).then(() => true, () => false);
                check('the app refreshed and shows Welcome to Pro', ok);
                // "Welcome to Pro" is set before the fresh status lands; give it a moment.
                const proRow = await page.waitForFunction(() => /^Pro/.test(document.querySelector('[data-testid="billing-plan"]')?.textContent ?? ''), null, { timeout: 15000 }).then(() => true, () => false);
                check('plan row now reads Pro', proRow, await page.getByTestId('billing-plan').textContent());
                const manage = await page.getByText('Manage subscription').waitFor({ timeout: 15000 }).then(() => true, () => false);
                check('a Manage subscription link appeared', manage);
                check('Settings reads Pro active', /Pro active/.test(await page.getByTestId('billing-plan').textContent()));
                // --- Pro on the Improvement Profile: everything, nothing locked ---
                await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
                await page.getByTestId('pf-pro-active').waitFor({ timeout: 15000 });
                check('Pro profile says "Pro active - full profile history enabled"', /full profile history enabled/.test(await page.getByTestId('pf-pro-active').innerText()));
                await page.waitForSelector('.pf-finding', { timeout: 15000 });
                check('Pro sees both themes in full, no locked cards', await page.locator('.pf-finding:not(.pf-finding-locked)').count() === 2 && await page.locator('[data-testid="pf-locked"]').count() === 0);
                check('header no longer shows Upgrade', await page.getByTestId('header-upgrade').count() === 0);
                const profPro = await (await page.request.get(BASE + '/api/profile')).json();
                check('/api/profile: 2 findings, 0 locked, pro:true', profPro.findings.length === 2 && profPro.locked_findings.length === 0 && profPro.pro === true);
                await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
            }
            const after = await (await page.request.get(BASE + '/api/billing/status?fresh=1')).json();
            console.log('  status after checkout:', JSON.stringify(after));
            check('post-purchase refresh reached the server (verified answer)', after.verified === true, after);
            if (after.pro) check('server confirms Pro via RevenueCat REST', after.pro === true && !!after.product, after);
            else console.log('  -- purchase did not complete in this run; Pro not asserted');
            await page.waitForTimeout(2000);
            await page.screenshot({ path: SHOTS + '/billing-final.png' });
        }
    }
} finally {
    await browser.close();
}
console.log('\nCSP violations (' + csp.length + '):');
for (const c of [...new Set(csp)]) console.log('  ', c.slice(0, 400));
console.log('console errors (' + errors.length + '):');
for (const e of [...new Set(errors)]) console.log('  ', e.slice(0, 300));
console.log(`\n${passed}/${passed + failed} checks passed`);
process.exit(failed ? 1 : 0);

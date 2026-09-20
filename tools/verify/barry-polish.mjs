/**
 * The Barry audit polish pass, in a real browser against :3001 (CLAUDE.md §42).
 *
 *     node tools/verify/barry-polish.mjs
 *
 * One signed-up account, one Scholar's-mate review. Proves: the Report's
 * "biggest learning opportunity" sits above the eval curve and its CTA opens
 * Correct on that move; a signed-in correction says "Saved to your account"
 * and its intent zone reads "Your stated intention"; a stubbed database
 * outage yields "Could not save — retry" with a Retry that saves; the
 * reserved status band collapses once the card is up; the importer answers
 * every lookup under the form (Looking up… / Found N / No public games /
 * error with Retry / Already imported); Settings has a sticky section nav
 * that reaches Imports and Privacy with no overflow at 390px; the Profile
 * shows N/10 toward the first pattern as a bar; the Privacy page no longer
 * contradicts Settings about corrections.
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.env.BASE || 'http://localhost:3001';
let pass = 0, fail = 0;
const check = (label, cond, detail) => {
    if (cond) { pass++; console.log('PASS  ' + label); }
    else { fail++; console.log('FAIL  ' + label + (detail !== undefined ? ' - ' + JSON.stringify(detail).slice(0, 300) : '')); }
};
const PGN = `[Event "Casual"]\n[White "Them"]\n[Black "You"]\n[Result "1-0"]\n\n1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0\n`;
const game = (i) => ({ external_id: `g${i}`, white: 'alice', black: 'bob', result: '1-0', date: '2026.09.01', time_control: '600', rated: true, opening: 'Ruy Lopez', move_count: 8, supported: true, variant: 'standard', source: 'chesscom', pgn: '' });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));

try {
    // --- sign up ------------------------------------------------------------
    const user = 'verify_bp_' + Math.floor(Math.random() * 1e6);
    await page.goto(BASE + '/signup', { waitUntil: 'networkidle' });
    await page.fill('#signup-user', user);
    await page.fill('#signup-email', `${user}@example.com`);
    await page.fill('#signup-pw', 'verify-password-12345');
    await page.click('button[type="submit"]');
    await page.waitForURL(BASE + '/', { timeout: 15000 });
    await page.waitForSelector('[data-testid="import-prompt"]', { timeout: 15000 });

    // --- importer feedback, under the form, every state -----------------------
    const prompt = page.locator('[data-testid="import-prompt"]');
    let mode = 'found';
    await page.route('**/api/profile/external/search', async route => {
        const body = route.request().postDataJSON();
        if (mode === 'down') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'provider_unavailable', message: 'That site is temporarily unavailable.' }) });
        if (mode === 'empty') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, source: body.source, username: body.username, games: [], note: '' }) });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, source: body.source, username: body.username, games: [game(0), game(1)], note: '' }) });
    });
    await page.route('**/api/profile/external/import', async route => {
        const body = route.request().postDataJSON();
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, source: body.source, username: body.username, imported: [], duplicates: [{ external_id: 'g0', name: 'a' }, { external_id: 'g1', name: 'b' }], skipped: [], imported_count: 0, duplicate_count: 2, skipped_count: 0, message: 'Imported 0 games, 2 games already in your library', progress: {} }) });
    });
    const status = () => prompt.locator('[data-testid="xi-status"]').innerText().catch(() => '');
    const waitStatus = (re) => page.waitForFunction(src => new RegExp(src).test(document.querySelector('[data-testid="import-prompt"] [data-testid="xi-status"]')?.textContent ?? ''), re.source, { timeout: 15000 });
    await prompt.locator('#xi-username').fill('alice');
    await prompt.locator('button[type="submit"]').click();
    await waitStatus(/Found 2 games/);
    check('a Chess.com lookup says how many it found, under the form', /Found 2 games/.test(await status()));
    const formBottom = await prompt.locator('form.xi-form').evaluate(el => el.getBoundingClientRect().bottom);
    const statusTop = await prompt.locator('[data-testid="xi-status"]').evaluate(el => el.getBoundingClientRect().top);
    check('the status line is directly under the form', statusTop >= formBottom - 1 && statusTop - formBottom < 60, { formBottom, statusTop });
    mode = 'empty';
    await prompt.locator('.xi-source', { hasText: 'Lichess' }).click();
    await prompt.locator('#xi-username').fill('ghost');
    await prompt.locator('button[type="submit"]').click();
    await waitStatus(/No public games found/);
    check('an empty Lichess result says so and explains public-only', /No public games found.*Only public games/.test(await status()), await status());
    mode = 'down';
    await prompt.locator('button[type="submit"]').click();
    await page.waitForSelector('[data-testid="import-prompt"] [data-testid="xi-status"] [role="alert"]', { timeout: 15000 });
    check('a provider failure is a visible error with a Retry', /temporarily unavailable/.test(await status()) && await prompt.locator('[data-testid="xi-status"] .xi-retry').count() === 1, await status());
    mode = 'found';
    await prompt.locator('[data-testid="xi-status"] .xi-retry').click();
    await waitStatus(/Found 2 games/);
    check('Retry re-runs the lookup', /Found 2 games/.test(await status()));
    await prompt.locator('button', { hasText: /^Import 2 games/ }).click();
    await page.waitForSelector('[data-testid="import-prompt"] .xi-actions a[href*="/settings"]', { timeout: 15000 });
    const doneText = await prompt.innerText();
    check('a re-import of known games reads as Already imported, not as a failure', /Already imported/.test(doneText) && !/Games imported/.test(doneText) && await prompt.locator('[role="alert"]').count() === 0, doneText.slice(0, 200));
    await page.unroute('**/api/profile/external/search');
    await page.unroute('**/api/profile/external/import');
    await prompt.locator('button', { hasText: 'Back to the board' }).click();
    await page.waitForTimeout(500);

    // --- Review: lesson first --------------------------------------------------
    await page.evaluate(() => { localStorage.setItem('chess-mode', 'postmortem'); localStorage.removeItem('postmortem-game'); localStorage.setItem('postmortem-panel', 'report'); });
    await page.reload({ waitUntil: 'networkidle' });
    await page.setInputFiles('.pm-file-input', { name: 'scholars.pgn', mimeType: 'application/x-chess-pgn', buffer: Buffer.from(PGN) });
    await page.waitForSelector('.pm-board-column', { timeout: 20000 });
    await page.waitForSelector('[data-testid="pm-key"]', { timeout: 90000 });
    const keyTop = await page.locator('[data-testid="pm-key"]').evaluate(el => el.getBoundingClientRect().top);
    const curveTop = await page.locator('.pm-curve, .pm-eval-curve, svg.pm-curve-svg').first().evaluate(el => el.getBoundingClientRect().top).catch(() => Infinity);
    check('the learning-opportunity card is the first section of the Report, above the curve', keyTop < curveTop, { keyTop, curveTop });
    const keyText = await page.locator('[data-testid="pm-key"]').innerText();
    check('it names the move, the side, the grade and the engine move', /3\.\.\. Nf6/.test(keyText) && /You were Black|Black/.test(keyText) && /Blunder/.test(keyText) && /engine preferred/.test(keyText), keyText.slice(0, 200));
    check('it says what the move did to the position', /from .* to .*losing/.test(keyText), keyText.slice(0, 300));
    await page.locator('[data-testid="pm-key-cta"]').click();
    await page.waitForSelector('.pm .corr-panel', { timeout: 10000 });
    check('Work through this decision opens Correct on that move', /Nf6/.test(await page.locator('.pm .corr-panel .corr-move').innerText()));

    // --- Correction Card: saved, natural copy, no blank band --------------------
    const corr = page.locator('.pm .corr-panel');
    await corr.locator('.corr-chip', { hasText: "I wasn't sure" }).click();
    await corr.locator('.corr-primary').click();
    await page.waitForSelector('.pm .corr-card', { timeout: 60000 });
    check('a signed-in correction says Saved — Zugzwang will remember this', (await corr.locator('[data-testid="corr-saved-chip"]').innerText()) === 'Saved — Zugzwang will remember this.');
    const intentZone = await corr.locator('[data-testid="corr-zone-intent"]').innerText();
    check('the intent zone reads naturally with "I wasn\'t sure"', /What you were trying to do\s+I wasn.t sure/i.test(intentZone), intentZone);
    const band = await corr.locator('.corr-progress').evaluate(el => el.getBoundingClientRect().height);
    check('the reserved status band is collapsed once the card is up', band < 2, band);
    const gap = await page.evaluate(() => {
        const head = document.querySelector('.pm .corr-head').getBoundingClientRect();
        const card = document.querySelector('.pm .corr-card').getBoundingClientRect();
        return card.top - head.bottom;
    });
    check('only a normal section gap sits between the header and the card (<= 40px)', gap <= 40, gap);
    check('the card title is inside the visible panel', await corr.locator('.corr-card').evaluate(el => {
        let sc = el.parentElement;
        while (sc && sc !== document.documentElement && !/(auto|scroll)/.test(getComputedStyle(sc).overflowY)) sc = sc.parentElement;
        const r = el.getBoundingClientRect(), p = (sc ?? document.documentElement).getBoundingClientRect();
        return r.top >= p.top - 1 && r.top < p.bottom;
    }));
    const unavailable = corr.locator('[data-testid="corr-practice-unavailable"]');
    if (await unavailable.count()) {
        const t = await unavailable.innerText();
        check('practice unavailable is an honest continuation with a reason and a next step', /couldn.t create a clean fresh test/.test(t) && /saved|Retry|session/.test(t), t.slice(0, 300));
    } else {
        check('practice is offered (an engine-verified position exists)', await corr.locator('[data-testid="corr-practice-available"]').count() === 1);
    }

    // simulated save failure on a fresh decision: the card comes back and says so
    await page.route('**/api/learning-loop/diagnose', async route => {
        const response = await route.fetch();
        const body = await response.json();
        if (body?.correction) { body.correction.saved_to_account = false; body.correction.save_failed = true; }
        await route.fulfill({ response, json: body });
    });
    await page.locator('.pm [role=tab]', { hasText: 'Moves' }).click();
    await page.locator('.pm-move', { hasText: 'Nc6' }).click();
    await page.waitForTimeout(500);
    await page.locator('.pm [role=tab]', { hasText: 'Correct' }).click();
    await corr.locator('.corr-chip', { hasText: 'Improve a piece' }).click();
    await corr.locator('.corr-primary').click();
    await page.waitForSelector('.pm [data-testid="corr-save-retry"]', { timeout: 60000 });
    check('a failed save reads Could not save — retry, never Session only or Saved', (await corr.locator('[data-testid="corr-saved-chip"]').innerText()) === 'Could not save — retry');
    check('the fine print says why and what to do', /could not be reached.*Retry/.test(await corr.locator('[data-testid="correction-storage-copy"]').innerText()));
    await page.unroute('**/api/learning-loop/diagnose');
    await corr.locator('[data-testid="corr-save-retry"]').click();
    await page.waitForFunction(() => document.querySelector('[data-testid="corr-saved-chip"]')?.textContent === 'Saved — Zugzwang will remember this.', null, { timeout: 60000 });
    check('Retry saves it to the account', true);

    // --- Settings: section nav, reachable Imports and Privacy, no overflow ----
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-testid="settings-nav"]', { timeout: 15000 });
    const nav = page.locator('[data-testid="settings-nav"]');
    check('the nav lists the seven sections in order', (await nav.locator('a').allInnerTexts()).join('|').replace(/\s+/g, ' ') .includes('Account|Subscription|Board & coaching|Import games|Security|Data & privacy'));
    check('Danger zone is last and marked', /Danger zone$/.test((await nav.locator('a').allInnerTexts()).join('|')) && await nav.locator('a.is-danger').count() === 1);
    await page.waitForSelector('#import-games', { timeout: 15000 });
    await nav.locator('a', { hasText: 'Import games' }).click();
    await page.waitForTimeout(1200);
    const importTop = await page.locator('#import-games').evaluate(el => el.getBoundingClientRect().top);
    check('Import games is one tap from the top', importTop >= 0 && importTop < 120, importTop);
    await nav.locator('a', { hasText: 'Data & privacy' }).click();
    await page.waitForTimeout(1200);
    const dataTop = await page.locator('#data').evaluate(el => el.getBoundingClientRect().top);
    check('Data & privacy is one tap from the top', dataTop >= 0 && dataTop < 120, dataTop);
    check('the nav is still visible after jumping (sticky)', await nav.evaluate(el => el.getBoundingClientRect().top >= 0 && el.getBoundingClientRect().bottom <= 80));
    check('Danger zone is visually isolated', await page.locator('#danger.settings-danger').count() === 1);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(400);
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check('no horizontal overflow at 390px with the nav', over <= 0, over);
    await page.setViewportSize({ width: 1280, height: 900 });

    // --- Privacy page agrees with Settings ------------------------------------
    await page.goto(BASE + '/privacy', { waitUntil: 'networkidle' });
    const priv = await page.innerText('body');
    check('Privacy says signed-in corrections are saved to the account', /Correction cards you make while signed in.*saved to your account/s.test(priv));
    check('Privacy no longer says corrections live in memory only', !/corrections and\s+practice from the learning loop live in memory only/.test(priv));
    check('Privacy still says guest work is session only', /As a guest they are session only/.test(priv));

    // --- Profile: exact progress toward the threshold -------------------------
    await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-testid="pf-progress"]', { timeout: 15000 });
    const prog = await page.locator('[data-testid="pf-progress"]').innerText();
    check('the pre-threshold profile reads N/10 analysed games toward your first recurring pattern', /\d+\/10 analysed games toward your first recurring pattern/.test(prog), prog.slice(0, 120));
    check('with a progress bar', await page.locator('[data-testid="pf-threshold-bar"][role=progressbar]').count() === 1);
    check('and an explanation of why it waits, plus an import CTA', /waits for enough evidence/.test(prog) && /Import recent games/.test(prog));
    check('no recurring pattern is claimed before the threshold', !/Your recurring weaknesses/.test(await page.innerText('body')));

    check('no page errors', errors.length === 0, errors);
} finally {
    await browser.close();
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

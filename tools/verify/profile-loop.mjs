/**
 * The profile -> evidence -> practice loop, in a real browser against :3001.
 *
 *     node tools/verify/profile-loop.mjs
 *
 * Needs the dev backend (ACCOUNTS_ENABLED=true) and the venv at /tmp/chessapp:
 * the account's analysed games are seeded by tools/verify/seed_profile.py
 * rather than waiting for the engine.
 *
 * Proves: evidence rows name their exact source game and carry no FEN/PGN;
 * Remove asks "Are you sure?" and cancel keeps the game; confirming removes it
 * and the profile counts update; "Practice this" opens Learn on the position
 * with the brief; the first move is graded and the engine's move revealed;
 * "Review game" opens Review.
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

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));

try {
    const user = 'verify_loop_' + Math.floor(Math.random() * 1e6);
    await page.goto(BASE + '/signup', { waitUntil: 'networkidle' });
    await page.fill('#signup-user', user);
    await page.fill('#signup-email', `${user}@example.com`);
    await page.fill('#signup-pw', 'verify-password-12345');
    await page.click('button[type="submit"]');
    await page.waitForURL(BASE + '/', { timeout: 15000 });
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(1500);

    // Seed 11 analysed games with findings through the same service the worker uses.
    const env = { ...process.env };
    for (const line of execFileSync('bash', ['-c', `set -a; . ${ROOT}/.env; set +a; env`], { encoding: 'utf8' }).split('\n')) {
        const i = line.indexOf('='); if (i > 0) env[line.slice(0, i)] = line.slice(i + 1);
    }
    const ids = execFileSync('/tmp/chessapp/bin/python', [`${ROOT}/tools/verify/seed_profile.py`, user, '11'], { env, encoding: 'utf8' }).trim().split(',');
    check('eleven games seeded (ten is the profile threshold; one will be deleted)', ids.length === 11, ids);

    // --- evidence labels ----------------------------------------------------
    await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
    await page.waitForSelector('.pf-finding', { timeout: 15000 });
    const card = page.locator('.pf-finding').first();
    const text = await card.innerText();
    check('a finding card renders', text.includes('concrete tactics'));
    check('first seen / most recent name the exact game', /First seen\s+(Chess\.com|Lichess) · you as White vs opp\d+ · (Blitz 5\+0|Rapid 10\+5) · 1-0 · Sep 1\d, 2026 · game #\d+/i.test(text), text.slice(0, 600));
    const examples = card.locator('[data-testid="pf-example"]');
    check('three evidence examples', await examples.count() === 3);
    const ex = await examples.first().innerText();
    check('an example shows move, engine move, phase, swing and the game label', /3\. Bb5/.test(ex) && /engine preferred d4/.test(ex) && /opening/.test(ex) && /lost 1\.\d pawns/.test(ex) && /game #\d+/.test(ex), ex);
    check('an example offers Review game', await examples.first().locator('button', { hasText: 'Review game' }).count() === 1);
    check('no FEN or PGN on the profile', !/KQkq|\[Event/.test(await page.locator('body').innerText()));
    check('Practice this is offered', await card.locator('[data-testid="pf-practice"]').count() === 1);

    // --- safe deletion ------------------------------------------------------
    const before = await page.locator('.pf-finding').first().innerText();
    const evidenceBefore = Number(/Evidence\s+(\d+) moves/i.exec(before)?.[1]);
    const removeBtn = page.locator('.pf-remove').first();
    await removeBtn.click();
    const dialog = page.locator('[data-testid="confirm-dialog"]');
    check('Remove asks "Are you sure?"', await dialog.count() === 1 && /Are you sure\?/.test(await dialog.innerText()));
    check('...with the consequence and "cannot be undone"', /no longer count toward your improvement profile/.test(await dialog.innerText()) && /cannot be undone/.test(await dialog.innerText()));
    await dialog.locator('button', { hasText: 'Cancel' }).click();
    check('Cancel closes the dialog and keeps the game', await dialog.count() === 0 && await page.locator('.pf-remove').count() === 11);
    await removeBtn.click();
    await dialog.locator('[data-testid="confirm-yes"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.pf-remove').length === 10, null, { timeout: 15000 });
    check('Delete game removes exactly one game', await page.locator('.pf-remove').count() === 10);
    await page.waitForTimeout(1500);
    const after = await page.locator('.pf-finding').first().innerText();
    const evidenceAfter = Number(/Evidence\s+(\d+) moves/i.exec(after)?.[1]);
    check('the profile evidence count dropped by one', evidenceAfter === evidenceBefore - 1, { evidenceBefore, evidenceAfter, after: after.slice(0, 300) });
    const mistakes = await (await page.request.get(BASE + '/api/profile/mistakes')).json();
    check('/api/profile/mistakes agrees: 10 analysed, one theme', mistakes.analysed_games === 10 && mistakes.themes.length === 1 && mistakes.themes[0].evidence_count === evidenceBefore - 1, mistakes.themes?.[0]?.evidence_count);

    // --- practice unavailable is said next to the button, on the profile ------
    await page.route('**/api/profile/mistakes/*/practice', async route => {
        await route.fulfill({ status: 200, json: { ok: true, available: false, reason: 'Practice is not available for this theme yet because this evidence is missing the position snapshot.', theme: 'x' } });
    });
    await page.locator('[data-testid="pf-practice"]').first().click();
    await page.waitForSelector('[data-testid="pf-practice-note"]', { timeout: 10000 });
    check('an unavailable answer stays on the profile with the reason beside the button',
          page.url().includes('/profile') && /missing the position snapshot/.test(await page.locator('[data-testid="pf-practice-note"]').innerText()));
    check('...and the button is usable again', await page.locator('[data-testid="pf-practice"]').first().isEnabled());
    await page.unroute('**/api/profile/mistakes/*/practice');

    // --- practice -----------------------------------------------------------
    await page.locator('[data-testid="pf-practice"]').first().click();
    await page.waitForURL(BASE + '/', { timeout: 15000 });
    await page.waitForSelector('[data-testid="sandbox-practice"]', { timeout: 20000 });
    const brief = await page.locator('[data-testid="sandbox-practice"]').innerText();
    check('Learn opens with the practice brief', /This position comes from one of your games/.test(brief) && /Find the move/.test(brief), brief);
    check('the brief names the source game and the move played', /(Chess\.com|Lichess) · you as White vs opp\d+/.test(brief) && /Move 3: you played Bb5/.test(brief), brief);
    check('the engine move is withheld before the attempt', !/engine preferred/.test(brief));
    check('the URL was cleaned after the handoff', !page.url().includes('practice='), page.url());
    check('the Chat tab is open', await page.locator('.sandbox-tabs [role=tab][aria-selected="true"]', { hasText: 'Chat' }).count() === 1);
    const intro = await page.locator('.sandbox-chat-model').first().innerText();
    check('the coach opens with a practice intro naming the theme and the source game',
          /train the pattern your games keep showing/.test(intro) && /one of your own games/.test(intro) && /(Chess\.com|Lichess) · you as White/.test(intro), intro);
    check('...saying what to check and what to do, without the engine move', /What to check first/.test(intro) && /Make the move you would play now/.test(intro) && !/\bd4\b/.test(intro), intro);
    // Play d2-d4 (the engine's move) by clicking squares.
    await page.locator('.sandbox-board-wrapper [data-square="d2"]').click();
    await page.locator('.sandbox-board-wrapper [data-square="d4"]').click();
    await page.waitForSelector('[data-testid="sandbox-practice-result"]', { timeout: 20000 });
    const result = await page.locator('[data-testid="sandbox-practice-result"]').innerText();
    check('the first move is graded and the engine move revealed', /You found d4/.test(result), result);

    // --- review game --------------------------------------------------------
    await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
    await page.waitForSelector('.pf-finding');
    await page.locator('[data-testid="pf-example"]').first().locator('button', { hasText: 'Review game' }).click();
    await page.waitForURL(BASE + '/', { timeout: 15000 });
    await page.waitForFunction(() => localStorage.getItem('chess-mode') === 'postmortem', null, { timeout: 15000 });
    check('Review game opens Review on that game', true);

    // --- Settings: the same confirmation guards the library there ------------
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
    await page.waitForSelector('.settings-card');
    const settingsRemove = page.locator('button[aria-label^="Remove "]').first();
    await settingsRemove.scrollIntoViewIfNeeded();
    await settingsRemove.click();
    const settingsDialog = page.locator('[data-testid="confirm-dialog"]');
    check('Settings > Remove also asks "Are you sure?"', await settingsDialog.count() === 1 && /Are you sure\?/.test(await settingsDialog.innerText()));
    await page.keyboard.press('Escape');
    check('Escape cancels it', await settingsDialog.count() === 0);

    check('no page errors', errors.length === 0, errors);
} finally {
    await browser.close();
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

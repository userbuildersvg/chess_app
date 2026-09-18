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

    // --- pre-threshold: where you stand ---------------------------------
    await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-testid="pf-progress"]', { timeout: 15000 });
    const progressText = await page.locator('[data-testid="pf-progress"]').innerText();
    check('below the threshold the profile shows "0 / 10 analysed games"', /0\s*\/\s*10 analysed games/.test(progressText), progressText.slice(0, 120));
    check('...with the threshold rationale', /enough evidence before calling something a recurring weakness/.test(progressText));
    check('...and the import actions', await page.locator('[data-testid="pf-progress"] a', { hasText: 'Import recent games' }).count() === 1 && await page.locator('[data-testid="pf-progress"] button', { hasText: 'Upload PGN' }).count() === 1);
    check('...and no weakness is claimed', await page.locator('.pf-finding').count() === 0);
    await page.locator('[data-testid="pf-progress"] summary').click();
    check('"What unlocks" explains the threshold', /recurring themes across your decisions/.test(await page.locator('[data-testid="pf-progress"]').innerText()));

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
    check('the progress module is gone once the threshold is met', await page.locator('[data-testid="pf-progress"]').count() === 0);
    check('the trainer card shows pattern / games / decisions', /(Stable|Fading|Growing) pattern · \d+ games · \d+ decisions/.test(await card.locator('[data-testid="pf-meta"]').innerText()), await card.locator('[data-testid="pf-meta"]').innerText());
    check('...and where it was seen, exactly', /Seen in: (Chess\.com|Lichess) · you as White vs opp\d+ · (Blitz 5\+0|Rapid 10\+5) · 1-0 · Sep 1\d, 2026 · game #\d+/.test(await card.locator('[data-testid="pf-seen"]').innerText()));
    check('...and a next-time rule', await card.locator('[data-testid="pf-rule"]').count() === 1);
    check('evidence is folded until asked for', await card.locator('[data-testid="pf-evidence"]').count() === 0);
    await card.locator('[data-testid="pf-show-evidence"]').click();
    check('Show evidence opens the rows', await card.locator('[data-testid="pf-evidence"]').count() === 1);
    const evText = await card.locator('[data-testid="pf-evidence"]').innerText();
    check('first seen / most recent name the exact game', /First seen\s+(Chess\.com|Lichess) · you as White vs opp\d+ · (Blitz 5\+0|Rapid 10\+5) · 1-0 · Sep 1\d, 2026 · game #\d+/i.test(evText), evText.slice(0, 300));
    const examples = card.locator('[data-testid="pf-example"]');
    check('three evidence examples', await examples.count() === 3);
    const ex = await examples.first().innerText();
    check('an example shows move, engine move, phase, swing and the game label', /3\. Bb5/.test(ex) && /engine preferred d4/.test(ex) && /opening/.test(ex) && /lost 1\.\d pawns/.test(ex) && /game #\d+/.test(ex), ex);
    check('an example offers Review game', await examples.first().locator('button', { hasText: 'Review game' }).count() === 1);
    check('no FEN or PGN on the profile', !/KQkq|\[Event/.test(await page.locator('body').innerText()));
    check('Practice this is offered', await card.locator('[data-testid="pf-practice"]').count() === 1);

    // --- safe deletion ------------------------------------------------------
    const before = await page.locator('.pf-finding').first().innerText();
    const evidenceBefore = Number(/(\d+) decisions/i.exec(before)?.[1]);
    const removeBtn = page.locator('.pf-remove').first();
    await removeBtn.click();
    const dialog = page.locator('[data-testid="confirm-dialog"]');
    check('Remove asks "Are you sure?"', await dialog.count() === 1 && /Remove this imported game\?/.test(await dialog.innerText()));
    check('...with the consequence and "cannot be undone"', /no longer count toward your Improvement Profile/.test(await dialog.innerText()) && /cannot be undone/.test(await dialog.innerText()));
    await dialog.locator('button', { hasText: 'Cancel' }).click();
    check('Cancel closes the dialog and keeps the game', await dialog.count() === 0 && await page.locator('.pf-remove').count() === 11);
    await removeBtn.click();
    await dialog.locator('[data-testid="confirm-yes"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.pf-remove').length === 10, null, { timeout: 15000 });
    check('Delete game removes exactly one game', await page.locator('.pf-remove').count() === 10);
    await page.waitForTimeout(1500);
    const after = await page.locator('.pf-finding').first().innerText();
    const evidenceAfter = Number(/(\d+) decisions/i.exec(after)?.[1]);
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
    check('Learn opens with the practice brief naming the weakness', /Practicing: A tactic was missed or allowed/.test(brief), brief);
    check('the brief names the source game and the move played', /From one of your games: (Chess\.com|Lichess) · you as White vs opp\d+/.test(brief) && /move 3, you played Bb5/.test(brief), brief);
    check('the task is stated before the first move', /Your task:/.test(brief) && /first move is graded/.test(brief), brief);
    check('the engine move is withheld before the attempt', !/engine preferred|\bd4\b/.test(brief));
    check('Back to Improvement Profile is offered', await page.locator('[data-testid="sandbox-back-to-profile"]').count() === 1);
    await page.waitForSelector('[data-testid="sandbox-chat-chips"] button', { timeout: 10000 });
    const chips = await page.locator('[data-testid="sandbox-chat-chips"] button').allInnerTexts();
    check('practice chips support the task without spoiling it', chips.length >= 2 && !chips.some(c => /best move/i.test(c)), chips);
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
    check('the first move shows a result card: "You found the idea", why, and the way back',
          /RESULT/i.test(result) && /You found the idea/.test(result) && /d4 is the engine's move/.test(result) && /Back to Improvement Profile/.test(result), result);
    const chipsAfter = await page.locator('[data-testid="sandbox-chat-chips"] button').allInnerTexts();
    check('after grading the chips include "Play the best move"', chipsAfter.some(c => /Play the best move/.test(c)), chipsAfter);
    await page.locator('[data-testid="sandbox-back-to-profile"]').first().click();
    await page.waitForURL(/\/profile$/, { timeout: 15000 });
    await page.waitForSelector('.pf-finding', { timeout: 15000 });
    check('back on the profile the card says "Practised just now"', /Practised just now/.test(await page.locator('[data-testid="pf-practised"]').first().innerText().catch(() => '')));

    // --- review game --------------------------------------------------------
    await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
    await page.waitForSelector('.pf-finding');
    await page.locator('[data-testid="pf-show-evidence"]').first().click();
    // The evidence row names its game ("… · game #N"); Review must open THAT one.
    const exampleText = await page.locator('[data-testid="pf-example"]').first().innerText();
    const wantId = Number((exampleText.match(/game #(\d+)/) || [])[1]);
    const libraryBefore = (await (await page.request.get(BASE + '/api/profile/games')).json()).data?.games?.length
        ?? (await (await page.request.get(BASE + '/api/profile/games')).json()).games?.length;
    await page.locator('[data-testid="pf-example"]').first().locator('button', { hasText: 'Review game' }).click();
    await page.waitForURL(BASE + '/', { timeout: 15000 });
    await page.waitForFunction(() => localStorage.getItem('chess-mode') === 'postmortem', null, { timeout: 15000 });
    await page.waitForSelector('.pm-board-column', { timeout: 20000 });
    const reviewId = await page.evaluate(() => localStorage.getItem('postmortem-game'));
    const review = await (await page.request.get(`${BASE}/api/postmortem/game/${reviewId}`)).json();
    check('Review game opens Review in the shell', (await page.locator('.app-mode[aria-current="page"]').innerText()).trim() === 'Review');
    check('the review is the exact source game', Number.isFinite(wantId) && review.imported_game_id === wantId, { wantId, got: review.imported_game_id, exampleText: exampleText.slice(0, 120) });
    check('source metadata travels with it', review.origin === 'imported' && !!review.import_source && review.player_color !== undefined, { origin: review.origin, source: review.import_source, color: review.player_color });
    check('analysis is honestly running or done, never faked', ['running', 'done'].includes(review.scan?.status), review.scan);
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForSelector('.pm-board-column', { timeout: 20000 });
    check('a reload resumes the same review', (await page.evaluate(() => localStorage.getItem('postmortem-game'))) === reviewId);
    const libraryAfter = (await (await page.request.get(BASE + '/api/profile/games')).json()).data?.games?.length
        ?? (await (await page.request.get(BASE + '/api/profile/games')).json()).games?.length;
    check('no duplicate imported_games row was created', libraryBefore === libraryAfter, { libraryBefore, libraryAfter });
    check('a deleted game gives a visible error, not a silent nothing',
          (await page.request.post(BASE + '/api/profile/games/999999999/review')).status() === 404);

    // --- Settings: the same confirmation guards the library there ------------
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
    await page.waitForSelector('.settings-card');
    // Remove lives under the row's More disclosure in Settings.
    await page.locator('[data-testid="imported-more"]').first().scrollIntoViewIfNeeded();
    await page.locator('[data-testid="imported-more"]').first().click();
    const settingsRemove = page.locator('button[aria-label^="Remove "]').first();
    await settingsRemove.click();
    const settingsDialog = page.locator('[data-testid="confirm-dialog"]');
    check('Settings > Remove also asks "Are you sure?"', await settingsDialog.count() === 1 && /Remove this imported game\?/.test(await settingsDialog.innerText()));
    await page.keyboard.press('Escape');
    check('Escape cancels it', await settingsDialog.count() === 0);

    check('no page errors', errors.length === 0, errors);
} finally {
    await browser.close();
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

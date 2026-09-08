/**
 * The improvement-profile workflow, in a real browser, against :3001.
 *
 *     node tools/verify/profile.mjs
 *
 * Needs a backend with ACCOUNTS_ENABLED=true behind :3001 - see CLAUDE.md §22.
 *
 * WHAT THIS OWNS
 * --------------
 * `test_improvement_profile.py` proves the detection, the counting and the
 * API. This proves the half it cannot see: that the workflow is reachable from
 * Review, that a guest is invited rather than shown an error, that importing a
 * multi-game file through the real form actually lands, and - the one that
 * matters most - that the profile stays SILENT until the evidence threshold is
 * met. A screen that claims a pattern from three games would be the failure
 * that makes the whole feature untrustworthy, and it is a screen, so it is
 * checked here.
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.env.BASE || 'http://localhost:3001';
let pass = 0, fail = 0;
const check = (label, cond, detail) => {
    if (cond) { pass++; console.log('PASS  ' + label); }
    else { fail++; console.log('FAIL  ' + label + (detail !== undefined ? ' - ' + JSON.stringify(detail) : '')); }
};

/**
 * Three legal games that are genuinely DIFFERENT games.
 *
 * They used to differ only in the Event header, which was fine until imports
 * were deduplicated by move sequence - at which point all three collapsed into
 * one and the fixture was quietly testing the deduplication it was supposed to
 * be unrelated to. Different moves, not different labels.
 */
const GAMES = [
    '1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Ng5 Qxg5 0-1',
    '1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. Bg5 Be7 0-1',
    '1. c4 c5 2. Nf3 Nf6 3. d4 cxd4 4. Nxd4 e5 0-1',
];

const pgn = (n) => `[Event "Verify ${n}"]
[White "profiler"]
[Black "opponent"]
[Result "0-1"]

${GAMES[n - 1]}
`;

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });

try {
    // --- a guest is invited, not refused ----------------------------------
    await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const guestText = await page.innerText('body');
    check('a guest is told an account is needed', /account/i.test(guestText), guestText.slice(0, 120));
    check('...and is offered a way to make one',
        !!(await page.$('a[href="/signup"]')));
    check('...rather than an error', !/error|failed|something went wrong/i.test(guestText));

    // The guest request to /api/profile answers 401 BY DESIGN - that is how the
    // page learns it needs an account, and it is asserted three lines above.
    // The browser logs it as a failed resource, so it is cleared here rather
    // than left to fail the "no unexpected errors" check at the end. Errors
    // from every later phase still count.
    errors.length = 0;

    // --- reachable from Review, on the empty canvas ------------------------
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.evaluate(() => { try { localStorage.setItem('chess-mode', 'postmortem'); } catch {} });
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const cta = await page.$('.pm-profile-cta');
    check('Review offers a way into the workflow', !!cta);
    check('...and it is a link to /profile',
        (await cta?.getAttribute('href')) === '/profile');
    check('Review is still the three-mode shell, not a fourth tab',
        (await page.$$('.pm-profile-cta')).length >= 1
        && !(await page.$('a[href="/profile"].mode-tab')));

    // --- sign up, then import ---------------------------------------------
    const user = 'verifier' + Date.now().toString().slice(-6);
    await page.goto(BASE + '/signup', { waitUntil: 'networkidle' });
    await page.fill('#signup-user', user);
    await page.fill('#signup-email', user + '@example.com');
    await page.fill('#signup-pw', 'verifier-password-1');
    const [signup] = await Promise.all([
        page.waitForResponse(r => r.url().includes('/api/auth/signup')),
        page.click('button.auth-submit'),
    ]);
    if (signup.status() === 429) {
        console.error('\nSIGNUP RATE-LIMITED (429). Restart the backend and run again. Nothing verified.');
        process.exit(2);
    }
    check('the verifying account was created', signup.status() === 200, signup.status());
    await page.waitForTimeout(2000);

    await page.goto(BASE + '/profile', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    check('a signed-in account gets the workflow, not the invitation',
        !!(await page.$('#pf-paste')));

    // Three games through the paste box - deliberately fewer than the ten the
    // profile needs, because the next check is that it says so.
    await page.fill('#pf-paste', [pgn(1), pgn(2), pgn(3)].join('\n'));
    const [imported] = await Promise.all([
        page.waitForResponse(r => r.url().includes('/api/profile/games') && r.request().method() === 'POST'),
        page.click('button:text-is("Add pasted games")'),
    ]);
    check('pasting a multi-game PGN imports it', imported.status() === 200, imported.status());
    await page.waitForTimeout(2500);

    const body = await page.innerText('body');
    check('the library lists what was added', /Your games \(3\)/.test(body), body.slice(0, 200));
    check('progress is shown while the scan runs', !!(await page.$('.pf-bar')));

    // --- the same games again: reported, and NOT counted twice --------------
    // Re-uploading a season is a normal thing to do, and every claim the
    // profile makes is a count - so a duplicate that slipped through would
    // raise confidence without a single new move being played.
    await page.fill('#pf-paste', [pgn(1), pgn(2), pgn(3)].join('\n'));
    const [again] = await Promise.all([
        page.waitForResponse(r => r.url().includes('/api/profile/games') && r.request().method() === 'POST'),
        page.click('button:text-is("Add pasted games")'),
    ]);
    check('re-uploading the same games is accepted', again.status() === 200, again.status());
    await page.waitForTimeout(2500);
    const afterDup = await page.innerText('body');
    check('the library still holds three games, not six',
        /Your games \(3\)/.test(afterDup), afterDup.slice(0, 200));
    check('and the page SAYS they were already there',
        /already in your library/i.test(afterDup), afterDup.slice(0, 400));

    // --- THE ONE THAT MATTERS ---------------------------------------------
    check('the profile claims NOTHING from three games',
        (await page.$$('.pf-finding')).length === 0);
    check('...and says how many more it needs, rather than showing an empty box',
        /more games? to go|analysed games/i.test(body), body.slice(0, 300));

    // --- over the per-request limit: said out loud, never silently dropped --
    // The failure this replaces returned 200 with "added: 50, skipped: 0" for a
    // body of 51, and the fifty-first game simply ceased to exist.
    // Distinct by construction: every combination of one of White's twenty
    // legal first moves with one of Black's twenty legal replies is a legal,
    // unique two-ply game. An earlier fixture repeated `a3 a6` a growing number
    // of times, which is illegal after the first, so python-chess truncated
    // them all into the SAME game and the batch tested deduplication instead of
    // the limit. Fixtures for a uniqueness rule have to be provably unique.
    const WHITE = ['a3','a4','b3','b4','c3','c4','d3','d4','e3','e4',
                   'f3','f4','g3','g4','h3','h4','Na3','Nc3','Nf3','Nh3'];
    const BLACK = ['a6','a5','b6','b5','c6','c5','d6','d5','e6','e5',
                   'f6','f5','g6','g5','h6','h5','Na6','Nc6','Nf6','Nh6'];
    const many = [];
    for (let i = 0; i < 51; i++) {
        const w = WHITE[i % WHITE.length];
        const b = BLACK[Math.floor(i / WHITE.length) % BLACK.length];
        many.push(`[Event "L${i}"]\n[White "profiler"]\n[Black "opponent"]\n[Result "*"]\n\n1. ${w} ${b} *\n`);
    }
    await page.fill('#pf-paste', many.join('\n'));
    const [limited] = await Promise.all([
        page.waitForResponse(r => r.url().includes('/api/profile/games') && r.request().method() === 'POST'),
        page.click('button:text-is("Add pasted games")'),
    ]);
    const limitedBody = await limited.json();
    check('an over-limit batch is accepted rather than erroring', limited.status() === 200, limited.status());
    check('the response accounts for every game sent',
        limitedBody.added + limitedBody.ignored + limitedBody.skipped.length
        + limitedBody.duplicates.length === 51,
        limitedBody);
    await page.waitForTimeout(2000);
    const limitText = await page.innerText('body');
    check('the page TELLS the person some were not imported',
        /not imported/i.test(limitText), limitText.slice(0, 400));

    // --- removing a game ---------------------------------------------------
    const before = (await page.$$('.pf-game')).length;
    await page.click('.pf-game .pf-remove');
    await page.waitForTimeout(2000);
    const after = (await page.$$('.pf-game')).length;
    check('a game can be removed from the library', after === before - 1, { before, after });

    check('no console or page errors through the whole workflow',
        errors.length === 0, [...new Set(errors)][0]);
} finally {
    await browser.close();
}

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);

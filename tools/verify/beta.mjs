/**
 * The closed beta gate, in a real browser, against the dev stack on :3001.
 *
 *     CODE=ZG-BETA-XXXX-XXXX node tools/verify/beta.mjs
 *     BASE=http://localhost:3000 CODE=... node tools/verify/beta.mjs
 *
 * **It spends the code you give it.** Codes are one-time, so pass one minted
 * for this purpose (`tools/beta_codes.py generate 1 --notes verification`) and
 * never one you intend to hand to a person.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `test_beta_access.py` proves the gate through TestClient, which is where the
 * assertions about rows, races and refusals belong. This proves the half
 * TestClient cannot see, and it is the half a person would doubt:
 *
 *   * that a browser arriving at the app gets the landing page and no board;
 *   * that the API keeps refusing when the page is *told* it has access -
 *     the DevTools bypass, performed rather than argued about;
 *   * that redeeming through the real form actually opens the real app;
 *   * that the footer links every locked-out visitor can see are real pages.
 *
 * The DevTools section is the point of the file. Anyone can claim a gate is
 * server-side; this drives the page's own React state and its own localStorage
 * into the "I have access" configuration, from inside the page, and then shows
 * the API answering 403 anyway.
 *
 * Needs Playwright's chromium, imported by absolute path for the same reason
 * ui.mjs and lifecycle.mjs do - it is a machine-level install, not a project
 * dependency.
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.env.BASE || 'http://localhost:3001';
const CODE = process.env.CODE;
let pass = 0, fail = 0;
const check = (label, cond, detail) => {
  if (cond) { pass++; console.log('PASS  ' + label); }
  else { fail++; console.log('FAIL  ' + label + (detail !== undefined ? ' - ' + JSON.stringify(detail) : '')); }
};

if (!CODE) {
  console.error('Set CODE to a beta code minted for verification. It will be spent.');
  process.exit(2);
}

/** A fetch made by the page itself, so it carries the page's own cookies. */
const api = (page, path, init) => page.evaluate(
  ([p, i]) => fetch(p, i).then(async r => ({ status: r.status, body: await r.text() })),
  [path, init || {}]);

const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const consoleErrors = [];
page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });

try {
  // -----------------------------------------------------------------------
  // 1. A visitor with no invitation gets the door, not the board.
  // -----------------------------------------------------------------------
  console.log('\n--- the landing page ---');
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForSelector('.beta-card', { timeout: 20000 });

  check('the landing page is shown', await page.isVisible('.beta-card'));
  check('there is no chessboard anywhere on it',
    (await page.locator('[data-square="e2"]').count()) === 0);
  check('it carries the wordmark',
    (await page.textContent('.beta-wordmark'))?.trim() === 'Zugzwang');
  check('it says Private Beta',
    (await page.textContent('.beta-eyebrow'))?.trim() === 'Private Beta');
  check('it explains why access is closed',
    (await page.textContent('.beta-body'))?.includes('closed beta'));
  check('there is one large access-code box',
    (await page.locator('#beta-code').count()) === 1);
  check('and a Continue button',
    (await page.textContent('button.beta-submit'))?.includes('Continue'));

  // Continue is disabled until a full code has been typed, so an empty
  // submission never reaches the rate limiter.
  check('Continue is disabled while the box is empty',
    await page.isDisabled('button.beta-submit'));

  // The five footer links every uninvited visitor sees.
  for (const label of ['Request beta access', 'Contact', 'Privacy', 'Terms', 'About']) {
    check(`the footer offers "${label}"`,
      (await page.locator(`.beta-footer a:text-is("${label}")`).count()) === 1);
  }
  check('and a returning tester is offered Sign in',
    (await page.locator('.beta-alt a:text-is("Sign in")').count()) === 1);

  // -----------------------------------------------------------------------
  // 2. Deep links do not go round it.
  // -----------------------------------------------------------------------
  console.log('\n--- routes ---');
  for (const path of ['/', '/profile', '/settings', '/signup', '/anything-else']) {
    await page.goto(BASE + path, { waitUntil: 'networkidle' });
    check(`${path} shows the landing page`, await page.isVisible('.beta-card'), path);
  }

  // The pages a locked-out visitor is deliberately allowed to read. A privacy
  // policy behind the door it describes is not a privacy policy.
  for (const path of ['/privacy', '/terms', '/contact', '/request-access', '/about']) {
    await page.goto(BASE + path, { waitUntil: 'networkidle' });
    const gated = await page.locator('#beta-code').count();
    check(`${path} is readable without an invitation`, gated === 0, path);
  }
  await page.goto(BASE + '/signin', { waitUntil: 'networkidle' });
  check('/signin is reachable, or a returning tester could never get back in',
    (await page.locator('#signin-id').count()) === 1);

  // -----------------------------------------------------------------------
  // 3. The DevTools bypass, actually attempted.
  // -----------------------------------------------------------------------
  console.log('\n--- the bypasses that must not work ---');
  await page.goto(BASE, { waitUntil: 'networkidle' });

  // Everything a person poking at the page would set.
  await page.evaluate(() => {
    localStorage.setItem('beta_access', 'true');
    localStorage.setItem('zw_beta', 'granted');
    localStorage.setItem('hasAccess', 'true');
    sessionStorage.setItem('beta_access', 'true');
    document.cookie = 'zw_beta=granted; path=/';
    document.cookie = 'beta_access=true; path=/';
    window.__BETA_ACCESS__ = true;
  });
  let r = await api(page, '/api/status');
  check('localStorage, sessionStorage and invented cookies change nothing',
    r.status === 403, r);

  // A direct fetch from the page's own console, which is the specific thing
  // "a user opening DevTools" means.
  r = await api(page, '/api/move', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Beta-Access': 'true' },
    body: JSON.stringify({ move: 'e2e4', beta_access: true, has_access: true }),
  });
  check('a hand-written fetch() from the console is refused', r.status === 403, r);
  check('and it says so as a beta problem', JSON.parse(r.body).beta_required === true);

  // React state. The gate component is asked to believe it has access by
  // forcing the status endpoint to lie to it, which is as close to "editing
  // React state" as a script can get from outside the devtools protocol - and
  // it is strictly more powerful, because it also survives the re-render.
  await page.route('**/api/beta/status', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ success: true, beta_required: true, has_access: true, signed_in: false }),
  }));
  await page.goto(BASE, { waitUntil: 'networkidle' });
  const boardAppeared = await page.locator('[data-square="e2"]').count() > 0
    || await page.locator('.app-wordmark').count() > 0;
  check('lying to the page about access does render the app (the page believes it)',
    boardAppeared, 'if this fails the test below proves nothing');
  r = await api(page, '/api/status');
  check('...and the API still refuses it', r.status === 403, r);
  r = await api(page, '/api/sandbox/session', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  });
  check('...as does every other guarded route', r.status === 403, r);
  await page.unroute('**/api/beta/status');

  // -----------------------------------------------------------------------
  // 4. Redeeming through the real form.
  // -----------------------------------------------------------------------
  console.log('\n--- redemption ---');
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForSelector('#beta-code');

  // A wrong code first, so the refusal is seen by a person and not only by a
  // test client.
  await page.fill('#beta-code', 'ZZZZ-ZZZZ');
  await Promise.all([
    page.waitForResponse(r => r.url().includes('/api/beta/redeem')),
    page.click('button.beta-submit'),
  ]);
  await page.waitForSelector('.beta-error', { timeout: 10000 });
  const refusal = (await page.textContent('.beta-error')) || '';
  check('a wrong code is refused on screen', refusal.includes("isn't valid"), refusal);
  check('the refusal names no reason a guesser could use',
    !/expired|disabled|used|unknown/i.test(refusal), refusal);
  check('and the box is still there to try again',
    (await page.locator('#beta-code').count()) === 1);

  // The field. `ZG-BETA-` is a fixed adornment beside the box and only the
  // eight characters that carry entropy are editable.
  const SECRET = CODE.replace(/^ZG-BETA-/, '');
  check('the prefix is shown beside the box, not inside it',
    (await page.textContent('.beta-prefix'))?.trim() === 'ZG-BETA-');

  // TYPED, one character at a time, including the prefix the person is
  // reading off their invitation. This is the case that was broken: the field
  // used to re-parse its own output and turned a correct code into a wrong
  // one, which no amount of server-side testing would ever have caught.
  await page.fill('#beta-code', '');
  await page.type('#beta-code', CODE.toLowerCase(), { delay: 5 });
  check('typing the whole code one character at a time lands on the right code',
    (await page.inputValue('#beta-code')) === SECRET,
    await page.inputValue('#beta-code'));

  // PASTED, which is what most testers will do. `fill` sets the value in one
  // shot, the way a paste does.
  await page.fill('#beta-code', '');
  await page.fill('#beta-code', '  ' + CODE.replace(/-/g, '').toLowerCase() + ' ');
  check('pasting the whole code, lowercase and unpunctuated, works too',
    (await page.inputValue('#beta-code')) === SECRET,
    await page.inputValue('#beta-code'));

  // And the eight characters on their own, which is what somebody copying
  // from a chat message often ends up with.
  await page.fill('#beta-code', '');
  await page.fill('#beta-code', SECRET.replace('-', '').toLowerCase());
  check('pasting just the secret half works', (await page.inputValue('#beta-code')) === SECRET,
    await page.inputValue('#beta-code'));

  const [redeemResponse] = await Promise.all([
    page.waitForResponse(r => r.url().includes('/api/beta/redeem')),
    page.click('button.beta-submit'),
  ]);
  check('the real code is accepted', redeemResponse.status() === 200, redeemResponse.status());

  // -----------------------------------------------------------------------
  // 5. The app, actually working.
  // -----------------------------------------------------------------------
  console.log('\n--- inside ---');
  await page.waitForSelector('[data-square="e2"]', { timeout: 25000 });
  check('the board is on screen', await page.isVisible('[data-square="e2"]'));
  check('and the landing page is gone',
    (await page.locator('.beta-card').count()) === 0);

  r = await api(page, '/api/status');
  check('the API now answers the app', r.status === 200, r.status);

  // A real move, so this is "the app works" and not "one endpoint answered".
  await page.click('[data-square="e2"]');
  await page.click('[data-square="e4"]');
  await page.waitForTimeout(1500);
  r = await api(page, '/api/status');
  check('a move played through the board reached the server',
    r.status === 200 && JSON.parse(r.body).status.fen.includes('4P3'),
    JSON.parse(r.body).status?.fen);

  // Access survives a reload, because it is a row and not a page state.
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForSelector('[data-square="e2"]', { timeout: 25000 });
  check('access survives a reload', (await page.locator('.beta-card').count()) === 0);

  // -----------------------------------------------------------------------
  // 6. A second, untouched browser is still outside.
  // -----------------------------------------------------------------------
  console.log('\n--- a different visitor ---');
  const ctx2 = await b.newContext({ viewport: { width: 1440, height: 900 } });
  const page2 = await ctx2.newPage();
  await page2.goto(BASE, { waitUntil: 'networkidle' });
  await page2.waitForSelector('.beta-card', { timeout: 20000 });
  check('another browser gets the landing page', await page2.isVisible('.beta-card'));
  const r2 = await api(page2, '/api/status');
  check('and is refused by the API', r2.status === 403, r2.status);

  // The code the first browser used is spent.
  await page2.fill('#beta-code', CODE);  // pasted whole; the field strips the prefix
  await Promise.all([
    page2.waitForResponse(r => r.url().includes('/api/beta/redeem')),
    page2.click('button.beta-submit'),
  ]);
  await page2.waitForSelector('.beta-error', { timeout: 10000 });
  check('the already-redeemed code does not work twice',
    ((await page2.textContent('.beta-error')) || '').includes("isn't valid"));
  check('so that browser is still outside',
    (await api(page2, '/api/status')).status === 403);
  await ctx2.close();

  // -----------------------------------------------------------------------
  // 7. Nothing broke on the way.
  // -----------------------------------------------------------------------
  console.log('\n--- console ---');
  const real = consoleErrors.filter(e =>
    !e.includes('403') && !e.includes('Failed to load resource'));
  check('no unexpected console errors', real.length === 0, real.slice(0, 4));

  await page.screenshot({ path: '/tmp/beta-inside.png' });
  await page.goto(BASE + '/privacy', { waitUntil: 'networkidle' });
} finally {
  await b.close();
}

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);

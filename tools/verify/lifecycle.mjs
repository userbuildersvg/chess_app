/**
 * The account lifecycle, in a real browser, against the dev stack on :3001.
 *
 *     node tools/verify/lifecycle.mjs                    # :3001
 *     BASE=http://localhost:3000 node tools/verify/lifecycle.mjs
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `test_accounts_postgres.py` proves the same sequence through TestClient,
 * which is where the assertions about rows and revoked identities belong. This
 * proves the half TestClient cannot see: that a real browser, carrying real
 * cookies through real Set-Cookie headers on a redirect-driven signup and a
 * button-driven sign-out, ends up where the server thinks it does.
 *
 * The sequence is a reproduced release blocker, not a hypothetical. A guest
 * played, signed up (their games claimed by the account), and logged out - and
 * the browser fell back to the SAME guest cookie, so `player_state` handed the
 * claimed board straight back, still holding the `current_game_id` of a row
 * the account now owned. The check that matters most here is the plain one:
 * after logging out, the board on screen is the starting position.
 *
 * Needs Playwright's chromium, imported by absolute path for the same reason
 * ui.mjs does - it is a machine-level install, not a project dependency.
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';
const BASE = process.env.BASE || 'http://localhost:3001';
const START = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR';
let pass = 0, fail = 0;
const check = (label, cond, detail) => {
  if (cond) { pass++; console.log('PASS  ' + label); }
  else { fail++; console.log('FAIL  ' + label + (detail !== undefined ? ' - ' + JSON.stringify(detail) : '')); }
};
const api = (page, path, init) => page.evaluate(
  ([p, i]) => fetch(p, i).then(r => r.json()), [path, init || {}]);
const guestCookie = async ctx =>
  (await ctx.cookies()).find(c => c.name === 'zw_guest')?.value;

const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const user = 'browserlc' + Date.now().toString().slice(-6);

try {
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-square="e2"]', { timeout: 20000 });
  const cookie0 = await guestCookie(ctx);
  check('a fresh visitor gets a signed guest cookie', !!cookie0 && cookie0.includes('.'));

  // Play a move on the real board.
  await page.click('[data-square="e2"]');
  await page.click('[data-square="e4"]');
  await page.waitForTimeout(1200);
  let st = await api(page, '/api/status');
  check('the guest has a game in progress', st.status.fen.includes('4P3'), st.status.fen);

  // Sign up through the real page.
  //
  // `limit_signup` is 5 per hour per IP and this tool spends one of them, so
  // the sixth run within the hour is rate-limited rather than broken. Watched
  // explicitly, because a 429 here otherwise surfaces three checks later as
  // "signup did not sign the browser in", which reads exactly like the bug
  // this file exists to catch. Restarting the backend clears the bucket.
  await page.goto(BASE + '/signup', { waitUntil: 'networkidle' });
  await page.fill('#signup-user', user);
  await page.fill('#signup-email', user + '@example.com');
  await page.fill('#signup-pw', 'lifecycle-password-1');
  const [signupResponse] = await Promise.all([
    page.waitForResponse(r => r.url().includes('/api/auth/signup')),
    page.click('button.auth-submit'),
  ]);
  if (signupResponse.status() === 429) {
    console.error('\nSIGNUP RATE-LIMITED (429). This tool spends one of the five '
      + 'signups an IP gets per hour. Restart the backend to clear the bucket, '
      + 'then run this again. Nothing was verified.');
    process.exit(2);
  }
  check('signup was accepted', signupResponse.status() === 200, signupResponse.status());
  await page.waitForTimeout(2500);
  let me = await api(page, '/api/auth/me');
  check('signup signed the browser in', me.signed_in === true, me);
  check('signup issued a different guest cookie', (await guestCookie(ctx)) !== cookie0);

  // Sign out through Settings.
  await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
  await page.click('button:text-is("Sign out")');
  await page.waitForTimeout(2500);
  me = await api(page, '/api/auth/me');
  check('sign out returned the browser to guest', me.signed_in === false, me);
  const cookieOut = await guestCookie(ctx);
  check('logout issued a brand-new guest cookie', cookieOut && cookieOut !== cookie0, cookieOut);

  // THE BLOCKER: the claimed position must not be handed back.
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-square="e2"]', { timeout: 20000 });
  st = await api(page, '/api/status');
  check('the signed-out guest sees a starting board, not the claimed game',
        st.status.fen.startsWith(START), st.status.fen);
  const e4 = await page.$eval('[data-square="e4"]', el => el.innerHTML.length);
  check('the board on screen is empty on e4 too', e4 < 200, e4);

  // The new guest can play, and it is a new game.
  await page.click('[data-square="d2"]');
  await page.click('[data-square="d4"]');
  await page.waitForTimeout(1200);
  st = await api(page, '/api/status');
  check('the new guest can play its own game', st.status.fen.includes('3P4'), st.status.fen);

  // GET /api/reset must be refused.
  const code = await page.evaluate(() => fetch('/api/reset').then(r => r.status));
  check('GET /api/reset is refused in the running app', code === 405, code);
  const posted = await page.evaluate(() => fetch('/api/reset', { method: 'POST' }).then(r => r.status));
  check('POST /api/reset works', posted === 200, posted);
} finally {
  await b.close();
}
console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);

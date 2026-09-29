/**
 * The public judge path (pages/Judge.tsx), as an uninvited stranger:
 *
 *   /judge -> Start sample review -> Report -> biggest learning opportunity
 *   -> intent -> saved lesson -> practice on the main board -> End practice
 *
 * The page is told it has NO beta access (/api/beta/status is answered
 * locked), so this proves the frontend draws the path rather than the landing
 * page; test_beta_access.py section 11 proves the same APIs pass the SERVER
 * gate. Also counts provider-backed requests: only /diagnose may call Gemini,
 * once, however often the button is clicked. Makes one real diagnosis.
 *
 *     node tools/verify/judge.mjs [http://localhost:3001]
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';
let passed = 0, failed = 0;
const check = (label, ok, detail = '') => {
    if (ok) { passed++; console.log(`PASS  ${label}`); }
    else { failed++; console.log(`FAIL  ${label}${detail ? ` - ${detail}` : ''}`); }
};
const PM = '.pm';
// Every route that can reach a model. Anything else in the loop is engine-only.
const LLM = /\/api\/(learning-loop\/diagnose|postmortem\/game\/[^/]+\/(chat|ai-move)|chat|ai-move|sandbox\/.*(chat|scenario))$/;

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 } }); // fresh: incognito
const page = await ctx.newPage();
const errors = [], llm = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('request', r => { const p = new URL(r.url()).pathname; if (r.method() === 'POST' && LLM.test(p)) llm.push(p); });
await page.route('**/api/beta/status', route => route.fulfill({
    json: { beta_required: true, has_access: false, signed_in: false },
}));

// Private surfaces still draw the door for this visitor.
for (const path of ['/settings', '/profile', '/admin', '/signup']) {
    await page.goto(BASE + path, { waitUntil: 'networkidle' });
    check(`${path} shows the beta landing, not the page`, await page.locator('.home-steps, .acct-card form').count() === 0
        && /access code|invitation|closed beta/i.test(await page.locator('body').innerText()));
}

await page.goto(BASE + '/', { waitUntil: 'networkidle' });
check('homepage offers the judge demo path', await page.locator('[data-testid="home-judge"]').count() === 1);
check('homepage load: 0 LLM calls', llm.length === 0, llm.join());
await page.locator('[data-testid="home-judge"]').click();
await page.waitForURL('**/judge');
const body = await page.locator('body').innerText();
check('/judge renders past the beta gate', /Try the core loop in 2 minutes/.test(body));
check('/judge explains sample, no account, the loop, Pro optional',
    /bundled sample game/.test(body) && /No account/.test(body) && /core product loop/.test(body) && /Pro \(RevenueCat\) is optional/.test(body));
check('/judge load: 0 LLM calls', llm.length === 0, llm.join());

// Double click: one navigation, one import.
let imports = 0;
page.on('request', r => { if (r.method() === 'POST' && /\/api\/postmortem\/import$/.test(r.url())) imports++; });
await page.locator('[data-testid="judge-sample"]').dblclick();
const key = page.locator(`${PM} [data-testid="pm-key"]`);
await key.waitFor({ timeout: 120000 });
await page.waitForTimeout(1500);
check('the sample was imported exactly once', imports === 1, String(imports));
check('Report tab is selected', (await page.locator(`${PM} [role=tab][aria-selected="true"]`).innerText()) === 'Report');
const keyText = await key.innerText();
check('biggest learning opportunity names a graded move', /Move \d+\.{1,3} \S+/.test(keyText) && /graded/.test(keyText), keyText.slice(0, 160));
check('sample review/report: 0 LLM calls', llm.length === 0, llm.join());

await key.locator('[data-testid="pm-key-cta"]').click();
const corr = page.locator(`${PM} .corr-panel`);
await corr.locator('.corr-chip').first().click();
await corr.locator('.corr-textarea').fill('I wanted to win back material');
const submit = corr.locator('.corr-primary', { hasText: /Show me what I missed/ });
await submit.click();
await submit.click({ timeout: 500 }).catch(() => {}); // a second click must not start a second diagnosis
await page.waitForSelector(`${PM} .corr-card`, { timeout: 90000 });
check('a saved lesson (correction card) is generated', await corr.locator('.corr-card').count() === 1);
check('intent -> lesson: exactly 1 diagnose request', llm.length === 1 && /diagnose/.test(llm[0]), llm.join());

const practise = corr.locator('.corr-primary', { hasText: /^Practise this idea$/ });
const unavailable = await corr.locator('[data-testid="corr-practice-unavailable"]').count() === 1;
check('the lesson says whether practice exists', (await practise.count() === 1) !== unavailable);
if (await practise.count()) {
    await practise.click();
    await page.waitForSelector(`${PM} .pm-board-wrapper.is-practice`, { timeout: 60000 });
    check('practice is on the main board', await page.locator(`${PM} .corr-retest-board`).count() === 0);
    check('practice start: 0 extra LLM calls', llm.length === 1, llm.join());
    await page.locator(`${PM} [data-testid="corr-end-practice"]`).click();
    await page.waitForTimeout(600);
    check('End practice returns to the review with the lesson kept',
        await page.locator(`${PM} .pm-board-wrapper.is-practice`).count() === 0 && await page.locator(`${PM} .corr-card`).count() === 1);
}
check('no page errors', errors.length === 0, errors.join(' | '));
console.log(`\nLLM requests on the whole path: ${llm.length} (${llm.join(', ') || 'none'})`);
await browser.close();
console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);

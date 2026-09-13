/** Advanced Coach Settings interaction, persistence and responsive smoke test. */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';
let passed = 0, failed = 0;
const check = (name, ok, detail = '') => {
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? ` - ${detail}` : ''}`);
    ok ? passed++ : failed++;
};

const browser = await chromium.launch();
for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 780 }]) {
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(BASE, { waitUntil: 'networkidle' });
    await page.getByRole('tab', { name: 'Actions' }).click();
    await page.getByRole('button', { name: 'Advanced…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Behavioral space' });
    check(`${viewport.width}px modal opens`, await dialog.isVisible());
    const box = await dialog.boundingBox();
    check(`${viewport.width}px modal fits viewport`, !!box && box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width && box.y + box.height <= viewport.height,
        JSON.stringify(box));

    if (viewport.width === 1440) {
        const directness = dialog.locator('input[type="range"]').nth(0);
        const creativity = dialog.locator('input[type="range"]').nth(1);
        await directness.fill('8');
        await creativity.fill('3');
        check('sliders update coordinate', await dialog.getByText('Directness:').locator('..').textContent().then(t => t.includes('8.0')));

        const plane = dialog.locator('.coach-plane');
        const planeBox = await plane.boundingBox();
        const pointBox = await dialog.locator('.coach-point').boundingBox();
        await page.mouse.move(pointBox.x + pointBox.width / 2, pointBox.y + pointBox.height / 2);
        await page.mouse.down();
        await page.mouse.move(planeBox.x + planeBox.width * .65, planeBox.y + planeBox.height * .6, { steps: 8 });
        await page.mouse.up();
        check('point drag updates both sliders', Math.abs(Number(await directness.inputValue()) - 6.5) <= .1 && Math.abs(Number(await creativity.inputValue()) - 4) <= .1);
        await page.mouse.click(planeBox.x + planeBox.width * .2, planeBox.y + planeBox.height * .2);
        check('graph updates sliders', await directness.inputValue() === '2' && await creativity.inputValue() === '8');

        // The preview must change substantially across the four quadrants:
        // same fact ("your e4 pawn is under pressure"), a different coach.
        const preview = dialog.getByTestId('coach-style-preview').locator('blockquote');
        const previewAt = async (d, c) => {
            await directness.fill(String(d));
            await creativity.fill(String(c));
            return (await preview.textContent()).replace(/[“”"]/g, '').trim();
        };
        const words = text => new Set(text.toLowerCase().match(/[a-z]+/g).filter(w => w.length >= 4));
        const overlap = (a, b) => { const A = words(a), B = words(b); return [...A].filter(w => B.has(w)).length / new Set([...A, ...B]).size; };
        const q = {
            gentlePlain: await previewAt(1, 1),
            bluntPlain: await previewAt(10, 1),
            gentleVivid: await previewAt(1, 10),
            bluntVivid: await previewAt(10, 10),
            balanced: await previewAt(5, 5),
        };
        check('preview: low directness / low creativity is gentle and plain', /gently notice/.test(q.gentlePlain) && q.gentlePlain.length < 80, q.gentlePlain);
        check('preview: high directness / low creativity is blunt and plain', /Defend it or lose/.test(q.bluntPlain) && !/bolt|weight|hinge/.test(q.bluntPlain), q.bluntPlain);
        check('preview: low directness / high creativity is warm and vivid', /carry a lot of weight/.test(q.gentleVivid) && !/Defend it or/.test(q.gentleVivid), q.gentleVivid);
        check('preview: high directness / high creativity is sharp and vivid', /loose bolt/.test(q.bluntVivid) && /Ignore it/.test(q.bluntVivid), q.bluntVivid);
        check('preview: every quadrant keeps the same fact (e4)', Object.values(q).every(t => /e4/.test(t)));
        check('preview: all five texts are distinct', new Set(Object.values(q)).size === 5);
        const pairs = [['gentlePlain', 'bluntPlain'], ['gentlePlain', 'gentleVivid'], ['bluntPlain', 'bluntVivid'], ['gentleVivid', 'bluntVivid'], ['gentlePlain', 'bluntVivid']];
        check('preview: quadrants share little wording', pairs.every(([a, b]) => overlap(q[a], q[b]) < 0.35),
            pairs.map(([a, b]) => `${a}/${b}=${overlap(q[a], q[b]).toFixed(2)}`).join(' '));
        check('preview: voice label tracks the point', (await dialog.getByText('Voice:').textContent()).includes('balanced · natural'));
        await previewAt(10, 10);
        check('preview: voice label at 10,10 reads blunt · vivid', (await dialog.getByText('Voice:').textContent()).includes('blunt · vivid'));
        check('preview: no banned wording in any preview', Object.values(q).every(t => !/stupid|terrible|pathetic|self-destruction|bizarre|gladly punish|invited disaster|as black|as white|if i were/i.test(t)));

        await dialog.getByRole('button', { name: 'Blunt Tactician' }).click();
        check('preset loads', await directness.inputValue() === '9.5' && await creativity.inputValue() === '2');
        await dialog.getByRole('button', { name: 'Sharp Story Coach' }).click();
        check('new preset loads', await directness.inputValue() === '8.5' && await creativity.inputValue() === '8.5' && /loose bolt/.test(await preview.textContent()));
        await dialog.getByRole('button', { name: 'Minimal Analyst' }).click();
        check('Minimal Analyst is direct and plain', await directness.inputValue() === '7' && await creativity.inputValue() === '1' && /Defend it now/.test(await preview.textContent()));
        await dialog.getByRole('button', { name: 'Blunt Tactician' }).click();
        await page.reload({ waitUntil: 'networkidle' });
        await page.getByRole('tab', { name: 'Actions' }).click();
        check('settings persist after refresh', (await page.getByText(/Blunt Tactician\. Changes delivery/).count()) === 1);
        await page.getByRole('button', { name: 'Advanced…' }).click();
        await page.getByRole('button', { name: 'Reset to balanced' }).click();
        check('reset restores 5,5', await page.locator('input[type="range"]').nth(0).inputValue() === '5' && await page.locator('input[type="range"]').nth(1).inputValue() === '5');
        await page.getByRole('button', { name: 'Done' }).click();
        check('Done closes modal', !(await page.getByRole('dialog').isVisible()));

        await page.evaluate(() => {
            localStorage.setItem('chess-coach-bluntness', '8');
            localStorage.setItem('chess-coach-creativity', '9');
        });
        let movePayload = null;
        await page.route('**/api/move', async route => {
            movePayload = route.request().postDataJSON();
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ success: false, message: 'payload probe' }),
            });
        });
        await page.locator('[data-square="e2"]').click();
        await page.locator('[data-square="e4"]').click();
        await page.waitForTimeout(100);
        check('Play move carries bounded coach style payload',
            movePayload?.coach_style?.bluntness === 8 && movePayload?.coach_style?.creativity === 9);
        await page.unroute('**/api/move');
        await page.evaluate(() => {
            localStorage.setItem('chess-coach-bluntness', '5');
            localStorage.setItem('chess-coach-creativity', '5');
            localStorage.setItem('chess-coach-style-preset', 'balanced');
        });
    } else {
        await page.keyboard.press('Escape');
        check('Escape closes modal', !(await dialog.isVisible()));
    }
    check(`${viewport.width}px has no page errors`, errors.length === 0, errors.join('; '));
    await page.close();
}

await browser.close();
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

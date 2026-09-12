/**
 * Board/layout stability stress probe.
 *
 * Unlike ui.mjs and overlap.mjs, this watches every animation frame after
 * rapid board-size, viewport, panel, Guided Play, and orientation changes.
 * A layout that eventually looks right but keeps changing size therefore
 * fails here.
 *
 * Browser zoom changes the CSS-pixel viewport available to a page. The zoom
 * cases below preserve a 1440x900 physical window and drive Chromium through
 * the corresponding CSS viewport while also setting its device scale factor.
 *
 *     node tools/verify/layout-stress.mjs [http://localhost:3001]
 */
import { chromium } from '/home/david111/.local/lib/node-v24.20.0-linux-x64/lib/node_modules/playwright/index.mjs';

const BASE = process.argv[2]?.startsWith('http') ? process.argv[2] : 'http://localhost:3001';

const OPERA = `[Event "Paris Opera"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
`;

const VIEWPORTS = [
    [1920, 1080], [1536, 864], [1440, 900], [1366, 768], [1280, 720],
    [1024, 768], [900, 700], [768, 900], [430, 932], [390, 844],
].map(([width, height]) => ({ label: `${width}x${height}`, width, height, scale: 1 }));

const ZOOMS = [80, 90, 100, 110, 125, 150].map(percent => ({
    label: `1440x900 @ ${percent}%`,
    width: Math.round(1440 / (percent / 100)),
    height: Math.round(900 / (percent / 100)),
    scale: percent / 100,
}));

const CASES = [...VIEWPORTS, ...ZOOMS.filter(item => item.label !== '1440x900 @ 100%')];
const MODES = {
    Play: {
        button: 'Play', root: '.chess-container', board: '.chess-board-wrapper',
        tab: '.rail-icon-btn', actions: 'Actions', rotate: null,
    },
    Learn: {
        button: 'Learn', root: '.sandbox', board: '.sandbox-board-wrapper',
        tab: '.sandbox-tab', actions: 'Actions', rotate: '.sandbox-rotate-btn',
    },
    Review: {
        button: 'Review', root: '.pm', board: '.pm-board-wrapper',
        tab: '.pm-tab', actions: 'Actions', rotate: '.pm-rotate-btn',
    },
};

let passed = 0;
let failed = 0;
const failures = [];
function check(label, ok, detail = '') {
    if (ok) {
        passed += 1;
        console.log(`PASS  ${label}`);
    } else {
        failed += 1;
        const line = `${label}${detail ? ` - ${detail}` : ''}`;
        failures.push(line);
        console.log(`FAIL  ${line}`);
    }
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
const consoleErrors = [];
page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
});
page.on('pageerror', error => consoleErrors.push(`pageerror: ${String(error)}`));

await page.addInitScript(() => {
    try {
        localStorage.setItem('chess-mode', 'game');
        localStorage.setItem('zugzwang-board-size', 'auto');
    } catch {}
});
await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('.chess-container .chess-board-wrapper');

// Mount and retain all three modes, matching normal tester behaviour. Review
// receives a real move list so its long-content and navigation layout are live.
await page.getByRole('button', { name: 'Learn', exact: true }).click();
await page.waitForSelector('.sandbox [data-square="e2"]', { timeout: 20000 });
await page.getByRole('button', { name: 'Review', exact: true }).click();
if (await page.locator('.pm-board-wrapper').count() === 0) {
    await page.setInputFiles('.pm-file-input', {
        name: 'opera-layout-stress.pgn',
        mimeType: 'application/x-chess-pgn',
        buffer: Buffer.from(OPERA),
    });
    await page.waitForSelector('.pm-board-wrapper', { timeout: 30000 });
}
await page.waitForTimeout(800);

async function setMetrics({ width, height, scale }) {
    await cdp.send('Emulation.setDeviceMetricsOverride', {
        width, height, deviceScaleFactor: scale, mobile: false,
        screenWidth: Math.round(width * scale),
        screenHeight: Math.round(height * scale),
    });
    await page.waitForTimeout(120);
}

async function frameSamples(rootSelector) {
    return page.evaluate(async root => {
        const samples = [];
        for (let index = 0; index < 48; index += 1) {
            await new Promise(resolve => requestAnimationFrame(resolve));
            const node = document.querySelector(root);
            if (!node) break;
            const value = Number.parseFloat(getComputedStyle(node).getPropertyValue('--board-size'));
            samples.push(Number.isFinite(value) ? value : -1);
        }
        return samples;
    }, rootSelector);
}

async function geometry(rootSelector, boardSelector) {
    return page.evaluate(({ rootSelector: root, boardSelector: board }) => {
        const rootNode = document.querySelector(root);
        const frame = document.querySelector(`${root} ${board}`);
        const squares = [...document.querySelectorAll(`${root} [data-square]`)]
            .map(square => square.getBoundingClientRect())
            .filter(rect => rect.width > 0 && rect.height > 0);
        if (!rootNode || !frame || squares.length !== 64) {
            return { missing: true, squareCount: squares.length };
        }
        const frameRect = frame.getBoundingClientRect();
        const minX = Math.min(...squares.map(rect => rect.left));
        const maxX = Math.max(...squares.map(rect => rect.right));
        const minY = Math.min(...squares.map(rect => rect.top));
        const maxY = Math.max(...squares.map(rect => rect.bottom));
        const squareMin = Math.min(...squares.map(rect => Math.min(rect.width, rect.height)));
        const squareMax = Math.max(...squares.map(rect => Math.max(rect.width, rect.height)));
        // The document deliberately suppresses horizontal scrolling, so
        // scrollWidth alone cannot tell us whether a control was pushed into
        // the clipped area. Measure every visible control directly as well.
        const escapedControls = [...document.querySelectorAll('button, input, select, textarea, a[href]')]
            .filter(control => {
                const rect = control.getBoundingClientRect();
                const style = getComputedStyle(control);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden'
                    && (rect.left < -1 || rect.right > window.innerWidth + 1);
            })
            .map(control => {
                const rect = control.getBoundingClientRect();
                return `${control.getAttribute('aria-label') || control.textContent?.trim() || control.tagName}`
                    + ` [${Math.round(rect.left)}, ${Math.round(rect.right)}]`;
            });
        return {
            missing: false,
            boardWidth: maxX - minX,
            boardHeight: maxY - minY,
            frameWidth: frameRect.width,
            frameHeight: frameRect.height,
            squareSpread: squareMax - squareMin,
            horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            rootRight: rootNode.getBoundingClientRect().right,
            viewportWidth: window.innerWidth,
            escapedControls,
        };
    }, { rootSelector, boardSelector });
}

for (const viewport of CASES) {
    await setMetrics(viewport);
    for (const [modeName, mode] of Object.entries(MODES)) {
        await page.getByRole('button', { name: mode.button, exact: true }).click();
        await page.waitForSelector(`${mode.root}:not([hidden])`);

        // Move through different panel-height/content shapes before sizing.
        const tabs = page.locator(`${mode.root} ${mode.tab}`);
        const tabCount = await tabs.count();
        for (let index = 0; index < tabCount; index += 1) {
            await tabs.nth(index).click();
        }
        await page.locator(`${mode.root} ${mode.tab}`, { hasText: mode.actions }).click();

        const size = page.locator(`${mode.root} select[aria-label="Board size"]`);
        // Deliberately change faster than a person can. The final Auto must
        // settle even if ResizeObserver callbacks from earlier sizes are queued.
        for (const value of ['large', 'small', 'medium', 'large', 'auto']) {
            await size.selectOption(value);
            await page.waitForTimeout(18);
        }

        if (modeName === 'Play') {
            const guided = page.locator(`${mode.root} .guided-play-switch input`);
            await guided.click();
            await guided.click();
        } else if (mode.rotate) {
            const rotate = page.locator(`${mode.root} ${mode.rotate}`);
            await rotate.click();
            await rotate.click();
        }

        const samples = await frameSamples(mode.root);
        const tail = samples.slice(-20);
        const tailRange = tail.length ? Math.max(...tail) - Math.min(...tail) : Infinity;
        const changes = samples.reduce((count, value, index) => (
            index > 0 && value !== samples[index - 1] ? count + 1 : count
        ), 0);
        check(`${modeName} ${viewport.label}: board settles`, tailRange <= 1,
            `tail range ${tailRange}px; ${changes} frame changes; ${samples.join(',')}`);

        const box = await geometry(mode.root, mode.board);
        check(`${modeName} ${viewport.label}: board is present`, !box.missing,
            `${box.squareCount ?? 0} visible squares`);
        if (!box.missing) {
            check(`${modeName} ${viewport.label}: board remains square`,
                Math.abs(box.boardWidth - box.boardHeight) <= 1 && box.squareSpread <= 1,
                `${box.boardWidth.toFixed(2)}x${box.boardHeight.toFixed(2)}, square spread ${box.squareSpread.toFixed(2)}`);
            check(`${modeName} ${viewport.label}: no horizontal page overflow`,
                box.horizontalOverflow <= 1 && box.rootRight <= box.viewportWidth + 1,
                `overflow ${box.horizontalOverflow}px, root right ${box.rootRight}, viewport ${box.viewportWidth}`);
            check(`${modeName} ${viewport.label}: no visible control is clipped sideways`,
                box.escapedControls.length === 0, box.escapedControls.join('; '));
        }

        // Exercise the native popover without leaving it open over the next mode.
        await size.focus();
        await page.keyboard.press('Alt+ArrowDown').catch(() => {});
        await page.keyboard.press('Escape');

    }
}

const meaningfulErrors = [...new Set(consoleErrors)].filter(message =>
    !message.includes('favicon')
);
check('stress run: no console or ResizeObserver errors', meaningfulErrors.length === 0,
    meaningfulErrors.slice(0, 4).join(' | '));

await cdp.send('Emulation.clearDeviceMetricsOverride');
await browser.close();

console.log(`\n${passed}/${passed + failed} passed`);
if (failures.length) {
    console.log('\nFailures:');
    for (const failure of failures) console.log(`- ${failure}`);
}
process.exit(failed ? 1 : 0);

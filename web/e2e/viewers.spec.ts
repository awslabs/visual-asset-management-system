/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page, Request, Response } from "@playwright/test";
import { type AssetFileRef, apiContext, findAssetFiles } from "./support/fixtures";

/**
 * React-18 viewer-plugin load smoke test. The 3D/media viewers are dynamically imported
 * and several (Three.js, NeedleUSD, Gaussian-splat, IFC) rely on lifecycle/init guards
 * that StrictMode's double-invoke can trip. This drives the real ViewFile route for one
 * file per viewer and asserts the viewer mounts (canvas/iframe/content) with no init-time
 * console/page errors.
 *
 * Run this suite on its own (`npx playwright test viewers.spec.ts`) — each test downloads a
 * multi-MB 3D file, so batching it with the full orchestration suite can trip the edge WAF
 * rate limit (transient 403), which is environmental, not a viewer defect.
 *
 * Subjects are derived from the environment (`e2e/CLAUDE.md` Rule 1): each case asks the API for
 * a file with its extension on a distributable asset — the download API refuses non-distributable
 * assets ("Asset not distributable") — and skips with the reason when the environment has none.
 * `E2E_VIEWER_ASSET="<databaseId>/<assetId>"` confines the lookup to one asset, which is how a run
 * targets a known fixture set such as BoomBox.glb, gramophone.usdz, simpleCube.usda,
 * benchmelb.spz and Ifc4_CubeAdvancedBrep.ifc uploaded together to one distributable asset.
 */

// Benign noise to ignore (network aborts on teardown, third-party analytics, favicon).
const IGNORE = [
    /favicon/i,
    /Failed to load resource.*404/i,
    /net::ERR_ABORTED/i,
    /ResizeObserver loop/i,
    /Download the React DevTools/i,
    // App-shell config bootstrap, not a viewer. Auth.tsx re-fetches amplify-config and secure-config
    // on every page load and logs these three when the request fails at the network layer, keeping the
    // cached config so the page still renders — which is why the heading and the viewer surface both
    // appear. Those requests share the connection with a multi-MB asset download, so late in a long
    // run the edge rejects one, and the case that catches it is whichever happens to be running:
    // gramophone.usdz one round, Ifc4_CubeAdvancedBrep.ifc the next. Attributing an app-shell fetch
    // failure to the viewer under test made an environmental condition look asset-specific. A viewer's
    // own init errors are still fatal here, and a genuine config outage fails the heading assertion
    // above rather than reaching this list.
    /getAmplifyConfig: Fetch error/i,
    /Failed to refresh amplify-config/i,
    /Error getting secure-config/i,
];

function watchErrors(page: Page): string[] {
    const errors: string[] = [];
    page.on("console", (msg) => {
        if (msg.type() === "error") {
            const t = msg.text();
            if (!IGNORE.some((re) => re.test(t))) errors.push(`console: ${t}`);
        }
    });
    page.on("pageerror", (err) => {
        const t = err.message || String(err);
        if (!IGNORE.some((re) => re.test(t))) errors.push(`pageerror: ${t}`);
    });
    return errors;
}

async function openFile(page: Page, subject: AssetFileRef) {
    // Stored file keys are asset-relative with a leading slash (e.g. /BoomBox.glb); ViewFile
    // parses the segment after /file/ as the key, so it must carry that leading slash.
    await page.goto(
        `/#/databases/${subject.databaseId}/assets/${subject.assetId}` +
            `/file/${encodeURIComponent(subject.key)}`,
        { waitUntil: "domcontentloaded" }
    );
}

const cases = [
    // `select` = the viewer name to choose when the extension maps to more than one viewer
    // (e.g. .glb → Three.js / Physna / VNTANA), so no single viewer auto-loads.
    // `wasm: true` = viewer needs WebAssembly + SharedArrayBuffer (COOP/COEP cross-origin
    // isolation via the COI service worker); on the first headless load the SW may not be
    // active, in which case the viewer shows a graceful "WASM Support Not Available" notice
    // instead of a canvas. That is a valid, error-free outcome for this smoke test.
    { ext: ".glb", viewer: "Three.js", select: /Three\.js/i },
    { ext: ".usdz", viewer: "NeedleUSD", wasm: true, requiresFeature: "ALLOWUNSAFEEVAL" },
    { ext: ".usda", viewer: "NeedleUSD", wasm: true, requiresFeature: "ALLOWUNSAFEEVAL" },
    { ext: ".spz", viewer: "Gaussian splat" },
    { ext: ".ifc", viewer: "IFC BIM", requiresFeature: "ALLOWUNSAFEEVAL" },
    { ext: ".ifczip", viewer: "IFC BIM", requiresFeature: "ALLOWUNSAFEEVAL" },
    // `signedUrlChecks: true` = the viewer frames a page that receives presigned URLs (the Robotics
    // Data Viewer, offered only with ALLOWUNSAFEEVAL): the case also asserts the file is read with
    // HTTP Range requests and that no request to the app origin carries a signed URL in its URL or
    // Referer, which would put the credential into the web access logs.
    { ext: ".mcap", viewer: "Robotics", requiresFeature: "ALLOWUNSAFEEVAL", signedUrlChecks: true },
];

// `maxBytes` keeps the scan off very large files, whose download alone can outlast a case's waits.
const SCAN = { maxDatabases: 8, maxAssetsPerDatabase: 20, maxBytes: 50 * 1024 * 1024 };
const LOOKUP_SCOPE = process.env.E2E_VIEWER_ASSET
    ? `asset ${process.env.E2E_VIEWER_ASSET} (E2E_VIEWER_ASSET)`
    : `the distributable assets among the first ${SCAN.maxAssetsPerDatabase} of each of the ` +
      `first ${SCAN.maxDatabases} databases (files up to ${SCAN.maxBytes / 1024 / 1024} MB)`;

// One lookup per worker, shared by every case: the scan covers every case's extension at once.
let subjects: Map<string, AssetFileRef> | null = null;

/** The deployment's feature list, read from the app's cached config (as security.spec.ts does). */
async function featuresEnabled(page: Page): Promise<string[]> {
    if (!page.url().startsWith("http")) {
        await page.goto("/", { waitUntil: "domcontentloaded" });
    }
    const deadline = Date.now() + 20_000;
    for (;;) {
        const features = await page.evaluate(() => {
            try {
                const raw = localStorage.getItem("vams_cache_config");
                const parsed = raw ? JSON.parse(raw) : null;
                return ((parsed?.value ?? parsed ?? {}).featuresEnabled ?? null) as string[] | null;
            } catch {
                return null;
            }
        });
        if (features && features.length) return features;
        if (Date.now() > deadline) return features ?? [];
        await page.waitForTimeout(1000);
    }
}

/** A query parameter only a presigned (SigV4 query-authenticated) URL carries. */
const SIGNED_URL = /X-Amz-(?:Signature|Credential|Security-Token)=/i;

/** How long to wait for a request's full header list before using its provisional headers. */
const ALL_HEADERS_TIMEOUT_MS = 2_000;

/**
 * A request's headers: the provisional ones Playwright records when the request starts (they
 * carry `Referer`), plus the full list when it arrives within ALL_HEADERS_TIMEOUT_MS. The full
 * list does not settle for some of the requests the page makes while a framed viewer is open, so
 * it is not awaited unbounded.
 */
async function requestHeaders(request: Request): Promise<Record<string, string>> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<null>((resolve) => {
        timer = setTimeout(() => resolve(null), ALL_HEADERS_TIMEOUT_MS);
    });
    const all = await Promise.race([request.allHeaders().catch(() => null), timeout]);
    clearTimeout(timer);
    return { ...request.headers(), ...(all ?? {}) };
}

/**
 * Records, across the page and its frames (and the COI service worker, which re-issues the framed
 * viewer's fetches), the 206 responses to presigned URLs and every request to the app origin. The
 * app origin's access logs record each request's URL and Referer, so neither may carry a signed URL.
 */
function trackSignedUrls(page: Page) {
    const context = page.context();
    const requests: Request[] = [];
    let partialResponses = 0;
    const onRequest = (request: Request) => requests.push(request);
    const onResponse = (response: Response) => {
        if (response.status() === 206 && SIGNED_URL.test(response.url())) partialResponses += 1;
    };
    context.on("request", onRequest);
    context.on("response", onResponse);
    return {
        async summary(): Promise<{ partialResponses: number; appOriginLeaks: string[] }> {
            context.off("request", onRequest);
            context.off("response", onResponse);
            const appOrigin = new URL(page.url()).origin;
            const appOriginRequests = requests.flatMap((request) => {
                try {
                    const url = new URL(request.url());
                    return url.origin === appOrigin ? [{ request, url }] : [];
                } catch {
                    return [];
                }
            });
            const checked = await Promise.all(
                appOriginRequests.map(async ({ request, url }) => {
                    const leaks: string[] = [];
                    const headers = await requestHeaders(request);
                    if (SIGNED_URL.test(request.url())) leaks.push(`${url.pathname} (URL)`);
                    if (SIGNED_URL.test(headers["referer"] ?? "")) {
                        leaks.push(`${url.pathname} (Referer)`);
                    }
                    return leaks;
                })
            );
            return { partialResponses, appOriginLeaks: checked.flat() };
        },
    };
}

async function viewerSubject(page: Page, ext: string): Promise<AssetFileRef | null> {
    if (!subjects) {
        const found = await findAssetFiles(
            page,
            cases.map((c) => c.ext),
            { ...SCAN, asset: process.env.E2E_VIEWER_ASSET, distributableOnly: true }
        );
        expect(
            found,
            "the running app exposed no API base or ID token in localStorage, so no file could be " +
                "looked up — the session in e2e/.auth/admin.json may have expired (E2E_FORCE_LOGIN=1)"
        ).not.toBeNull();
        subjects = found;
    }
    return subjects!.get(ext) ?? null;
}

for (const c of cases) {
    test(`viewer loads a ${c.ext} file (${c.viewer}) without init errors`, async ({ page }) => {
        const subject = await viewerSubject(page, c.ext);
        test.skip(!subject, `No ${c.ext} file was found via the API in ${LOOKUP_SCOPE}`);
        if (c.requiresFeature) {
            const features = await featuresEnabled(page);
            test.skip(
                !features.includes(c.requiresFeature),
                `The ${c.viewer} viewer needs ${c.requiresFeature}, which this deployment does not enable`
            );
        }
        const fileName = subject!.key.split("/").filter(Boolean).pop() ?? subject!.key;
        console.log(`[viewers] ${c.ext} subject=${JSON.stringify(subject)}`);

        const errors = watchErrors(page);
        const signedUrls = c.signedUrlChecks ? trackSignedUrls(page) : null;
        await openFile(page, subject!);

        // Wait for the ViewFile shell (the file heading) to render. A plain substring match, not a
        // constructed RegExp: a file name carries regex metacharacters (`.` at minimum).
        await expect(page.getByRole("heading", { name: fileName, exact: false })).toBeVisible({
            timeout: 60_000,
        });

        // Multi-viewer extensions require an explicit pick; single-viewer ones auto-load.
        if (c.select) {
            const picker = page.getByRole("button", { name: /select viewer/i });
            if (await picker.isVisible().catch(() => false)) {
                await picker.click();
                // The picker is a listbox of viewer options.
                await page.getByRole("option", { name: c.select }).first().click();
            }
        }

        // The viewer mounts a rendering surface: a WebGL/2D <canvas> for the 3D viewers, or
        // an <iframe> for iframe-embedded viewers. WASM viewers may instead show a graceful
        // "WASM Support Not Available" notice when the COI service worker isn't yet active —
        // accept either, since both prove the viewer mounted and handled state without error.
        const surface = page.locator("canvas, iframe").first();
        const wasmNotice = page.getByText(/WebAssembly.*Support Not Available/i);
        await expect(async () => {
            const shown = c.wasm
                ? (await surface.isVisible().catch(() => false)) ||
                  (await wasmNotice.isVisible().catch(() => false))
                : await surface.isVisible().catch(() => false);
            expect(shown).toBe(true);
        }).toPass({ timeout: 60_000 });

        // Let the loader pull the file + render a frame, then check for runtime errors.
        await page.waitForTimeout(6000);

        // No uncaught page errors or viewer-init console errors.
        expect(errors, `viewer errors for ${fileName}:\n${errors.join("\n")}`).toEqual([]);

        if (signedUrls) {
            const { partialResponses, appOriginLeaks } = await signedUrls.summary();
            expect(
                partialResponses,
                `${fileName} was not read with HTTP Range requests (no 206 from Amazon S3)`
            ).toBeGreaterThan(0);
            expect(
                appOriginLeaks,
                `requests to the app origin carried a signed URL:\n${appOriginLeaks.join("\n")}`
            ).toEqual([]);
        }
    });
}

// ---------------------------------------------------------------------------------------------
// IFC BIM viewer: render proof and every panel action.
//
// A canvas being visible does not prove a model rendered, so these cases decode the viewer canvas
// in the page and compare it with the empty scene (grid only) before the model arrives. Each panel
// action then has to change the picture the way it should: hiding everything returns to the empty
// scene, Show All brings the model back, isolating a category draws less than the whole model.
// Every action is local to the browser (visibility, camera, section planes, measurements), so the
// environment is not changed (e2e/CLAUDE.md Rule 2).
//
// The cases also watch the page for Content-Security-Policy violations and for requests to any
// host other than the app, its API and AWS endpoints, which is how a viewer library that starts
// fetching from a public CDN (an air-gapped and GovCloud break) shows up.
// ---------------------------------------------------------------------------------------------

/** Colour count, luminance spread and share of non-background pixels in the IFC viewer canvas. */
interface CanvasStats {
    colours: number;
    lumSd: number;
    nonBgPct: number;
}

/** The IFC viewer's scene background colour (ThatOpenWebIfcViewerComponent.tsx, 0x333333). */
const IFC_BACKGROUND = [0x33, 0x33, 0x33];

async function ifcCanvasStats(page: Page): Promise<CanvasStats> {
    // An element screenshot rather than canvas.toDataURL: the renderer does not preserve its
    // drawing buffer, so reading the canvas back can return a blank frame.
    const canvas = page.locator("#thatopenwebifc-viewer-container canvas").first();
    const png = await canvas.screenshot();
    return page.evaluate(
        async ({ b64, bg }) => {
            const blob = await (await fetch(`data:image/png;base64,${b64}`)).blob();
            const bitmap = await createImageBitmap(blob);
            const off = new OffscreenCanvas(bitmap.width, bitmap.height);
            const ctx = off.getContext("2d")!;
            ctx.drawImage(bitmap, 0, 0);
            const d = ctx.getImageData(0, 0, bitmap.width, bitmap.height).data;
            const colours = new Set<number>();
            let sum = 0;
            let sum2 = 0;
            let n = 0;
            let nonBg = 0;
            for (let i = 0; i < d.length; i += 16) {
                const [r, g, b] = [d[i], d[i + 1], d[i + 2]];
                colours.add((r >> 3) * 1024 + (g >> 3) * 32 + (b >> 3));
                const l = 0.2126 * r + 0.7152 * g + 0.0722 * b;
                sum += l;
                sum2 += l * l;
                n += 1;
                if (Math.abs(r - bg[0]) > 6 || Math.abs(g - bg[1]) > 6 || Math.abs(b - bg[2]) > 6) {
                    nonBg += 1;
                }
            }
            const mean = sum / n;
            return {
                colours: colours.size,
                lumSd: Math.round(Math.sqrt(Math.max(0, sum2 / n - mean * mean)) * 10) / 10,
                nonBgPct: Math.round((nonBg / n) * 1000) / 10,
            };
        },
        { b64: png.toString("base64"), bg: IFC_BACKGROUND }
    );
}

/** Polls until the canvas stats satisfy `ok` (the renderer draws on its own animation frame). */
async function expectCanvas(
    page: Page,
    what: string,
    ok: (s: CanvasStats) => boolean
): Promise<CanvasStats> {
    let last: CanvasStats | null = null;
    await expect(async () => {
        last = await ifcCanvasStats(page);
        expect(ok(last), `${what}: canvas ${JSON.stringify(last)}`).toBe(true);
    }).toPass({ timeout: 20_000 });
    return last!;
}

/** Hosts the IFC viewer may contact: the app, its API, and AWS endpoints (S3, Cognito). */
function isAllowedHost(host: string, appHost: string, apiHost: string | null): boolean {
    return (
        host === appHost ||
        host === apiHost ||
        /\.amazonaws\.com$/i.test(host) ||
        /\.amazonaws\.com\.cn$/i.test(host) ||
        /\.amazoncognito\.com$/i.test(host) ||
        /\.api\.aws$/i.test(host)
    );
}

for (const ext of [".ifc", ".ifczip"]) {
    test(`IFC BIM viewer renders a ${ext} model and runs each panel action`, async ({ page }) => {
        test.setTimeout(240_000);
        const subject = await viewerSubject(page, ext);
        test.skip(!subject, `No ${ext} file was found via the API in ${LOOKUP_SCOPE}`);
        const features = await featuresEnabled(page);
        test.skip(
            !features.includes("ALLOWUNSAFEEVAL"),
            "The IFC BIM viewer needs ALLOWUNSAFEEVAL, which this deployment does not enable"
        );
        const fileName = subject!.key.split("/").filter(Boolean).pop() ?? subject!.key;
        const ctx = await apiContext(page);
        const appHost = new URL(page.url()).host;
        const apiHost = ctx?.base ? new URL(ctx.base).host : null;

        // CSP violations, recorded from the first script on every page and frame.
        await page.addInitScript(() => {
            (window as any).__vamsCsp = [];
            document.addEventListener("securitypolicyviolation", (e) =>
                (window as any).__vamsCsp.push(
                    `${e.violatedDirective} ${e.blockedURI} (${e.sourceFile}:${e.lineNumber})`
                )
            );
        });
        const foreignHosts = new Set<string>();
        page.context().on("request", (request) => {
            try {
                const host = new URL(request.url()).host;
                if (host && !isAllowedHost(host, appHost, apiHost)) foreignHosts.add(host);
            } catch {
                /* data: and blob: URLs have no host */
            }
        });
        const errors = watchErrors(page);

        await openFile(page, subject!);
        await expect(page.getByRole("heading", { name: fileName, exact: false })).toBeVisible({
            timeout: 60_000,
        });

        // The panel mounts only once the model is parsed and the scene is ready.
        const tablist = page.getByRole("tablist", { name: "IFC viewer panels" });
        await expect(tablist).toBeVisible({ timeout: 120_000 });
        const rendered = await expectCanvas(
            page,
            "model rendered",
            (s) => s.colours >= 30 && s.lumSd >= 15
        );

        // Model tree: the categories the model holds.
        await tablist.getByRole("tab", { name: /Model/ }).click();
        const tree = page.getByRole("tree", { name: "Model tree" });
        await expect(tree).toBeVisible();
        const isolateButtons = page.getByRole("button", {
            name: /^Isolate .* \(show only this\)$/,
        });
        await expect(isolateButtons.first()).toBeVisible();
        expect(await isolateButtons.count(), "categories in the model tree").toBeGreaterThan(0);

        // Hide All -> the empty scene; Show All -> the model again.
        await page.getByRole("button", { name: /Hide All/ }).click();
        const hidden = await expectCanvas(
            page,
            "Hide All",
            (s) => s.nonBgPct < rendered.nonBgPct && s.colours < rendered.colours
        );
        await page.getByRole("button", { name: /Show All/ }).click();
        await expectCanvas(
            page,
            "Show All",
            (s) => s.colours > hidden.colours && s.nonBgPct > hidden.nonBgPct
        );

        // Isolate the first category: something is drawn, but not more than the whole model.
        await isolateButtons.first().click();
        await expectCanvas(
            page,
            "Isolate",
            (s) => s.nonBgPct > hidden.nonBgPct && s.nonBgPct <= rendered.nonBgPct
        );
        await page.getByRole("button", { name: /Show All/ }).click();

        // Select a category from the tree -> Properties lists the element.
        await tree.locator("[data-node-key]").first().locator("span[title]").first().click();
        await tablist.getByRole("tab", { name: /Properties/ }).click();
        await expect(page.getByRole("tabpanel", { name: /Properties/ })).not.toBeEmpty();

        // Tools: camera presets, zoom and fit each keep the model on screen.
        await tablist.getByRole("tab", { name: /Tools/ }).click();
        for (const view of ["Top", "Front", "Left", "Iso"]) {
            await page.getByRole("button", { name: view, exact: true }).click();
            await expectCanvas(page, `${view} view`, (s) => s.nonBgPct > hidden.nonBgPct);
        }
        await page.getByRole("button", { name: /Zoom In/ }).click();
        await page.getByRole("button", { name: /Zoom Out/ }).click();
        await page.getByRole("button", { name: /Fit to View/ }).click();
        await expectCanvas(page, "Fit to View", (s) => s.nonBgPct > hidden.nonBgPct);

        // Section plane: enable, double-click the model, then delete every plane.
        const canvas = page.locator("#thatopenwebifc-viewer-container canvas").first();
        const box = (await canvas.boundingBox())!;
        const centre = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
        await page.getByRole("button", { name: "Enable Section Plane" }).click();
        await expect(page.getByRole("button", { name: "Section: ON" })).toBeVisible();
        await page.mouse.move(centre.x, centre.y);
        await page.mouse.dblclick(centre.x, centre.y);
        await page.getByRole("button", { name: "Delete All Planes" }).click();
        await page.getByRole("button", { name: "Section: ON" }).click();

        // Length: two double-clicks on the model draw a measurement label; Clear removes it.
        await page.getByRole("button", { name: "Length", exact: true }).click();
        for (const dx of [-60, 60]) {
            await page.mouse.move(centre.x + dx, centre.y);
            await page.mouse.dblclick(centre.x + dx, centre.y);
        }
        await page.getByRole("button", { name: "Length", exact: true }).click();

        // Area: three points, then Finish.
        await page.getByRole("button", { name: "Area", exact: true }).click();
        for (const [dx, dy] of [
            [-50, -30],
            [50, -30],
            [50, 40],
        ]) {
            await page.mouse.move(centre.x + dx, centre.y + dy);
            await page.mouse.dblclick(centre.x + dx, centre.y + dy);
        }
        await page.getByRole("button", { name: /Finish Area/ }).click();
        await page.getByRole("button", { name: "Clear Measurements" }).click();
        await expectCanvas(page, "after the tools", (s) => s.nonBgPct > hidden.nonBgPct);

        const csp = await page.evaluate(() => (window as any).__vamsCsp as string[]);
        expect(csp, `CSP violations while viewing ${fileName}`).toEqual([]);
        expect(
            [...foreignHosts],
            `requests left the app, its API and AWS while viewing ${fileName}`
        ).toEqual([]);
        expect(errors, `viewer errors for ${fileName}:\n${errors.join("\n")}`).toEqual([]);
    });
}

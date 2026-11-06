/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page, Request, Response } from "@playwright/test";
import {
    type AssetFileRef,
    dataGrid,
    expectCanvasRendered,
    expectNoNewCspViolations,
    featuresEnabled,
    findAssetFiles,
    largestCanvas,
    missingFeatures,
    openViewerFile,
    pickViewerIfAsked,
    type RenderThresholds,
    viewerSamples,
    watchConsoleErrors,
    watchCspViolations,
    watchRequestHosts,
} from "./support/fixtures";

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

        const errors = watchConsoleErrors(page);
        const signedUrls = c.signedUrlChecks ? trackSignedUrls(page) : null;
        await openViewerFile(page, subject!);

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

/**
 * Render proof per sample (#1174). The cases above prove a viewer MOUNTS; these prove it DRAWS the
 * file: canvas viewers by the pixels of their scene (an empty viewer shows only its clear colour),
 * DOM viewers by the content they render. Every case also fails on any new Content Security Policy
 * violation and on any request to a host outside the deployment (a viewer pulling code from a public
 * CDN at run time), which air-gapped and GovCloud deployments cannot serve.
 *
 * SEEDED: the subjects are the public samples listed in `e2e/fixtures/viewer-samples.json`, uploaded
 * by `tools/VamsCLI/examples/seed_viewer_samples.py` to one distributable asset. The cases skip with a
 * reason unless `E2E_VIEWER_SAMPLES` names the fixture that script writes:
 *
 *   python3 tools/VamsCLI/examples/seed_viewer_samples.py --profile <name>
 *   export E2E_VIEWER_SAMPLES=/abs/path/web/e2e/fixtures/viewer-samples-seed.json
 *   cd web && npx playwright test e2e/viewers.spec.ts
 *
 * Run the same spec on the development build first: a case that passes there and fails on a change
 * is a regression. `cspAllowed` lists the baseline violations a case already shows there, exactly.
 */

type RenderKind = "canvas" | "pdf" | "grid" | "text" | "not-offered";

interface RenderCase {
    /** Manifest id of the sample (`e2e/fixtures/viewer-samples.json`). */
    sample: string;
    /**
     * The viewer's `name` in the viewer config. The case asserts this viewer is the one selected
     * (`pickViewerIfAsked`), so it must match the config exactly; for `not-offered` it is a label.
     */
    viewer: string;
    kind: RenderKind;
    /** Picker option to choose when several viewers are offered (default: `viewer` literally). */
    select?: RegExp;
    requiresFeature?: string | string[];
    /** Run only when the deployment does NOT enable this feature (the gated path). */
    withoutFeature?: string;
    /** A request whose URL contains this must succeed (a tileset's tile, say). */
    fetches?: string;
    thresholds?: RenderThresholds;
    cspAllowed?: RegExp[];
}

const renderCases: RenderCase[] = [
    {
        sample: "cesium-tileset",
        viewer: "Cesium 3D Tileset Viewer",
        kind: "canvas",
        select: /Cesium/i,
        fetches: "dragon_low.b3dm",
    },
    {
        sample: "splat-ply",
        viewer: "Gaussian Splat Viewer (PlayCanvas)",
        kind: "canvas",
        // The picker label is "Gaussian Splat Viewer (PlayCanvas)"; a looser pattern such as
        // /PlayCanvas\)/ also matches "SuperSplat Editor (PlayCanvas)".
        select: /Gaussian Splat Viewer \(PlayCanvas\)/i,
    },
    {
        sample: "splat-sog",
        viewer: "Gaussian Splat Viewer (PlayCanvas)",
        kind: "canvas",
        // The picker label is "Gaussian Splat Viewer (PlayCanvas)"; a looser pattern such as
        // /PlayCanvas\)/ also matches "SuperSplat Editor (PlayCanvas)".
        select: /Gaussian Splat Viewer \(PlayCanvas\)/i,
    },
    { sample: "online3d-amf", viewer: "Online 3D Viewer", kind: "canvas" },
    { sample: "online3d-bim", viewer: "Online 3D Viewer", kind: "canvas" },
    { sample: "online3d-off", viewer: "Online 3D Viewer", kind: "canvas" },
    { sample: "online3d-wrl", viewer: "Online 3D Viewer", kind: "canvas" },
    {
        sample: "online3d-3dm",
        viewer: "Online 3D Viewer (Rhinoceros 3D)",
        kind: "canvas",
        requiresFeature: "ALLOWUNSAFEEVAL",
    },
    // #921: under the default CSP, rhino3dm cannot run, so no viewer may be offered for .3dm.
    {
        sample: "online3d-3dm",
        viewer: "no viewer (.3dm without ALLOWUNSAFEEVAL)",
        kind: "not-offered",
        withoutFeature: "ALLOWUNSAFEEVAL",
    },
    {
        sample: "pdf",
        viewer: "PDF Viewer",
        kind: "pdf",
        // One line of black text on a white page: a small share of pixels, two or three colours.
        thresholds: { minForegroundFraction: 0.001, minDistinctColours: 2 },
    },
    { sample: "csv", viewer: "Columnar Data Viewer", kind: "grid", select: /Columnar/i },
    { sample: "csv-bom", viewer: "Columnar Data Viewer", kind: "grid", select: /Columnar/i },
    { sample: "csv-ragged", viewer: "Columnar Data Viewer", kind: "grid", select: /Columnar/i },
    { sample: "fcs", viewer: "Columnar Data Viewer", kind: "grid" },
    { sample: "text", viewer: "Text Viewer", kind: "text" },
];

const seeded = viewerSamples();

test.describe("viewer render proof (seeded samples)", () => {
    for (const c of renderCases) {
        const label = c.withoutFeature ? `${c.sample} without ${c.withoutFeature}` : c.sample;
        test(`${c.viewer} renders ${label}`, async ({ page }) => {
            test.skip(
                !seeded,
                "E2E_VIEWER_SAMPLES is not set (or unreadable): run " +
                    "tools/VamsCLI/examples/seed_viewer_samples.py and export the path it prints"
            );
            const sample = seeded!.samples[c.sample];
            test.skip(
                !sample,
                `the seed fixture has no ${c.sample} sample; re-run the seed script`
            );

            // Watchers go in before the first navigation so they see the app's first document.
            const csp = await watchCspViolations(page);
            const hosts = watchRequestHosts(page);
            const errors = watchConsoleErrors(page);
            const fetched = c.fetches
                ? page.waitForResponse(
                      (response) => response.url().includes(c.fetches!) && response.status() < 400,
                      { timeout: 90_000 }
                  )
                : null;

            const features = await featuresEnabled(page);
            // A case that checks the gated path names the feature it must run WITHOUT; any other
            // case needs the features its own entry names or, failing that, the sample's.
            if (!c.withoutFeature) {
                const missing = missingFeatures(
                    features,
                    c.requiresFeature ?? sample.requiresFeature ?? []
                );
                test.skip(
                    missing.length > 0,
                    `${c.viewer} needs ${missing.join(", ")}, which this deployment does not enable`
                );
            }
            if (c.withoutFeature) {
                test.skip(
                    features.includes(c.withoutFeature),
                    `this case checks a deployment without ${c.withoutFeature}`
                );
            }

            const subject = {
                databaseId: seeded!.databaseId,
                assetId: seeded!.assetId,
                key: sample.key,
            };
            const fileName = sample.key.split("/").pop()!;
            await openViewerFile(page, subject);
            await expect(page.getByRole("heading", { name: fileName, exact: false })).toBeVisible({
                timeout: 60_000,
            });

            if (c.kind === "not-offered") {
                await expect(
                    page.getByText(/No compatible viewers found for file types/i)
                ).toBeVisible({
                    timeout: 60_000,
                });
                await expect(page.getByText(/Failed to load rhino3dm/i)).toHaveCount(0);
            } else {
                await pickViewerIfAsked(page, c.viewer, c.select);
            }

            if (c.kind === "canvas") {
                await expect(page.locator("canvas").first()).toBeVisible({ timeout: 60_000 });
                const stats = await expectCanvasRendered(
                    page,
                    await largestCanvas(page),
                    `${c.viewer} (${fileName})`,
                    c.thresholds
                );
                console.log(`[viewers] ${c.sample} canvas ${JSON.stringify(stats)}`);
            } else if (c.kind === "pdf") {
                const pageCanvas = page.locator(".react-pdf__Page canvas").first();
                await expect(pageCanvas).toBeVisible({ timeout: 60_000 });
                const stats = await expectCanvasRendered(
                    page,
                    pageCanvas,
                    `${c.viewer} (${fileName})`,
                    c.thresholds
                );
                console.log(`[viewers] ${c.sample} page ${JSON.stringify(stats)}`);
            } else if (c.kind === "grid") {
                // Scope to the viewer's own grid: the file page also renders the asset-versions
                // table, whose header cells a page-wide query reads before the viewer mounts.
                const grid = dataGrid(page);
                const headers = grid.getByRole("columnheader");
                await expect(headers.first()).toBeVisible({ timeout: 60_000 });
                // react-data-grid renders the header row plus a window of data rows.
                await expect
                    .poll(async () => await grid.getByRole("row").count())
                    .toBeGreaterThan(1);
                const names = await headers.allTextContents();
                expect(names.length).toBeGreaterThan(0);
                expect(
                    names.filter((name) => name.includes("\uFEFF")),
                    "a column name carries the UTF-8 byte order mark"
                ).toEqual([]);
                if (sample.expect?.columns) expect(names).toHaveLength(sample.expect.columns);
                if (sample.expect?.firstHeader) expect(names[0]).toBe(sample.expect.firstHeader);
            } else if (c.kind === "text") {
                await expect(
                    page.getByText(sample.expect?.line ?? "", { exact: false })
                ).toBeVisible({
                    timeout: 60_000,
                });
            }

            if (fetched) await fetched;
            // Let late loads (textures, workers, tiles) finish before the error and policy checks.
            await page.waitForTimeout(3000);

            expect(errors, `viewer errors for ${fileName}:\n${errors.join("\n")}`).toEqual([]);
            await expectNoNewCspViolations(csp, c.cspAllowed, fileName);
            const outside = await hosts.thirdParty();
            expect(outside, `${fileName} reached hosts outside the deployment`).toEqual([]);
        });
    }
});

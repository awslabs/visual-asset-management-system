/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page, Request, Response } from "@playwright/test";
import {
    type AssetFileRef,
    canvasStats,
    type CanvasStats,
    chooseViewer,
    CONSOLE_ERROR_IGNORE,
    CSP_CONSOLE_REPORT,
    dataGrid,
    expectCanvasRendered,
    expectSuperSplatRendered,
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
    SUPERSPLAT_CSP_ALLOWED,
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
 * targets a known fixture set such as BoomBox.glb, gramophone.usdz, simpleCube.usda, a .usdc
 * and a .usd layer, benchmelb.spz and Ifc4_CubeAdvancedBrep.ifc uploaded together to one
 * distributable asset.
 */

const cases = [
    // `select` = the viewer name to choose when the extension maps to more than one viewer
    // (e.g. .glb → Three.js / Physna / VNTANA), so no single viewer auto-loads.
    // `wasm: true` = viewer needs WebAssembly + SharedArrayBuffer (COOP/COEP cross-origin
    // isolation via the COI service worker); on the first headless load the SW may not be
    // active, in which case the viewer shows a graceful "WASM Support Not Available" notice
    // instead of a canvas. That is a valid, error-free outcome for this smoke test.
    // `referenceChecks: true` = the Three.js viewer loads the files a model references from the
    // asset; the case also asserts that no request to the web app's origin failed (an unresolved
    // texture used to be requested from the site root, which answers 403) and that no Content
    // Security Policy violation was reported. USD opens in Three.js with or without
    // ALLOWUNSAFEEVAL: a viewer is chosen only when Needle is listed too.
    { ext: ".glb", viewer: "Three.js", select: /Three\.js/i, referenceChecks: true },
    { ext: ".usdz", viewer: "Three.js", select: /Three\.js/i, referenceChecks: true },
    { ext: ".usda", viewer: "Three.js", select: /Three\.js/i, referenceChecks: true },
    { ext: ".usdc", viewer: "Three.js", select: /Three\.js/i, referenceChecks: true },
    { ext: ".usd", viewer: "Three.js", select: /Three\.js/i, referenceChecks: true },
    {
        ext: ".usdz",
        viewer: "NeedleUSD",
        select: /Needle/i,
        wasm: true,
        requiresFeature: "ALLOWUNSAFEEVAL",
    },
    {
        ext: ".usda",
        viewer: "NeedleUSD",
        select: /Needle/i,
        wasm: true,
        requiresFeature: "ALLOWUNSAFEEVAL",
    },
    // With ALLOWUNSAFEEVAL the SuperSplat Editor also opens .spz, so the picker appears there.
    { ext: ".spz", viewer: "Gaussian splat", select: /Gaussian Splat Viewer \(BabylonJS\)/i },
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

/**
 * Records failed requests to the web app's origin (status >= 400 or a network failure) and the
 * page's Content Security Policy violations while a viewer is open. Stream-route requests are
 * excluded: a referenced file the asset does not hold is a legitimate 404 there, and the viewer
 * lists it in its notice.
 */
async function trackReferenceRequests(page: Page) {
    await page.addInitScript(() => {
        (window as any).__cspViolations = [];
        document.addEventListener("securitypolicyviolation", (event: any) => {
            (window as any).__cspViolations.push(`${event.violatedDirective} ${event.blockedURI}`);
        });
    });
    const failures: string[] = [];
    const isStreamRoute = (url: string) => url.includes("/download/stream/");
    const onResponse = (response: Response) => {
        if (response.status() >= 400 && !isStreamRoute(response.url())) {
            failures.push(`${response.status()} ${response.url()}`);
        }
    };
    const onRequestFailed = (request: Request) => {
        const reason = request.failure()?.errorText ?? "";
        if (!isStreamRoute(request.url()) && !/ERR_ABORTED/i.test(reason)) {
            failures.push(`failed ${request.url()} (${reason})`);
        }
    };
    page.on("response", onResponse);
    page.on("requestfailed", onRequestFailed);
    return {
        async summary(): Promise<{ appOriginFailures: string[]; cspViolations: string[] }> {
            page.off("response", onResponse);
            page.off("requestfailed", onRequestFailed);
            const appOrigin = new URL(page.url()).origin;
            const appOriginFailures = failures.filter((line) => line.includes(appOrigin));
            const cspViolations = await page
                .evaluate(() => (window as any).__cspViolations ?? [])
                .catch(() => []);
            return { appOriginFailures, cspViolations };
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
        const references = c.referenceChecks ? await trackReferenceRequests(page) : null;
        await openViewerFile(page, subject!);

        // Wait for the ViewFile shell (the file heading) to render. A plain substring match, not a
        // constructed RegExp: a file name carries regex metacharacters (`.` at minimum).
        await expect(page.getByRole("heading", { name: fileName, exact: false })).toBeVisible({
            timeout: 60_000,
        });

        // A multi-viewer extension shows "No Viewer Component Selected" until a viewer is chosen; a
        // single-viewer one loads its viewer straight away. Wait for one of the two, then choose the
        // case's viewer with the shared keyboard helper unless the picker already shows it.
        // chooseViewer fails when no listed viewer matches, so a required pick is never skipped.
        if (c.select) {
            const surface = page.locator("canvas, iframe").first();
            const noViewer = page.getByText(/No Viewer Component Selected/i);
            await expect
                .poll(
                    async () =>
                        (await noViewer.isVisible().catch(() => false)) ||
                        (await surface.isVisible().catch(() => false)),
                    { timeout: 60_000 }
                )
                .toBe(true);
            const shown =
                (await page
                    .getByLabel(/select viewer/i)
                    .first()
                    .textContent()
                    .catch(() => null)) ?? "";
            if (!c.select.test(shown)) {
                await chooseViewer(page, c.select);
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

        if (references) {
            const { appOriginFailures, cspViolations } = await references.summary();
            expect(
                appOriginFailures,
                `requests to the web app origin failed while ${fileName} was open:\n` +
                    appOriginFailures.join("\n")
            ).toEqual([]);
            expect(
                cspViolations,
                `Content Security Policy violations while ${fileName} was open:\n` +
                    cspViolations.join("\n")
            ).toEqual([]);
        }

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

type RenderKind = "canvas" | "pdf" | "grid" | "text" | "not-offered" | "refused" | "supersplat";

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
    /**
     * A web path the deployment serves only when the viewer is enabled (a licensed add-on's
     * bundle). The case skips, with the reason, when the path is not served as JavaScript.
     */
    servedBundle?: string;
}

/** Whether the deployment serves `path` as JavaScript (the SPA answers a missing file with HTML). */
async function servesScript(page: Page, path: string): Promise<boolean> {
    const origin = new URL(page.url()).origin;
    const res = await page.request.get(`${origin}${path}`);
    return res.ok() && /javascript/i.test(res.headers()["content-type"] ?? "");
}

/** The VNTANA bundle, built only when `vntana-viewer` is enabled in addonViewerConfig.json. */
const VNTANA_BUNDLE = "/viewers/vntana/vntana-viewer.bundle.js";

/**
 * The case's thresholds over the sample's: a manifest entry may set `expect.minForegroundFraction` /
 * `expect.minDistinctColours` for a model that fills little of the canvas, and every other sample
 * keeps the defaults of `expectCanvasRendered`.
 */
function caseThresholds(c: RenderCase, sample: { expect?: Record<string, any> }): RenderThresholds {
    const fromSample: RenderThresholds = {};
    if (typeof sample.expect?.minForegroundFraction === "number") {
        fromSample.minForegroundFraction = sample.expect.minForegroundFraction;
    }
    if (typeof sample.expect?.minDistinctColours === "number") {
        fromSample.minDistinctColours = sample.expect.minDistinctColours;
    }
    return { ...fromSample, ...c.thresholds };
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
    // BabylonJS 9 with no CDN fetch (#1174): every listed format draws; SPZ version 4 is refused
    // with a message (Babylon reads it only through @adobe/spz, which the CSP blocks).
    {
        sample: "splat-ply",
        viewer: "Gaussian Splat Viewer (BabylonJS)",
        kind: "canvas",
        select: /Gaussian Splat Viewer \(BabylonJS\)/i,
    },
    {
        sample: "splat-spz-v3",
        viewer: "Gaussian Splat Viewer (BabylonJS)",
        kind: "canvas",
        select: /Gaussian Splat Viewer \(BabylonJS\)/i,
    },
    {
        sample: "splat-spz-v4",
        viewer: "Gaussian Splat Viewer (BabylonJS)",
        kind: "refused",
        select: /Gaussian Splat Viewer \(BabylonJS\)/i,
    },
    {
        sample: "splat-splat",
        viewer: "Gaussian Splat Viewer (BabylonJS)",
        kind: "canvas",
        select: /Gaussian Splat Viewer \(BabylonJS\)/i,
    },
    {
        sample: "splat-sog",
        viewer: "Gaussian Splat Viewer (BabylonJS)",
        kind: "canvas",
        select: /Gaussian Splat Viewer \(BabylonJS\)/i,
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
    // Licensed add-on, disabled by default: these cases run only on a build that enables it.
    {
        sample: "vntana-glb-box",
        viewer: "VNTANA 3D Viewer",
        kind: "canvas",
        select: /VNTANA/i,
        servedBundle: VNTANA_BUNDLE,
    },
    {
        sample: "vntana-glb-avocado",
        viewer: "VNTANA 3D Viewer",
        kind: "canvas",
        select: /VNTANA/i,
        servedBundle: VNTANA_BUNDLE,
    },
    // The IFC viewer draws a grid under the model, so these cases only prove the canvas is not
    // blank; "IFC BIM viewer panel actions" below compares the model against the bare grid.
    { sample: "ifc-haus", viewer: "ThatOpen IFC BIM Viewer", kind: "canvas" },
    { sample: "ifc-haus-zip", viewer: "ThatOpen IFC BIM Viewer", kind: "canvas" },
    { sample: "ifc-school-structural", viewer: "ThatOpen IFC BIM Viewer", kind: "canvas" },
    // SuperSplat Editor (iframe, WebGPU): splat count > 0 and a canvas that differs from the empty
    // editor's, inside the frame (expectSuperSplatRendered). One case per single-file format it is
    // offered for, SPZ v3 and v4 separately: PR-3 sends users with SPZ v4 here on ALLOWUNSAFEEVAL
    // deployments. No permissively licensed .lcc sample exists, so .lcc has no case.
    ...[
        "splat-ply",
        "splat-splat",
        "splat-sog",
        "splat-spz-v3",
        "splat-spz-v4",
        "splat-ksplat",
    ].map(
        (sample): RenderCase => ({
            sample,
            viewer: "SuperSplat Editor (PlayCanvas)",
            kind: "supersplat",
            requiresFeature: "ALLOWUNSAFEEVAL",
            cspAllowed: SUPERSPLAT_CSP_ALLOWED,
        })
    ),
];

const seeded = viewerSamples();

test.describe("viewer render proof (seeded samples)", () => {
    for (const c of renderCases) {
        const label = c.withoutFeature ? `${c.sample} without ${c.withoutFeature}` : c.sample;
        test(`${c.viewer} ${c.kind === "refused" ? "refuses" : "renders"} ${label}`, async ({
            page,
        }) => {
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
            const errors = watchConsoleErrors(
                page,
                c.cspAllowed ? [...CONSOLE_ERROR_IGNORE, CSP_CONSOLE_REPORT] : CONSOLE_ERROR_IGNORE
            );
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

            if (c.servedBundle) {
                test.skip(
                    !(await servesScript(page, c.servedBundle)),
                    `this deployment does not serve ${c.servedBundle}: ${c.viewer} is not enabled`
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

            if (c.servedBundle) {
                // The VNTANA component shows its info panel after the element's `load` event and an
                // error box after its `error` event (3.x reports what failed in `detail.type`).
                // Scoped to the viewer's root: the viewer picker above it shows the same name, so a
                // page-wide match would pass before the model loaded.
                const root = page.locator(".vntana-viewer-root");
                await expect(root.getByText(/Mouse: Rotate/)).toBeVisible({ timeout: 90_000 });
                await expect(root.getByText(/Error Loading Model/i)).toHaveCount(0);
                await expect(root.locator("vntana-zoom-in-button")).toHaveCount(1);
                await expect(root.locator("vntana-scene-graph-button")).toHaveCount(1);
            }

            if (c.kind === "canvas") {
                await expect(page.locator("canvas").first()).toBeVisible({ timeout: 60_000 });
                const stats = await expectCanvasRendered(
                    page,
                    await largestCanvas(page),
                    `${c.viewer} (${fileName})`,
                    caseThresholds(c, sample)
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
            } else if (c.kind === "supersplat") {
                // The editor mounts as one iframe on the self-hosted build, handed this file.
                const editor = page.locator('iframe[title="SuperSplat Editor"]');
                await expect(editor).toHaveCount(1, { timeout: 60_000 });
                await expect(editor).toHaveAttribute(
                    "src",
                    /^\/viewers\/supersplat\/index\.html\?load=[^&]+&filename=/
                );
                // The value is double-encoded (SuperSplatViewerComponent.tsx); searchParams undoes one.
                const handedName = new URL(
                    (await editor.getAttribute("src"))!,
                    page.url()
                ).searchParams.get("filename");
                expect(
                    decodeURIComponent(handedName ?? ""),
                    "the SuperSplat iframe was handed another file"
                ).toBe(fileName);
                const result = await expectSuperSplatRendered(
                    page,
                    editor,
                    `${c.viewer} (${fileName})`
                );
                console.log(`[viewers] ${c.sample} supersplat ${JSON.stringify(result)}`);
            } else if (c.kind === "refused") {
                // The SuperSplat Editor reads SPZ version 4 and is offered only with
                // ALLOWUNSAFEEVAL, so the message names it there and nowhere else.
                const alert = page.getByRole("alert");
                await expect(alert).toContainText(
                    `${fileName} uses SPZ format version ${sample.expect?.spzVersion ?? 4}`,
                    { timeout: 60_000 }
                );
                if (features.includes("ALLOWUNSAFEEVAL")) {
                    await expect(alert).toContainText("Open it in the SuperSplat Editor instead");
                } else {
                    await expect(alert).toContainText("does not support");
                    await expect(alert).not.toContainText("SuperSplat");
                }
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

/**
 * IFC BIM viewer panel actions (#1174), on the seeded IFC samples. The viewer draws a grid under the
 * model, so "the canvas is not blank" cannot tell a model from an empty scene. Hide All is that empty
 * scene: each case measures it and requires the model, Show All, Isolate and every camera move to draw
 * more than it. Every action is local to the browser (visibility, camera, section planes,
 * measurements), so the environment is not changed (e2e/CLAUDE.md Rule 2).
 */
test.describe("IFC BIM viewer panel actions (seeded samples)", () => {
    for (const sampleId of ["ifc-haus", "ifc-haus-zip"]) {
        test(`IFC BIM viewer runs each panel action on ${sampleId}`, async ({ page }) => {
            test.setTimeout(240_000);
            test.skip(
                !seeded,
                "E2E_VIEWER_SAMPLES is not set (or unreadable): run " +
                    "tools/VamsCLI/examples/seed_viewer_samples.py and export the path it prints"
            );
            const sample = seeded!.samples[sampleId];
            test.skip(
                !sample,
                `the seed fixture has no ${sampleId} sample; re-run the seed script`
            );

            const csp = await watchCspViolations(page);
            const hosts = watchRequestHosts(page);
            const errors = watchConsoleErrors(page);
            const missing = missingFeatures(await featuresEnabled(page), "ALLOWUNSAFEEVAL");
            test.skip(
                missing.length > 0,
                "The IFC BIM viewer needs ALLOWUNSAFEEVAL, which this deployment does not enable"
            );

            const fileName = sample.key.split("/").pop()!;
            await openViewerFile(page, {
                databaseId: seeded!.databaseId,
                assetId: seeded!.assetId,
                key: sample.key,
            });
            await expect(page.getByRole("heading", { name: fileName, exact: false })).toBeVisible({
                timeout: 60_000,
            });
            // Only one viewer handles .ifc/.ifczip; fail if any other viewer opened the file.
            await pickViewerIfAsked(page, "ThatOpen IFC BIM Viewer");

            // The panel mounts only once the model is parsed and the scene is ready.
            const tablist = page.getByRole("tablist", { name: "IFC viewer panels" });
            await expect(tablist).toBeVisible({ timeout: 120_000 });
            const canvas = page.locator("#thatopenwebifc-viewer-container canvas").first();

            /** Polls the canvas until `ok` holds (the renderer draws on its own frame loop). */
            const expectCanvas = async (
                what: string,
                ok: (s: CanvasStats) => boolean
            ): Promise<CanvasStats> => {
                let last: CanvasStats | null = null;
                await expect(async () => {
                    last = await canvasStats(page, canvas);
                    expect(ok(last), `${what}: canvas ${JSON.stringify(last)}`).toBe(true);
                }).toPass({ timeout: 20_000 });
                return last!;
            };
            const model = await canvasStats(page, canvas);

            // Model tree: Hide All gives the empty scene (grid only); Show All brings the model back.
            await tablist.getByRole("tab", { name: /Model/ }).click();
            const tree = page.getByRole("tree", { name: "Model tree" });
            await expect(tree).toBeVisible();
            const isolate = page.getByRole("button", { name: /^Isolate .* \(show only this\)$/ });
            await expect(isolate.first()).toBeVisible();
            await page.getByRole("button", { name: /Hide All/ }).click();
            const empty = await expectCanvas(
                "Hide All",
                (s) => s.distinctColours < model.distinctColours
            );
            const drawsModel = (s: CanvasStats) =>
                s.distinctColours > empty.distinctColours &&
                Math.abs(s.foregroundFraction - empty.foregroundFraction) > 0.005;
            await page.getByRole("button", { name: /Show All/ }).click();
            await expectCanvas("Show All", drawsModel);

            // Isolate the first category: some of the model, never more colours than all of it.
            await isolate.first().click();
            await expectCanvas(
                "Isolate",
                (s) =>
                    s.distinctColours > empty.distinctColours &&
                    s.distinctColours <= model.distinctColours + 8
            );
            await page.getByRole("button", { name: /Show All/ }).click();

            // Select a category from the tree; Properties then shows the element.
            await tree.locator("[data-node-key]").first().locator("span[title]").first().click();
            await tablist.getByRole("tab", { name: /Properties/ }).click();
            await expect(page.getByRole("tabpanel", { name: /Properties/ })).not.toBeEmpty();

            // Tools: camera presets, zoom and fit keep the model on screen.
            await tablist.getByRole("tab", { name: /Tools/ }).click();
            for (const view of ["Top", "Front", "Left", "Iso"]) {
                await page.getByRole("button", { name: view, exact: true }).click();
                await expectCanvas(`${view} view`, drawsModel);
            }
            await page.getByRole("button", { name: /Zoom In/ }).click();
            await page.getByRole("button", { name: /Zoom Out/ }).click();
            await page.getByRole("button", { name: /Fit to View/ }).click();
            await expectCanvas("Fit to View", drawsModel);

            // Section plane: enable, double-click the model, delete every plane, switch off.
            const box = (await canvas.boundingBox())!;
            const centre = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
            await page.getByRole("button", { name: "Enable Section Plane" }).click();
            await expect(page.getByRole("button", { name: "Section: ON" })).toBeVisible();
            await page.mouse.move(centre.x, centre.y);
            await page.mouse.dblclick(centre.x, centre.y);
            await page.getByRole("button", { name: "Delete All Planes" }).click();
            await page.getByRole("button", { name: "Section: ON" }).click();

            // Length: two points on the model; Area: three points, then Finish; then clear both.
            await page.getByRole("button", { name: "Length", exact: true }).click();
            for (const dx of [-60, 60]) {
                await page.mouse.move(centre.x + dx, centre.y);
                await page.mouse.dblclick(centre.x + dx, centre.y);
            }
            await page.getByRole("button", { name: "Length", exact: true }).click();
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
            await expectCanvas("after the tools", drawsModel);

            await page.waitForTimeout(2000);
            expect(errors, `viewer errors for ${fileName}:\n${errors.join("\n")}`).toEqual([]);
            await expectNoNewCspViolations(csp, [], fileName);
            const outside = await hosts.thirdParty();
            expect(outside, `${fileName} reached hosts outside the deployment`).toEqual([]);
        });
    }
});

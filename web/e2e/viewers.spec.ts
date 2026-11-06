/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Locator, Page, Request, Response } from "@playwright/test";
import { type AssetFileRef, apiContext, chooseViewer, findAssetFiles } from "./support/fixtures";

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
// BabylonJS Gaussian Splat viewer: every supported format renders, with no CDN fetch
// ---------------------------------------------------------------------------------------------

/**
 * The BabylonJS splat viewer must draw real content for each format it lists, and must not reach a
 * third-party host while doing so: Babylon.js 9's splat loader defaults to fetching @adobe/spz and
 * fflate from unpkg, which the CSP blocks and an air-gapped, GovCloud or EU Sovereign deployment
 * cannot reach (web/customInstalls/babylonjs/runtimeFetches.js removes them at install time).
 *
 * The subjects are the files e2e/support/prepareSplatSamples.mjs derives from the sample in
 * e2e/fixtures/viewer-samples.json and uploads to a distributable asset. Each case skips, with the
 * reason, when its file is not in the scanned assets.
 */
const SPLAT_CASES: Array<{ file: string; expect: "render" | "refuse"; pick: boolean }> = [
    // `pick` = the extension maps to more than one viewer, so the BabylonJS viewer is chosen.
    { file: "vams-e2e-splat.ply", expect: "render", pick: true },
    { file: "vams-e2e-splat-v3.spz", expect: "render", pick: false },
    // SPZ version 4 needs @adobe/spz, which the CSP does not allow; the viewer names the version.
    { file: "vams-e2e-splat-v4.spz", expect: "refuse", pick: false },
    { file: "vams-e2e-splat.splat", expect: "render", pick: true },
    { file: "vams-e2e-splat.sog", expect: "render", pick: true },
];

/**
 * CSP violations the app raises on its own before any viewer opens, as `<directive> <blocked URI>`.
 * Exact entries only: a violation not listed here fails the case.
 */
const CSP_VIOLATION_BASELINE: string[] = [];

/** Hosts besides the app origin and the API that a viewer may contact: Amazon S3 and Amazon Cognito. */
const AWS_HOST =
    /^(?:[a-z0-9.-]+\.)?(?:s3|s3-[a-z0-9-]+|cognito-idp|cognito-identity)[.a-z0-9-]*\.amazonaws\.com(?:\.cn)?$/;

/** Minimum distinct colours and luminance standard deviation of a canvas that shows a splat. */
const RENDERED = { colours: 200, luminanceStdDev: 4 };

let splatSubjects: Map<string, AssetFileRef> | null = null;

async function splatSubject(page: Page, file: string): Promise<AssetFileRef | null> {
    if (!splatSubjects) {
        const found = await findAssetFiles(
            page,
            SPLAT_CASES.map((c) => c.file),
            { ...SCAN, asset: process.env.E2E_VIEWER_ASSET, distributableOnly: true }
        );
        expect(found, "no API base or ID token in localStorage (E2E_FORCE_LOGIN=1)").not.toBeNull();
        splatSubjects = found;
    }
    return splatSubjects!.get(file) ?? null;
}

/**
 * Records every CSP violation in the page: from the first script of any later document load, and in
 * the current document too, because opening a file from the app root only changes the URL fragment.
 */
async function watchCspViolations(page: Page): Promise<() => Promise<string[]>> {
    const install = () => {
        const w = window as any;
        if (w.__vamsCspViolations) return;
        w.__vamsCspViolations = [];
        document.addEventListener("securitypolicyviolation", (e) => {
            w.__vamsCspViolations.push(`${e.violatedDirective} ${e.blockedURI}`);
        });
    };
    await page.addInitScript(install);
    if (page.url().startsWith("http")) await page.evaluate(install);
    return async () =>
        ((await page.evaluate(() => (window as any).__vamsCspViolations ?? [])) as string[]).filter(
            (v) => !CSP_VIOLATION_BASELINE.includes(v)
        );
}

/** Records the host of every request the page, its frames and its workers make. */
function watchHosts(page: Page): () => string[] {
    const hosts = new Set<string>();
    const onRequest = (request: Request) => {
        try {
            const url = new URL(request.url());
            if (url.protocol === "http:" || url.protocol === "https:") hosts.add(url.host);
        } catch {
            // blob: and data: URLs carry no host
        }
    };
    page.context().on("request", onRequest);
    return () => {
        page.context().off("request", onRequest);
        return [...hosts];
    };
}

/**
 * Distinct colours and luminance standard deviation of what the canvas shows. Everything but the
 * canvas is hidden for the screenshot, so the viewer's panels and hints overlapping it do not count,
 * and an empty scene (one clear colour) scores about zero. The PNG is decoded in the page with
 * createImageBitmap, so no image library is needed.
 */
async function canvasStats(page: Page, canvas: Locator) {
    const png = await canvas.screenshot({
        style: "* { visibility: hidden !important; } canvas { visibility: visible !important; }",
    });
    return page.evaluate(async (base64: string) => {
        const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
        const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
        const surface = new OffscreenCanvas(bitmap.width, bitmap.height);
        const context = surface.getContext("2d")!;
        context.drawImage(bitmap, 0, 0);
        const data = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
        const colours = new Set<number>();
        let sum = 0;
        let sumSquares = 0;
        const pixels = data.length / 4;
        for (let i = 0; i < data.length; i += 4) {
            colours.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
            const y = 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
            sum += y;
            sumSquares += y * y;
        }
        const mean = sum / pixels;
        return {
            colours: colours.size,
            luminanceStdDev: Math.sqrt(Math.max(0, sumSquares / pixels - mean * mean)),
        };
    }, png.toString("base64"));
}

for (const c of SPLAT_CASES) {
    test(`BabylonJS splat viewer ${c.expect === "render" ? "renders" : "refuses"} ${
        c.file
    } with no CDN fetch`, async ({ page }) => {
        const subject = await splatSubject(page, c.file);
        test.skip(
            !subject,
            `No ${c.file} was found via the API in ${LOOKUP_SCOPE}; seed it with ` +
                "e2e/support/prepareSplatSamples.mjs"
        );
        const api = await apiContext(page);
        const allowedHosts = [new URL(page.url()).host, api ? new URL(api.base).host : ""];

        const errors = watchErrors(page);
        const cspViolations = await watchCspViolations(page);
        const hosts = watchHosts(page);
        await openFile(page, subject!);
        await expect(page.getByRole("heading", { name: c.file, exact: false })).toBeVisible({
            timeout: 60_000,
        });
        if (c.pick) await chooseViewer(page, /BabylonJS/i);

        if (c.expect === "refuse") {
            await expect(page.getByRole("alert")).toContainText(
                `${c.file} uses SPZ format version 4`,
                { timeout: 60_000 }
            );
        } else {
            const canvas = page.locator(`canvas[aria-label*="${c.file}"]`);
            await expect(canvas).toBeVisible({ timeout: 60_000 });
            let last = { colours: 0, luminanceStdDev: 0 };
            await expect
                .poll(
                    async () => {
                        last = await canvasStats(page, canvas);
                        return (
                            last.colours >= RENDERED.colours &&
                            last.luminanceStdDev >= RENDERED.luminanceStdDev
                        );
                    },
                    { timeout: 90_000, intervals: [2_000, 5_000] }
                )
                .toBe(true)
                .catch(() => {
                    throw new Error(
                        `${c.file} did not render: ${last.colours} colours, luminance standard ` +
                            `deviation ${last.luminanceStdDev.toFixed(1)} (need ` +
                            `${RENDERED.colours} and ${RENDERED.luminanceStdDev})`
                    );
                });
            console.log(`[viewers] ${c.file} canvas ${JSON.stringify(last)}`);
        }

        expect(errors, `viewer errors for ${c.file}:\n${errors.join("\n")}`).toEqual([]);
        expect(await cspViolations(), `CSP violations while ${c.file} was open`).toEqual([]);
        const thirdParty = hosts().filter((h) => !allowedHosts.includes(h) && !AWS_HOST.test(h));
        expect(
            thirdParty,
            `${c.file}: requests left the deployment (a CDN such as unpkg.com or jsdelivr.net?)`
        ).toEqual([]);
    });
}

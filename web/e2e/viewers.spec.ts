/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page, Request, Response } from "@playwright/test";
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

/*
 * Potree render proof (#1174). The Potree viewer draws the octree the Point Cloud Potree Viewer
 * pipeline (`app.pipelines.usePreviewPcPotreeViewer`) wrote for the file, so each case needs a file
 * of its extension that has been through that pipeline; the case skips, naming the missing preview,
 * when the file has none. Samples and their licences: web/e2e/fixtures/viewer-samples.json.
 *
 * Each case proves the point cloud was drawn, not only that a canvas mounted:
 * - Potree reports points resident on the GPU (`Potree.lru.numPoints`), and
 * - the canvas holds point pixels. Potree's default background is a dark blue gradient whose red
 *   channel stays below ~35 (Utils.createBackgroundTexture: at most 255 * (1/15 + 0.05 + 0.012)),
 *   so a pixel whose red channel is above POTREE_POINT_RED counts as a drawn point. Potree renders
 *   with preserveDrawingBuffer, so the canvas can be read back in the page.
 * It also fails on any Content Security Policy violation and on any request to a host other than
 * the app, its API and AWS service endpoints — a third-party fetch breaks in an isolated network or
 * another partition.
 */
const potreeCases = [
    { ext: ".las" },
    { ext: ".laz" },
    { ext: ".e57" },
    // .ply also matches the Gaussian splat and Three.js viewers, so Potree is chosen explicitly.
    { ext: ".ply", select: /Potree/i },
];

/** Red-channel floor above Potree's background gradient; see the block comment above. */
const POTREE_POINT_RED = 48;
/** Share of the canvas that must hold point pixels after fitToScreen. */
const POTREE_MIN_POINT_SHARE = 0.01;

/** Hosts a viewer may contact besides the app and its API: AWS service endpoints in any partition. */
const AWS_HOST = /\.(amazonaws\.com|amazonaws\.com\.cn|amazonaws\.eu|amazoncognito\.com|aws)$/i;

let potreeSubjects: Map<string, AssetFileRef> | null = null;

for (const c of potreeCases) {
    test(`Potree draws the pipeline octree of a ${c.ext} file`, async ({ page }) => {
        if (!potreeSubjects) {
            potreeSubjects = await findAssetFiles(
                page,
                potreeCases.map((p) => p.ext),
                { ...SCAN, asset: process.env.E2E_VIEWER_ASSET, distributableOnly: true }
            );
            expect(
                potreeSubjects,
                "no API context; the saved session may have expired"
            ).not.toBeNull();
        }
        const subject = potreeSubjects!.get(c.ext) ?? null;
        test.skip(!subject, `No ${c.ext} file was found via the API in ${LOOKUP_SCOPE}`);

        // The preview the viewer will stream, at the path PotreeViewerComponent builds.
        const ctx = await apiContext(page);
        expect(ctx, "no API context; the saved session may have expired").not.toBeNull();
        const relative = subject!.key.replace(/^\/+/, "");
        const metadataUrl =
            `${ctx!.base}/database/${subject!.databaseId}/assets/${subject!.assetId}` +
            `/auxiliaryPreviewAssets/stream/${relative}/preview/PotreeViewer/metadata.json`;
        const preview = await page.request.get(metadataUrl, {
            headers: { Authorization: `Bearer ${ctx!.token}` },
        });
        test.skip(
            preview.status() === 404,
            `${
                subject!.key
            } has no Potree preview; run the Point Cloud Potree Viewer pipeline on it`
        );
        expect(preview.ok(), `the Potree preview answered ${preview.status()}`).toBe(true);

        const fileName = relative.split("/").pop() ?? relative;
        console.log(`[viewers] potree ${c.ext} subject=${JSON.stringify(subject)}`);

        const errors = watchErrors(page);
        const cspViolations: string[] = [];
        await page.exposeFunction("__vamsE2ePotreeCsp", (v: string) => cspViolations.push(v));
        await page.addInitScript(() => {
            document.addEventListener("securitypolicyviolation", (e) => {
                (window as any).__vamsE2ePotreeCsp?.(
                    `${e.effectiveDirective || e.violatedDirective} blocked ${
                        e.blockedURI || "inline"
                    }`
                );
            });
        });
        const allowedOrigins = new Set([new URL(page.url()).origin, new URL(ctx!.base).origin]);
        const thirdParty = new Set<string>();
        page.context().on("request", (request) => {
            try {
                const url = new URL(request.url());
                if (!/^https?:$/.test(url.protocol)) return;
                if (!allowedOrigins.has(url.origin) && !AWS_HOST.test(url.hostname)) {
                    thirdParty.add(url.origin);
                }
            } catch {
                // data:, blob: and similar carry no host
            }
        });
        const loaded = page.waitForEvent("console", {
            predicate: (msg) => /Point cloud loaded successfully/.test(msg.text()),
            timeout: 120_000,
        });

        // The app is already open from the API lookup, and a hash-only navigation keeps the same
        // document, so leave it first: the CSP listener is installed only in a new document.
        await page.goto("about:blank");
        await openFile(page, subject!);
        await expect(page.getByRole("heading", { name: fileName, exact: false })).toBeVisible({
            timeout: 60_000,
        });
        if (
            c.select &&
            (await page
                .getByLabel(/select viewer/i)
                .isVisible()
                .catch(() => false))
        ) {
            await chooseViewer(page, c.select);
        }

        await loaded;
        await expect
            .poll(() => page.evaluate(() => (window as any).Potree?.lru?.numPoints ?? 0), {
                timeout: 60_000,
                message: "Potree reported no points resident on the GPU",
            })
            .toBeGreaterThan(0);

        const canvas = page.locator("#potree_render_area > canvas").first();
        await expect(canvas).toBeVisible();
        let stats = { width: 0, height: 0, pointShare: 0, colours: 0 };
        await expect(async () => {
            stats = await canvas.evaluate((el, red) => {
                const source = el as HTMLCanvasElement;
                const copy = document.createElement("canvas");
                copy.width = source.width;
                copy.height = source.height;
                const g = copy.getContext("2d")!;
                g.drawImage(source, 0, 0);
                const { data } = g.getImageData(0, 0, copy.width, copy.height);
                let points = 0;
                const colours = new Set<number>();
                for (let i = 0; i < data.length; i += 4) {
                    if (data[i] > red) {
                        points += 1;
                        colours.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
                    }
                }
                return {
                    width: copy.width,
                    height: copy.height,
                    pointShare: points / (copy.width * copy.height),
                    colours: colours.size,
                };
            }, POTREE_POINT_RED);
            expect(stats.pointShare).toBeGreaterThan(POTREE_MIN_POINT_SHARE);
        }).toPass({ timeout: 30_000 });
        console.log(`[viewers] potree ${c.ext} canvas ${JSON.stringify(stats)}`);
        expect(stats.colours, "the point pixels carry a single colour").toBeGreaterThan(16);

        expect(errors, `viewer errors for ${fileName}:\n${errors.join("\n")}`).toEqual([]);
        expect(cspViolations, `CSP violations:\n${cspViolations.join("\n")}`).toEqual([]);
        expect([...thirdParty], "requests to third-party hosts").toEqual([]);
    });
}

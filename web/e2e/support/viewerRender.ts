/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { BrowserContext, expect, Frame, Locator, Page, test } from "@playwright/test";

/**
 * Render and page-safety checks for viewer specs (#1174 test plan): proof that a viewer drew the
 * file rather than only mounting a surface, every Content Security Policy violation the page or
 * its frames report, and every host the page contacts.
 */

/** One `securitypolicyviolation` event, from the page or any frame. */
export interface CspViolation {
    frameUrl: string;
    directive: string;
    blockedUri: string;
    sourceFile: string;
}

/** A known, accepted violation. Every field given must match; `framePath` matches the frame URL's path. */
export interface CspBaselineEntry {
    framePath: RegExp;
    directive: RegExp;
    blockedUri: RegExp;
    why: string;
}

/**
 * Records CSP violations from every frame of every page in the context. Call before navigating:
 * the listener is installed by an init script, which runs in each new document (frames included)
 * before its own scripts.
 */
export async function recordCspViolations(context: BrowserContext): Promise<CspViolation[]> {
    const violations: CspViolation[] = [];
    await context.exposeBinding(
        "__vamsE2eCspViolation",
        (source, v: Omit<CspViolation, "frameUrl">) => {
            violations.push({ frameUrl: source.frame.url(), ...v });
        }
    );
    await context.addInitScript(() => {
        document.addEventListener("securitypolicyviolation", (e) => {
            const report = (window as any).__vamsE2eCspViolation;
            if (typeof report === "function") {
                report({
                    directive: e.effectiveDirective || e.violatedDirective,
                    blockedUri: e.blockedURI,
                    sourceFile: e.sourceFile,
                });
            }
        });
    });
    return violations;
}

/** The violations no baseline entry accounts for. */
export function unexpectedCspViolations(
    violations: CspViolation[],
    baseline: CspBaselineEntry[]
): CspViolation[] {
    return violations.filter((v) => {
        let path = v.frameUrl;
        try {
            path = new URL(v.frameUrl).pathname;
        } catch {
            // keep the raw URL (about:blank, etc.)
        }
        return !baseline.some(
            (b) =>
                b.framePath.test(path) &&
                b.directive.test(v.directive) &&
                b.blockedUri.test(v.blockedUri)
        );
    });
}

/**
 * Hosts a viewer may contact: the app origin, the API origin, and AWS service endpoints (Amazon S3
 * presigned URLs, Amazon Cognito) in the commercial, China and European Sovereign Cloud domains.
 * Anything else — a public CDN such as unpkg or jsDelivr above all — is a third-party fetch that
 * breaks in an isolated network or partition and is reported.
 */
const AWS_HOST = /(^|\.)(amazonaws\.com|amazonaws\.com\.cn|amazonaws\.eu|amazoncognito\.com)$/i;

/** Records the origin of every http(s) request the context makes, frames and workers included. */
export function recordRequestHosts(context: BrowserContext): Set<string> {
    const origins = new Set<string>();
    context.on("request", (request) => {
        try {
            const url = new URL(request.url());
            if (url.protocol === "http:" || url.protocol === "https:") origins.add(url.origin);
        } catch {
            // not a URL we can classify (data:, blob:)
        }
    });
    return origins;
}

/** The recorded origins that are neither the app, the API, nor an AWS endpoint. */
export function thirdPartyOrigins(origins: Set<string>, allowed: string[]): string[] {
    const allow = new Set(allowed.map((o) => new URL(o).origin));
    return [...origins].filter((o) => !allow.has(o) && !AWS_HOST.test(new URL(o).hostname)).sort();
}

/** Pixel statistics of an RGBA screenshot. */
export interface PixelStats {
    width: number;
    height: number;
    /** Distinct colours after quantising each channel to 5 bits. */
    distinctColours: number;
    /** Standard deviation of luminance (0-255). */
    luminanceStdDev: number;
    /** Share of pixels that differ from `baseline` by more than 48 summed RGB levels, when given. */
    changedFraction: number | null;
}

/**
 * Decodes PNG screenshots and compares them, in a blank page so that no application CSP applies
 * (`createImageBitmap` → OffscreenCanvas → `getImageData`; no image library). Reading pixels from
 * the viewer's own canvas is not possible for every viewer: PlayCanvas and WebGPU canvases do not
 * preserve their drawing buffer, so an element screenshot is the reliable source.
 */
export async function pixelStats(
    context: BrowserContext,
    png: Buffer,
    baseline?: Buffer
): Promise<PixelStats> {
    const scratch = await context.newPage();
    try {
        return await scratch.evaluate(
            async ({ shot, base }) => {
                const decode = async (b64: string) => {
                    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
                    const bitmap = await createImageBitmap(
                        new Blob([bytes], { type: "image/png" })
                    );
                    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
                    const ctx = canvas.getContext("2d")!;
                    ctx.drawImage(bitmap, 0, 0);
                    return ctx.getImageData(0, 0, bitmap.width, bitmap.height);
                };
                const img = await decode(shot);
                const ref = base ? await decode(base) : null;
                const d = img.data;
                const colours = new Set<number>();
                let sum = 0;
                let sumSq = 0;
                let changed = 0;
                const n = img.width * img.height;
                const comparable = !!ref && ref.width === img.width && ref.height === img.height;
                for (let i = 0; i < d.length; i += 4) {
                    colours.add(((d[i] >> 3) << 10) | ((d[i + 1] >> 3) << 5) | (d[i + 2] >> 3));
                    const lum = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
                    sum += lum;
                    sumSq += lum * lum;
                    if (comparable) {
                        const r = ref!.data;
                        const delta =
                            Math.abs(d[i] - r[i]) +
                            Math.abs(d[i + 1] - r[i + 1]) +
                            Math.abs(d[i + 2] - r[i + 2]);
                        if (delta > 48) changed += 1;
                    }
                }
                const mean = sum / n;
                return {
                    width: img.width,
                    height: img.height,
                    distinctColours: colours.size,
                    luminanceStdDev: Math.sqrt(Math.max(0, sumSq / n - mean * mean)),
                    changedFraction: comparable ? changed / n : null,
                };
            },
            { shot: png.toString("base64"), base: baseline?.toString("base64") }
        );
    } finally {
        await scratch.close();
    }
}

/** The frame an `<iframe>` locator shows, once it has a document. */
export async function frameOf(iframe: Locator): Promise<Frame> {
    const handle = await iframe.elementHandle();
    const frame = handle ? await handle.contentFrame() : null;
    if (!frame) throw new Error("the iframe has no content frame");
    return frame;
}

/**
 * The violations the SuperSplat Editor's page is known to raise under the production CSP, none of
 * which breaks the editor (visualizerPlugin/CLAUDE.md, Current Viewers). Any other violation, from
 * the editor frame or the app page, fails the case.
 */
const SUPERSPLAT_FRAME = /^\/viewers\/supersplat\//;
export const SUPERSPLAT_CSP_BASELINE: CspBaselineEntry[] = [
    {
        framePath: SUPERSPLAT_FRAME,
        directive: /^script-src/,
        blockedUri: /^inline$/,
        why: "the upstream inline block that unregisters service workers stays blocked (#665)",
    },
    {
        framePath: SUPERSPLAT_FRAME,
        directive: /^base-uri$/,
        blockedUri: /\/viewers\/supersplat\/$/,
        why: "base-uri 'none' blocks the editor's <base href>; its URLs are relative to the page",
    },
    {
        framePath: SUPERSPLAT_FRAME,
        directive: /^font-src$/,
        blockedUri: /^data$/,
        why: "the embedded pc-icon data-URI font is blocked; the editor's icons fall back",
    },
];

/** Share of the frame's canvas pixels that must differ from the empty editor's. */
const SUPERSPLAT_MIN_CHANGED = 0.01;
const SUPERSPLAT_MIN_COLOURS = 32;

/**
 * Proves the SuperSplat Editor drew the file: its status bar counts more than zero splats, and its
 * canvas differs from the same editor opened with no file at the same size. Without a WebGPU
 * adapter the editor cannot render at all (it shows a startup message); the case then skips with
 * the reason, unless E2E_REQUIRE_WEBGPU=1 makes that a failure.
 */
export async function expectSuperSplatRendered(page: Page, iframe: Locator, fileName: string) {
    await expect(iframe).toBeVisible({ timeout: 60_000 });
    const frame = await frameOf(iframe);
    const hasAdapter = await frame.evaluate(async () => {
        const gpu = (navigator as any).gpu;
        return !!gpu && !!(await gpu.requestAdapter().catch(() => null));
    });
    if (!hasAdapter) {
        expect(
            process.env.E2E_REQUIRE_WEBGPU,
            "E2E_REQUIRE_WEBGPU=1 but no WebGPU adapter"
        ).not.toBe("1");
        test.skip(true, "This browser has no WebGPU adapter, which the SuperSplat Editor requires");
    }

    const splatCount = frame.locator(".status-bar-stat-value").first();
    await expect
        .poll(
            async () => Number(((await splatCount.textContent()) ?? "").replace(/\D/g, "")) || 0,
            {
                message: `the SuperSplat status bar never counted the splats of ${fileName}`,
                timeout: 90_000,
            }
        )
        .toBeGreaterThan(0);
    // Let the camera settle on the loaded scene before the screenshot.
    await page.waitForTimeout(3000);
    const canvas = frame.locator("canvas").first();
    const box = await iframe.boundingBox();
    const loaded = await canvas.screenshot();

    // The empty editor, at the iframe's size so its canvas lays out the same, as the baseline.
    const empty = await page.context().newPage();
    let baseline: Buffer;
    try {
        await empty.setViewportSize({
            width: Math.round(box!.width),
            height: Math.round(box!.height),
        });
        const src = (await iframe.getAttribute("src")) ?? "";
        await empty.goto(new URL(src, page.url()).pathname, { waitUntil: "domcontentloaded" });
        const emptyCanvas = empty.locator("canvas").first();
        await expect(empty.locator(".status-bar-stat-value").first()).toBeVisible({
            timeout: 60_000,
        });
        await empty.waitForTimeout(3000);
        baseline = await emptyCanvas.screenshot();
    } finally {
        await empty.close();
    }

    const stats = await pixelStats(page.context(), loaded, baseline!);
    console.log(`[viewers] SuperSplat ${fileName} pixels=${JSON.stringify(stats)}`);
    expect(
        stats.changedFraction,
        "the editor canvas and the empty editor differ in size"
    ).not.toBeNull();
    expect(
        stats.changedFraction!,
        `${fileName}: the SuperSplat canvas matches the empty editor`
    ).toBeGreaterThanOrEqual(SUPERSPLAT_MIN_CHANGED);
    expect(
        stats.distinctColours,
        `${fileName}: the SuperSplat canvas is nearly flat`
    ).toBeGreaterThanOrEqual(SUPERSPLAT_MIN_COLOURS);
}

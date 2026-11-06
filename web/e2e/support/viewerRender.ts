/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Locator, Page, Request } from "@playwright/test";

/**
 * Render-proof helpers for the viewer smoke (`viewers.spec.ts`). "A canvas is visible" proves a
 * viewer mounted, not that it drew the model; these prove the drawing, the policy and the hosts.
 */

/** Pixel statistics of a rendered surface: distinct colours (5 bits per channel) and luminance spread. */
export interface PixelStats {
    width: number;
    height: number;
    colours: number;
    lumStd: number;
}

/**
 * Pixel statistics of an element, from an element screenshot decoded in the browser
 * (createImageBitmap → OffscreenCanvas → getImageData). A screenshot is used rather than
 * `canvas.toDataURL`, which reads blank from a WebGL context without preserveDrawingBuffer.
 * An empty viewer scene is a flat background: one colour and a luminance spread of 0.
 *
 * `region` crops to a part of the surface, as fractions of its box, to keep a viewer's own
 * overlay panels (scene graph, toolbars) out of the measurement.
 */
export async function pixelStats(
    page: Page,
    surface: Locator,
    region: { x: number; y: number; width: number; height: number } = {
        x: 0,
        y: 0,
        width: 1,
        height: 1,
    }
): Promise<PixelStats> {
    const box = await surface.boundingBox();
    if (!box) throw new Error("the render surface has no layout box");
    const png = await page.screenshot({
        clip: {
            x: box.x + box.width * region.x,
            y: box.y + box.height * region.y,
            width: box.width * region.width,
            height: box.height * region.height,
        },
    });
    return page.evaluate(async (b64: string) => {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
        const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
        const ctx = canvas.getContext("2d")!;
        ctx.drawImage(bitmap, 0, 0);
        const data = ctx.getImageData(0, 0, bitmap.width, bitmap.height).data;
        const colours = new Set<number>();
        let sum = 0;
        let sumSq = 0;
        const n = data.length / 4;
        for (let i = 0; i < data.length; i += 4) {
            colours.add((data[i] >> 3) * 1024 + (data[i + 1] >> 3) * 32 + (data[i + 2] >> 3));
            const lum = 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
            sum += lum;
            sumSq += lum * lum;
        }
        const mean = sum / n;
        return {
            width: bitmap.width,
            height: bitmap.height,
            colours: colours.size,
            lumStd: Math.sqrt(Math.max(0, sumSq / n - mean * mean)),
        };
    }, png.toString("base64"));
}

/**
 * Records every Content-Security-Policy violation the page (and its frames) reports, from the
 * first script onwards. Read the list with `cspViolations(page)`.
 */
export async function recordCspViolations(page: Page): Promise<void> {
    await page.addInitScript(() => {
        const w = window as unknown as { __cspViolations?: string[] };
        w.__cspViolations = [];
        document.addEventListener("securitypolicyviolation", (e) => {
            w.__cspViolations!.push(`${e.effectiveDirective} ${e.blockedURI}`);
        });
    });
}

/** The CSP violations recorded since `recordCspViolations(page)` on the current document. */
export async function cspViolations(page: Page): Promise<string[]> {
    return page.evaluate(
        () => (window as unknown as { __cspViolations?: string[] }).__cspViolations ?? []
    );
}

/**
 * AWS service host suffixes per partition (commercial and GovCloud, China, EU Sovereign): the API,
 * Amazon S3 presigned downloads and Amazon Cognito all live under one of these.
 */
const AWS_HOST_SUFFIXES = [".amazonaws.com", ".amazonaws.com.cn", ".amazonaws.eu"];

/**
 * Records requests to any host other than the app origin, the API host and AWS service hosts — a
 * viewer that fetches a decoder, font or texture from a public CDN (unpkg, jsDelivr, gstatic)
 * shows up here, and such a fetch fails in an isolated or partitioned deployment.
 */
export function trackForeignHosts(page: Page, allowedHosts: string[]): { hosts(): string[] } {
    const allowed = new Set(allowedHosts.map((h) => h.toLowerCase()));
    const foreign = new Set<string>();
    const onRequest = (request: Request) => {
        let url: URL;
        try {
            url = new URL(request.url());
        } catch {
            return;
        }
        if (url.protocol !== "http:" && url.protocol !== "https:") return; // data:, blob:
        const host = url.hostname.toLowerCase();
        if (allowed.has(host) || AWS_HOST_SUFFIXES.some((s) => host.endsWith(s))) return;
        foreign.add(host);
    };
    page.context().on("request", onRequest);
    return {
        hosts() {
            page.context().off("request", onRequest);
            return [...foreign].sort();
        },
    };
}

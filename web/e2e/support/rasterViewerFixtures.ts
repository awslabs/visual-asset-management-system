/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** Helper for the GeoTIFF Map Viewer layout spec: reads pixels of a screenshot in the live app. */

import { Page } from "@playwright/test";

/**
 * How many pixels of a PNG screenshot are one of the two stripe colours of the visualizer frame's
 * backdrop (`.visualizer-container`: #606dbc and #465298). A viewer that fills its frame and paints
 * its own background shows none.
 */
export async function stripePixels(page: Page, png: Buffer): Promise<number> {
    return page.evaluate(async (b64: string) => {
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const bmp = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
        const canvas = new OffscreenCanvas(bmp.width, bmp.height);
        const ctx = canvas.getContext("2d")!;
        ctx.drawImage(bmp, 0, 0);
        const d = ctx.getImageData(0, 0, bmp.width, bmp.height).data;
        const near = (i: number, r: number, g: number, b: number) =>
            Math.abs(d[i] - r) < 4 && Math.abs(d[i + 1] - g) < 4 && Math.abs(d[i + 2] - b) < 4;
        let n = 0;
        for (let i = 0; i < d.length; i += 4) {
            if (near(i, 0x60, 0x6d, 0xbc) || near(i, 0x46, 0x52, 0x98)) n++;
        }
        return n;
    }, png.toString("base64"));
}

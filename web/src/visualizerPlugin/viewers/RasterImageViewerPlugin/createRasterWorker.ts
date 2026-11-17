/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Creates the decode worker. This lives in its own module because of `import.meta.url`, which the
 * CommonJS transform jest uses cannot evaluate; a unit test replaces this module with `jest.mock`
 * (the same arrangement as `PDFViewerPlugin/pdfWorker.ts`). `new URL(..., import.meta.url)` makes the
 * worker a tracked, content-hashed bundle asset served from the app's own origin.
 *
 * Returns null where workers are unavailable or cannot be created, and the session decodes on the
 * page instead.
 */
export function createRasterWorker(): Worker | null {
    if (typeof Worker === "undefined") return null;
    try {
        return new Worker(new URL("./rasterWorker.ts", import.meta.url), { type: "module" });
    } catch (error) {
        console.warn("TIFF/TGA viewer: decode worker unavailable, decoding on the page", error);
        return null;
    }
}

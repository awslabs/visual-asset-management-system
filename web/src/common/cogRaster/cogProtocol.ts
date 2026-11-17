/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

let registration: Promise<void> | null = null;

/**
 * Registers the `cog://` protocol with maplibre-gl once per page. Both libraries are imported on first
 * use, so a page that never draws a raster does not load the COG reader (geotiff.js and its
 * decoders). A failed load is not remembered, so the next call retries.
 */
export function registerCogProtocol(): Promise<void> {
    if (!registration) {
        registration = (async () => {
            const [maplibre, cog] = await Promise.all([
                import("maplibre-gl"),
                import("@geomatico/maplibre-cog-protocol"),
            ]);
            maplibre.addProtocol("cog", cog.cogProtocol);
        })().catch((error) => {
            registration = null;
            throw error;
        });
    }
    return registration;
}

/** For tests: forget that the protocol was registered. */
export function resetCogProtocolRegistration(): void {
    registration = null;
}

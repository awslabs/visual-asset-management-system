/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { registerCogProtocol } from "../../../common/cogRaster/cogProtocol";

/**
 * Loads the COG tile protocol when the GeoTIFF Map Viewer first opens. The registration is shared
 * with the Search map (common/cogRaster/cogProtocol), so it happens once per page whichever surface
 * draws a raster first. Nothing is unloaded on cleanup: maplibre keeps one protocol table per page
 * and another map may still use it.
 */
export class CogMapDependencyManager {
    static async loadCogProtocol(): Promise<void> {
        await registerCogProtocol();
    }

    static cleanup(): void {
        // The protocol stays registered for other maps on the page.
    }
}

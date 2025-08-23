/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as Cesium from "cesium";

export class CesiumDependencyManager {
    private static loaded = false;
    private static cesiumBaseUrl = "/cesium/";

    static async loadCesium(): Promise<void> {
        if (this.loaded) return;

        try {
            // Set Cesium base URL for assets
            (window as any).CESIUM_BASE_URL = this.cesiumBaseUrl;

            // Configure Cesium Ion access token if available
            // This can be set via environment variables or configuration
            const cesiumIonToken = process.env.REACT_APP_CESIUM_ION_TOKEN;
            if (cesiumIonToken) {
                Cesium.Ion.defaultAccessToken = cesiumIonToken;
            }

            // Set up Cesium workers and other assets
            // The cesium package should handle most of this automatically

            // Mark as loaded
            this.loaded = true;
            console.log("CesiumJS dependencies loaded successfully");
        } catch (error) {
            console.error("Failed to load CesiumJS dependencies:", error);
            throw error;
        }
    }

    static cleanup(): void {
        // Cleanup Cesium resources if needed
        // Most cleanup is handled by individual viewer instances
        this.loaded = false;
        console.log("CesiumJS dependencies cleaned up");
    }

    static isLoaded(): boolean {
        return this.loaded;
    }
}

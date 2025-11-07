/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { StylesheetManager } from "../../core/StylesheetManager";

export class CesiumDependencyManager {
    private static cesiumInstance: any = null;
    private static loadedDependencies = new Set<string>();
    private static readonly PLUGIN_ID = "cesium-viewer";
    private static loadPromise: Promise<any> | null = null;
    private static cesiumBaseUrl = "/viewers/cesium/";

    /**
     * Load Cesium dynamically from bundled files
     * @returns Promise that resolves to the Cesium module
     */
    static async loadCesium(): Promise<any> {
        // Return existing instance if already loaded
        if (this.cesiumInstance) {
            return this.cesiumInstance;
        }

        // Return existing promise if loading is in progress
        if (this.loadPromise) {
            return this.loadPromise;
        }

        // Start loading Cesium
        this.loadPromise = this.performLoad();

        try {
            const result = await this.loadPromise;
            this.loadPromise = null;
            return result;
        } catch (error) {
            this.loadPromise = null;
            throw error;
        }
    }

    /**
     * Perform the actual loading of Cesium from bundled files
     */
    private static async performLoad(): Promise<any> {
        try {
            console.log(`[${this.PLUGIN_ID}] Loading Cesium from bundle...`);

            // Set Cesium base URL for assets BEFORE loading the script
            (window as any).CESIUM_BASE_URL = this.cesiumBaseUrl;

            // Load Cesium dependencies from public folder
            await this.loadCesiumFromAssets();

            // Get the Cesium module from window object (exposed by wrapped bundle)
            this.cesiumInstance = (window as any).Cesium;

            if (!this.cesiumInstance) {
                throw new Error("Cesium not found on window object after loading bundle");
            }

            // Debug: Check what's available
            console.log(`[${this.PLUGIN_ID}] Cesium loaded successfully from bundle`);
            console.log(
                `[${this.PLUGIN_ID}] Cesium.Viewer available:`,
                typeof this.cesiumInstance.Viewer
            );
            console.log(
                `[${this.PLUGIN_ID}] Cesium keys:`,
                Object.keys(this.cesiumInstance).slice(0, 10)
            );

            // Configure Cesium Ion access token if available
            const cesiumIonToken = process.env.REACT_APP_CESIUM_ION_TOKEN;
            if (cesiumIonToken) {
                this.cesiumInstance.Ion.defaultAccessToken = cesiumIonToken;
                console.log(`[${this.PLUGIN_ID}] Cesium Ion token configured`);
            }

            return this.cesiumInstance;
        } catch (error) {
            console.error(`[${this.PLUGIN_ID}] Failed to load Cesium:`, error);

            // Reset state on failure
            this.cesiumInstance = null;

            // Provide user-friendly error message
            const errorMessage = error instanceof Error ? error.message : "Unknown error";
            throw new Error(`Failed to load Cesium: ${errorMessage}`);
        }
    }

    /**
     * Load Cesium from bundled assets in public folder
     */
    private static async loadCesiumFromAssets(): Promise<void> {
        // Load CSS first using StylesheetManager
        const stylesheets = ["/viewers/cesium/Widgets/widgets.css"];

        for (const stylesheet of stylesheets) {
            try {
                await StylesheetManager.loadStylesheet(this.PLUGIN_ID, stylesheet);
            } catch (error) {
                console.warn(`[${this.PLUGIN_ID}] Failed to load stylesheet ${stylesheet}:`, error);
                // Continue loading even if stylesheet fails
            }
        }

        // Then load the bundled JavaScript
        const scripts = ["/viewers/cesium/Cesium.js"];

        for (const script of scripts) {
            await this.loadScript(script);
        }
    }

    /**
     * Load a script dynamically
     */
    private static loadScript(src: string): Promise<void> {
        return new Promise((resolve, reject) => {
            if (this.loadedDependencies.has(src)) {
                resolve(); // Already loaded
                return;
            }

            if (document.querySelector(`script[src="${src}"]`)) {
                this.loadedDependencies.add(src);
                resolve(); // Already in DOM
                return;
            }

            const script = document.createElement("script");
            script.src = src;
            script.onload = () => {
                this.loadedDependencies.add(src);
                console.log(`[${this.PLUGIN_ID}] Loaded script: ${src}`);
                resolve();
            };
            script.onerror = () => reject(new Error(`Failed to load script: ${src}`));
            document.head.appendChild(script);
        });
    }

    /**
     * Cleanup Cesium resources and reset state
     */
    static cleanup(): void {
        try {
            console.log(`[${this.PLUGIN_ID}] Cleaning up Cesium resources...`);

            // Remove all stylesheets managed by this plugin
            StylesheetManager.removePluginStylesheets(this.PLUGIN_ID);

            // Clear loaded dependencies tracking
            this.loadedDependencies.clear();

            // Reset state
            this.cesiumInstance = null;
            this.loadPromise = null;

            console.log(`[${this.PLUGIN_ID}] Cesium cleanup completed`);
        } catch (error) {
            console.error(`[${this.PLUGIN_ID}] Error during cleanup:`, error);
        }
    }

    /**
     * Check if Cesium is currently loaded
     */
    static isLoaded(): boolean {
        return this.cesiumInstance !== null;
    }

    /**
     * Get the loaded Cesium module (if available)
     */
    static getCesium(): any | null {
        return this.isLoaded() ? this.cesiumInstance : null;
    }

    /**
     * Get plugin information
     */
    static getPluginInfo() {
        return {
            id: this.PLUGIN_ID,
            loaded: this.cesiumInstance !== null,
            hasModule: this.cesiumInstance !== null,
            loadedScripts: Array.from(this.loadedDependencies),
            baseUrl: this.cesiumBaseUrl,
        };
    }
}

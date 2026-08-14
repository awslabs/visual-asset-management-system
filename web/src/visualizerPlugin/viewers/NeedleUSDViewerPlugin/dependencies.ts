/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { StylesheetManager } from "../../core/StylesheetManager";
import { loadExternalScript } from "../../core/loadExternalScript";

// Export to make this a module for TypeScript
export {};

export class NeedleUSDDependencyManager {
    private static usdBundle: any = null;
    private static loadedDependencies = new Set<string>();
    private static readonly PLUGIN_ID = "needletools-usd-viewer";
    private static isLoaded = false;
    private static loadPromise: Promise<void> | null = null;

    /**
     * Load the USD viewer library dynamically
     */
    static async loadUSDViewer(): Promise<void> {
        // Check if already loaded and bundle is present. The bundle is captured into a static field
        // and survives cleanup, so this also covers reopening the viewer after an unmount.
        if (this.usdBundle) {
            this.isLoaded = true;
            console.log("NeedleUSDViewer: Already loaded, reusing existing bundle");
            this.restoreGlobalsFromBundle();
            return;
        }

        // A load is already running. Join it rather than starting a second one. This previously
        // removed the script tags of an in-flight load to "force a reload"; removing an in-flight
        // script fires no error event, so the first caller's promise never settled and its viewer
        // waited forever. Script-level deduplication lives in loadExternalScript, but the bundle
        // capture below also has to happen exactly once per load.
        if (this.loadPromise) {
            return this.loadPromise;
        }

        console.log("Loading Needle USD viewer library...");
        this.loadPromise = this.loadUSDViewerFromAssets();

        try {
            await this.loadPromise;
        } finally {
            // Released once settled, so the promise is shared only by callers overlapping in time and
            // a later attempt can retry after a failure.
            this.loadPromise = null;
        }
    }

    /**
     * Restore global references from the bundle
     */
    private static restoreGlobalsFromBundle(): void {
        if (!this.usdBundle) {
            console.warn("NeedleUSDViewer: Cannot restore globals - bundle not available");
            return;
        }

        if (this.usdBundle.THREE) {
            (window as any).THREE = this.usdBundle.THREE;
        }
        if (this.usdBundle.ThreeRenderDelegateInterface) {
            (window as any).ThreeRenderDelegateInterface =
                this.usdBundle.ThreeRenderDelegateInterface;
        }
        if (this.usdBundle.getUsdModule) {
            (globalThis as any)["NEEDLE:USD:GET"] = this.usdBundle.getUsdModule;
        }

        console.log("NeedleUSDViewer: Globals restored from bundle");
    }

    /**
     * Load USD viewer scripts and populate bundle
     */
    private static async loadUSDViewerFromAssets(): Promise<void> {
        // Load the bundled USD viewer (includes Three.js and ThreeRenderDelegateInterface)
        await this.loadScript("/viewers/needletools_usd_viewer/usd-viewer-bundle.js");
        console.log("NeedleUSDViewer: Bundled USD viewer loaded (includes patched Three.js)");

        // Load USD WASM bindings
        await this.loadScript("/viewers/needletools_usd_viewer/emHdBindings.js");
        console.log("NeedleUSDViewer: USD WASM bindings loaded");

        // Capture globals into bundle immediately after loading
        const THREE = (window as any).THREE;
        const ThreeRenderDelegateInterface = (window as any).ThreeRenderDelegateInterface;
        const getUsdModule = (globalThis as any)["NEEDLE:USD:GET"];

        if (!THREE || !ThreeRenderDelegateInterface || !getUsdModule) {
            throw new Error("USD Viewer dependencies failed to load properly");
        }

        // Store in persistent bundle (survives cleanup)
        this.usdBundle = {
            THREE,
            ThreeRenderDelegateInterface,
            getUsdModule,
        };

        this.isLoaded = true;

        console.log("NeedleUSDViewer: Bundle created and stored");

        // Verify USD-specific extensions are present
        const hasOnBuild = THREE.Material && typeof THREE.Material.prototype.onBuild === "function";
        if (hasOnBuild) {
            console.log("NeedleUSDViewer: THREE.js has USD extensions (onBuild found)");
        } else {
            console.warn("NeedleUSDViewer: THREE.js may be missing USD extensions");
        }
    }

    /**
     * Load a script dynamically
     */
    private static async loadScript(src: string, asModule = false): Promise<void> {
        await loadExternalScript(src, { asModule });
        this.loadedDependencies.add(src);
    }

    /**
     * Check if USD viewer is loaded
     */
    static isUSDViewerLoaded(): boolean {
        return this.isLoaded && !!this.usdBundle;
    }

    /**
     * Get the USD bundle
     */
    static getUSDBundle(): any {
        if (!this.isUSDViewerLoaded()) {
            throw new Error("USD Viewer library not loaded");
        }
        return this.usdBundle;
    }

    /**
     * Cleanup (clears globals but keeps bundle for reuse)
     */
    static cleanup(): void {
        // Remove all stylesheets managed by this plugin
        StylesheetManager.removePluginStylesheets(this.PLUGIN_ID);

        // Note: We keep the bundle and isLoaded flag for reuse
        // Only the global references are cleared by the component
        console.log("NeedleUSDDependencyManager: Cleanup completed (bundle preserved for reuse)");
    }
}

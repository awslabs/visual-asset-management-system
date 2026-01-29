/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { StylesheetManager } from "../../core/StylesheetManager";

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
        // Check if already loaded and bundle is present
        if (this.isLoaded && this.usdBundle) {
            console.log("NeedleUSDViewer: Already loaded, reusing existing bundle");
            this.restoreGlobalsFromBundle();
            return Promise.resolve();
        }

        console.log("Loading Needle USD viewer library...");

        // Check if scripts already exist in DOM
        const bundleScript = document.querySelector(
            'script[src="/viewers/needletools_usd_viewer/usd-viewer-bundle.js"]'
        ) as HTMLScriptElement;
        const wasmScript = document.querySelector(
            'script[src="/viewers/needletools_usd_viewer/emHdBindings.js"]'
        ) as HTMLScriptElement;

        if (bundleScript && wasmScript && this.usdBundle) {
            // Scripts loaded and bundle available, just restore globals
            this.isLoaded = true;
            this.restoreGlobalsFromBundle();
            console.log("NeedleUSDViewer: Restored globals from existing bundle");
            return Promise.resolve();
        }

        if ((bundleScript || wasmScript) && !this.usdBundle) {
            // Scripts exist but bundle not loaded - remove and reload
            console.log(
                "NeedleUSDViewer: Scripts exist but bundle not loaded, removing to force reload..."
            );
            bundleScript?.remove();
            wasmScript?.remove();
            this.loadedDependencies.clear();
        }

        // Create new load promise
        this.loadPromise = this.loadUSDViewerFromAssets();
        return this.loadPromise;
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
    private static loadScript(src: string, asModule: boolean = false): Promise<void> {
        return new Promise((resolve, reject) => {
            // Check if script is in DOM
            const existingScript = document.querySelector(
                `script[src="${src}"]`
            ) as HTMLScriptElement;

            if (existingScript && this.loadedDependencies.has(src)) {
                // Script already loaded successfully
                console.log(`NeedleUSDViewer: Script ${src} already loaded`);
                resolve();
                return;
            }

            if (existingScript) {
                // Script exists but may still be loading - wait for it
                existingScript.addEventListener(
                    "load",
                    () => {
                        this.loadedDependencies.add(src);
                        resolve();
                    },
                    { once: true }
                );

                existingScript.addEventListener(
                    "error",
                    () => {
                        reject(new Error(`Failed to load script: ${src}`));
                    },
                    { once: true }
                );

                return;
            }

            // Script not in DOM, create it
            const script = document.createElement("script");
            script.src = src;

            if (asModule) {
                script.type = "module";
            } else {
                script.async = true;
            }

            script.onload = () => {
                this.loadedDependencies.add(src);
                resolve();
            };
            script.onerror = () => reject(new Error(`Failed to load script: ${src}`));
            document.head.appendChild(script);
        });
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

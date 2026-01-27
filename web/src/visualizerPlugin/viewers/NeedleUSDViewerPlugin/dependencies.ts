/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { StylesheetManager } from "../../core/StylesheetManager";

// Export to make this a module for TypeScript
export {};

export class NeedleUSDDependencyManager {
    private static usdViewerInstance: any = null;
    private static loadedDependencies = new Set<string>();
    private static readonly PLUGIN_ID = "needletools-usd-viewer";
    private static threeJsLoaded = false;

    static async loadUSDViewer(): Promise<any> {
        if (this.usdViewerInstance) {
            return this.usdViewerInstance;
        }

        try {
            // Load USD viewer dependencies dynamically
            await this.loadUSDViewerFromAssets();
            this.usdViewerInstance = (window as any).USDViewer || {};
            return this.usdViewerInstance;
        } catch (error) {
            console.error("Failed to load USD Viewer:", error);
            throw error;
        }
    }

    private static async loadUSDViewerFromAssets(): Promise<void> {
        // Load the bundled USD viewer (includes Three.js and ThreeRenderDelegateInterface)
        await this.loadScript("/viewers/needletools_usd_viewer/usd-viewer-bundle.js");
        console.log("NeedleUSDViewer: Bundled USD viewer loaded (includes Three.js)");

        // Load USD WASM bindings
        await this.loadScript("/viewers/needletools_usd_viewer/emHdBindings.js");
        console.log("NeedleUSDViewer: USD WASM bindings loaded");

        // Verify that THREE and ThreeRenderDelegateInterface are available
        if ((window as any).THREE) {
            console.log("NeedleUSDViewer: THREE is available globally");
        } else {
            console.warn("NeedleUSDViewer: THREE not found in global scope");
        }

        if ((window as any).ThreeRenderDelegateInterface) {
            console.log("NeedleUSDViewer: ThreeRenderDelegateInterface is available globally");
        } else {
            console.warn("NeedleUSDViewer: ThreeRenderDelegateInterface not found in global scope");
        }
    }

    private static loadScript(src: string, asModule: boolean = false): Promise<void> {
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

    static cleanup(): void {
        // Clean up USD viewer instances and event listeners
        if (this.usdViewerInstance) {
            try {
                // USD viewer cleanup if needed
                this.usdViewerInstance = null;
            } catch (error) {
                console.warn("Error during USD Viewer cleanup:", error);
            }
        }

        // Remove all stylesheets managed by this plugin
        StylesheetManager.removePluginStylesheets(this.PLUGIN_ID);

        // Clear loaded dependencies tracking
        this.loadedDependencies.clear();

        console.log("NeedleUSDDependencyManager: Cleanup completed");
    }
}
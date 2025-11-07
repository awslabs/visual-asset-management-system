/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { StylesheetManager } from "../../core/StylesheetManager";

// Declare the global OV object that will be loaded from the script
declare const OV: any;

export class Online3dViewerDependencyManager {
    private static loaded = false;
    private static readonly PLUGIN_ID = "online3d-viewer";

    static async loadOnline3dViewer(): Promise<void> {
        if (this.loaded) return;

        try {
            // Load the Online3DViewer library dynamically from public directory
            await this.loadScript("/viewers/online3dviewer/o3dv.min.js");

            // Wait for OV to be available on window
            await this.waitForOV();

            console.log("Online3dViewer dependencies loaded successfully");
            this.loaded = true;
        } catch (error) {
            console.error("Failed to load Online3dViewer dependencies:", error);
            throw error;
        }
    }

    private static loadScript(src: string): Promise<void> {
        return new Promise((resolve, reject) => {
            // Check if script is already loaded
            const existingScript = document.querySelector(`script[src="${src}"]`);
            if (existingScript) {
                resolve();
                return;
            }

            const script = document.createElement("script");
            script.src = src;
            script.async = true;
            script.onload = () => resolve();
            script.onerror = () => reject(new Error(`Failed to load script: ${src}`));
            document.head.appendChild(script);
        });
    }

    private static waitForOV(): Promise<void> {
        return new Promise((resolve, reject) => {
            const maxAttempts = 50;
            let attempts = 0;

            const checkOV = () => {
                if (typeof (window as any).OV !== "undefined") {
                    resolve();
                } else if (attempts < maxAttempts) {
                    attempts++;
                    setTimeout(checkOV, 100);
                } else {
                    reject(new Error("Timeout waiting for OV to load"));
                }
            };

            checkOV();
        });
    }

    static cleanup(): void {
        // Remove all stylesheets managed by this plugin
        StylesheetManager.removePluginStylesheets(this.PLUGIN_ID);

        // Remove the script tag
        const script = document.querySelector('script[src="/viewers/online3dviewer/o3dv.min.js"]');
        if (script) {
            script.remove();
        }

        // Clean up the global OV object by setting to undefined instead of deleting
        // (delete is not allowed on window properties in strict mode)
        if ((window as any).OV) {
            (window as any).OV = undefined;
        }

        this.loaded = false;
        console.log("Online3dViewer dependencies cleaned up");
    }

    static isLoaded(): boolean {
        return this.loaded;
    }

    static getOV(): any {
        if (!this.loaded) {
            throw new Error("Online3dViewer not loaded. Call loadOnline3dViewer() first.");
        }
        return (window as any).OV;
    }
}

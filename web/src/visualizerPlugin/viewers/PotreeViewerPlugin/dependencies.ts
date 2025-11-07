/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { StylesheetManager } from "../../core/StylesheetManager";

export class PotreeDependencyManager {
    private static potreeInstance: any = null;
    private static loadedDependencies = new Set<string>();
    private static readonly PLUGIN_ID = "potree-viewer";

    static async loadPotree(): Promise<any> {
        if (this.potreeInstance) {
            return this.potreeInstance;
        }

        try {
            // Load Potree dependencies dynamically only when needed
            await this.loadPotreeFromAssets();
            this.potreeInstance = (window as any).Potree;
            return this.potreeInstance;
        } catch (error) {
            console.error("Failed to load Potree:", error);
            throw error;
        }
    }

    private static async loadPotreeFromAssets(): Promise<void> {
        // Load only the essential Potree files dynamically
        const scripts = [
            "/viewers/potree_libs/jquery/jquery-3.1.1.min.js",
            "/viewers/potree_libs/spectrum/spectrum.js",
            "/viewers/potree_libs/jquery-ui/jquery-ui.min.js",
            "/viewers/potree_libs/other/BinaryHeap.js",
            "/viewers/potree_libs/tween/tween.min.js",
            "/viewers/potree_libs/d3/d3.js",
            "/viewers/potree_libs/proj4/proj4.js",
            "/viewers/potree_libs/openlayers3/ol.js",
            "/viewers/potree_libs/i18next/i18next.js",
            "/viewers/potree_libs/jstree/jstree.js",
            "/viewers/potree_libs/potree/potree.js",
            "/viewers/potree_libs/plasio/js/laslaz.js",
        ];

        const stylesheets = [
            "/viewers/potree_libs/potree/potree.css",
            "/viewers/potree_libs/jquery-ui/jquery-ui.min.css",
            "/viewers/potree_libs/openlayers3/ol.css",
            "/viewers/potree_libs/spectrum/spectrum.css",
            "/viewers/potree_libs/jstree/themes/mixed/style.css",
        ];

        // Load stylesheets first using StylesheetManager
        for (const stylesheet of stylesheets) {
            await StylesheetManager.loadStylesheet(this.PLUGIN_ID, stylesheet);
        }

        // Then load scripts in order
        for (const script of scripts) {
            await this.loadScript(script);
        }
    }

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
                resolve();
            };
            script.onerror = () => reject(new Error(`Failed to load script: ${src}`));
            document.head.appendChild(script);
        });
    }

    static cleanup(): void {
        // Clean up Potree instances and event listeners
        if (this.potreeInstance) {
            // Remove any global event listeners or cleanup Potree state
            try {
                // Potree cleanup if needed
                this.potreeInstance = null;
            } catch (error) {
                console.warn("Error during Potree cleanup:", error);
            }
        }

        // Remove all stylesheets managed by this plugin
        StylesheetManager.removePluginStylesheets(this.PLUGIN_ID);

        // Clear loaded dependencies tracking
        this.loadedDependencies.clear();

        console.log("PotreeDependencyManager: Cleanup completed");
    }
}

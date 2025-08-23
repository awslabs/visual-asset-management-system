/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export class PotreeDependencyManager {
    private static potreeInstance: any = null;
    private static loadedDependencies = new Set<string>();

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
            "/potree_libs/jquery/jquery-3.1.1.min.js",
            "/potree_libs/spectrum/spectrum.js",
            "/potree_libs/jquery-ui/jquery-ui.min.js",
            "/potree_libs/other/BinaryHeap.js",
            "/potree_libs/tween/tween.min.js",
            "/potree_libs/d3/d3.js",
            "/potree_libs/proj4/proj4.js",
            "/potree_libs/openlayers3/ol.js",
            "/potree_libs/i18next/i18next.js",
            "/potree_libs/jstree/jstree.js",
            "/potree_libs/potree/potree.js",
            "/potree_libs/plasio/js/laslaz.js",
        ];

        const stylesheets = [
            "/potree_libs/potree/potree.css",
            "/potree_libs/jquery-ui/jquery-ui.min.css",
            "/potree_libs/openlayers3/ol.css",
            "/potree_libs/spectrum/spectrum.css",
            "/potree_libs/jstree/themes/mixed/style.css",
        ];

        // Load stylesheets first
        for (const stylesheet of stylesheets) {
            await this.loadStylesheet(stylesheet);
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

    private static loadStylesheet(href: string): Promise<void> {
        return new Promise((resolve, reject) => {
            if (this.loadedDependencies.has(href)) {
                resolve(); // Already loaded
                return;
            }

            if (document.querySelector(`link[href="${href}"]`)) {
                this.loadedDependencies.add(href);
                resolve(); // Already in DOM
                return;
            }

            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = href;
            link.onload = () => {
                this.loadedDependencies.add(href);
                resolve();
            };
            link.onerror = () => reject(new Error(`Failed to load stylesheet: ${href}`));
            document.head.appendChild(link);
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
    }
}

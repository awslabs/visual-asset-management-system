/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * ThreeJS Dependency Manager
 * Handles dynamic loading of the ThreeJS viewer library bundle
 */
export class ThreeJSDependencyManager {
    private static isLoaded = false;
    private static loadPromise: Promise<void> | null = null;

    /**
     * Load the ThreeJS viewer library dynamically
     */
    static async loadThreeJS(): Promise<void> {
        // Check if already loaded and globals are present
        if (this.isLoaded && (window as any).THREEBundle && (window as any).THREE) {
            console.log("ThreeJS: Already loaded, reusing existing instance");
            return Promise.resolve();
        }

        console.log("Loading ThreeJS viewer library...");

        // Check if script already exists in DOM
        let script = document.querySelector(
            'script[src="/viewers/threejs/threejs.min.js"]'
        ) as HTMLScriptElement;

        if (script && (window as any).THREEBundle) {
            // Script loaded and bundle available, just restore globals
            this.isLoaded = true;
            if ((window as any).THREEBundle.THREE) {
                (window as any).THREE = (window as any).THREEBundle.THREE;
            }
            console.log("ThreeJS: Restored globals from existing bundle");
            return Promise.resolve();
        }

        if (script && !(window as any).THREEBundle) {
            // Script exists but bundle not loaded - remove and reload
            console.log(
                "ThreeJS: Script exists but bundle not loaded, removing to force reload..."
            );
            script.remove();
            script = null as any;
        }

        // Create new load promise
        this.loadPromise = new Promise<void>((resolve, reject) => {
            // Create new script element
            const newScript = document.createElement("script");
            newScript.src = "/viewers/threejs/threejs.min.js";
            newScript.async = true;

            newScript.onload = () => {
                console.log("ThreeJS viewer library loaded successfully");

                if ((window as any).THREEBundle) {
                    this.isLoaded = true;

                    if ((window as any).THREEBundle.THREE) {
                        (window as any).THREE = (window as any).THREEBundle.THREE;
                        console.log("ThreeJS: Clean THREE.js now available globally");
                    }

                    resolve();
                } else {
                    const error = new Error("ThreeJS bundle loaded but not found in window object");
                    console.error(error);
                    reject(error);
                }
            };

            newScript.onerror = (error) => {
                const errorMsg = "Failed to load ThreeJS viewer library";
                console.error(errorMsg, error);
                reject(new Error(errorMsg));
            };

            document.head.appendChild(newScript);
        });

        return this.loadPromise;
    }

    /**
     * Check if ThreeJS is loaded
     */
    static isThreeJSLoaded(): boolean {
        return this.isLoaded && !!(window as any).THREEBundle;
    }

    /**
     * Get the ThreeJS bundle
     */
    static getThreeJSBundle(): any {
        if (!this.isThreeJSLoaded()) {
            throw new Error("ThreeJS viewer library not loaded");
        }
        return (window as any).THREEBundle;
    }

    /**
     * Cleanup (currently no-op, but kept for consistency with other viewers)
     */
    static cleanup(): void {
        // ThreeJS library remains loaded for potential reuse
        console.log("ThreeJS viewer cleanup called (library remains loaded)");
    }
}

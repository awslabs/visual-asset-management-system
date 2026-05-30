/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * That Open Engine (web-ifc) Dependency Manager
 * Handles dynamic loading of the self-contained IFC/BIM viewer UMD bundle
 * (window.ThatOpenWebIfcBundle), served from /viewers/thatopenwebifc/.
 *
 * Mirrors ThreeJSDependencyManager: a <script> tag is injected on demand and
 * the library is exposed as a window global. The viewer component reads the
 * global rather than importing the ESM packages, keeping all third-party
 * dependencies out of the core web build.
 */
export class ThatOpenWebIfcDependencyManager {
    private static isLoaded = false;
    private static loadPromise: Promise<void> | null = null;

    private static readonly SCRIPT_SRC = "/viewers/thatopenwebifc/thatopenwebifc.min.js";

    /**
     * Load the That Open Engine IFC/BIM viewer bundle dynamically.
     */
    static async loadThatOpenWebIfc(): Promise<void> {
        // Check if already loaded and the global is present.
        if (this.isLoaded && (window as any).ThatOpenWebIfcBundle) {
            console.log("ThatOpenWebIfc: Already loaded, reusing existing instance");
            return Promise.resolve();
        }

        console.log("Loading That Open Engine IFC/BIM viewer library...");

        // Check if the script already exists in the DOM.
        let script = document.querySelector(
            `script[src="${this.SCRIPT_SRC}"]`
        ) as HTMLScriptElement;

        if (script && (window as any).ThatOpenWebIfcBundle) {
            // Script loaded and bundle available.
            this.isLoaded = true;
            console.log("ThatOpenWebIfc: Restored from existing bundle");
            return Promise.resolve();
        }

        if (script && !(window as any).ThatOpenWebIfcBundle) {
            // Script exists but bundle not loaded - remove and reload.
            console.log(
                "ThatOpenWebIfc: Script exists but bundle not loaded, removing to force reload..."
            );
            script.remove();
            script = null as any;
        }

        // Create a new load promise.
        this.loadPromise = new Promise<void>((resolve, reject) => {
            const newScript = document.createElement("script");
            newScript.src = this.SCRIPT_SRC;
            newScript.async = true;

            newScript.onload = () => {
                console.log("That Open Engine IFC/BIM viewer library loaded successfully");

                if ((window as any).ThatOpenWebIfcBundle) {
                    this.isLoaded = true;
                    resolve();
                } else {
                    const error = new Error(
                        "That Open Engine bundle loaded but not found in window object"
                    );
                    console.error(error);
                    reject(error);
                }
            };

            newScript.onerror = (error) => {
                const errorMsg = "Failed to load That Open Engine IFC/BIM viewer library";
                console.error(errorMsg, error);
                reject(new Error(errorMsg));
            };

            document.head.appendChild(newScript);
        });

        return this.loadPromise;
    }

    /**
     * Check if the bundle is loaded.
     */
    static isThatOpenWebIfcLoaded(): boolean {
        return this.isLoaded && !!(window as any).ThatOpenWebIfcBundle;
    }

    /**
     * Get the loaded bundle ({ THREE, OBC, OBF, FRAGS, WEBIFC, unzipSync }).
     */
    static getBundle(): any {
        if (!this.isThatOpenWebIfcLoaded()) {
            throw new Error("That Open Engine IFC/BIM viewer library not loaded");
        }
        return (window as any).ThatOpenWebIfcBundle;
    }

    /**
     * Cleanup. The library remains loaded for potential reuse (matching the
     * Three.js viewer); per-model disposal is handled by the component via
     * components.dispose().
     */
    static cleanup(): void {
        console.log("ThatOpenWebIfc viewer cleanup called (library remains loaded)");
    }
}

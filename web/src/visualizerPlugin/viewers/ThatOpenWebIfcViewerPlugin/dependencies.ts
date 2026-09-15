/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { loadExternalScript } from "../../core/loadExternalScript";

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

    private static readonly SCRIPT_SRC = "/viewers/thatopenwebifc/thatopenwebifc.min.js";

    /**
     * Load the That Open Engine IFC/BIM viewer bundle dynamically.
     * Safe to call concurrently and repeatedly — overlapping callers share a single load.
     */
    static async loadThatOpenWebIfc(): Promise<void> {
        // Already usable.
        if ((window as any).ThatOpenWebIfcBundle) {
            this.isLoaded = true;
            return;
        }

        console.log("Loading That Open Engine IFC/BIM viewer library...");
        // isReady keeps a tag that already executed from short-circuiting if the global has since been
        // dropped; the bundle has to actually re-execute to redefine it.
        await loadExternalScript(this.SCRIPT_SRC, {
            isReady: () => !!(window as any).ThatOpenWebIfcBundle,
        });

        if (!(window as any).ThatOpenWebIfcBundle) {
            throw new Error("That Open Engine bundle loaded but not found in window object");
        }

        this.isLoaded = true;
        console.log("That Open Engine IFC/BIM viewer library loaded successfully");
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

/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { loadExternalScript } from "../../core/loadExternalScript";

/** The self-hosted bundle. Several viewers ship their own Three.js; this one is the Three.js viewer's. */
const BUNDLE_SRC = "/viewers/threejs/threejs.min.js";

/**
 * ThreeJS Dependency Manager
 * Handles dynamic loading of the ThreeJS viewer library bundle.
 *
 * Concurrent callers share ONE in-flight load. Two viewers mounting at once — or one viewer
 * remounting while its first load is still downloading — used to each run the full routine: the
 * second call found the first call's script tag in the DOM, saw `window.THREEBundle` still undefined
 * because the download had not finished, and removed that script to "force a reload". Removing an
 * in-flight script does not reliably fire `onerror`, so the first caller's promise never settled and
 * its viewer sat on "Processing…" indefinitely. The promise is now reused rather than the script
 * being torn out from under it, and a script tag is only ever discarded when no load is in flight.
 */
export class ThreeJSDependencyManager {
    private static isLoaded = false;
    private static loadPromise: Promise<void> | null = null;

    /** True when the bundle is present and usable. */
    private static bundleAvailable(): boolean {
        return !!(window as any).THREEBundle;
    }

    /** Publish the bundle's Three.js on window, which the viewer code reads. */
    private static exposeGlobals(): void {
        const bundle = (window as any).THREEBundle;
        if (bundle?.THREE) {
            (window as any).THREE = bundle.THREE;
        }
    }

    /**
     * Load the ThreeJS viewer library dynamically.
     * Safe to call concurrently and repeatedly — callers share a single load.
     */
    static async loadThreeJS(): Promise<void> {
        // Already usable: make sure the globals are published and return.
        if (this.bundleAvailable()) {
            this.isLoaded = true;
            this.exposeGlobals();
            return;
        }

        // A load is already running. Join it instead of starting a second one — this is what keeps a
        // concurrent caller from removing the in-flight script.
        if (this.loadPromise) {
            return this.loadPromise;
        }

        this.loadPromise = this.injectBundle();

        try {
            await this.loadPromise;
        } finally {
            // Released once the load settles, so the promise is only ever shared by callers that
            // overlap in time. Holding a settled promise here would break reloading: the viewer
            // deletes window.THREE and window.THREEBundle when it unmounts, so opening a second file
            // finds no bundle, and a retained promise would resolve instantly without re-injecting —
            // leaving the caller to fail on "ThreeJS dependencies not loaded". Concurrent callers
            // already hold this exact promise, so clearing it does not reintroduce the load race.
            this.loadPromise = null;
        }
    }

    /** Loads the bundle and publishes its globals. */
    private static async injectBundle(): Promise<void> {
        // isReady keeps a tag that already executed from short-circuiting once the viewer has deleted
        // the globals on unmount — without it the load would resolve without re-executing the bundle.
        await loadExternalScript(BUNDLE_SRC, { isReady: () => this.bundleAvailable() });

        if (!this.bundleAvailable()) {
            throw new Error("ThreeJS bundle loaded but not found in window object");
        }

        this.isLoaded = true;
        this.exposeGlobals();
    }

    /**
     * Check if ThreeJS is loaded
     */
    static isThreeJSLoaded(): boolean {
        return this.isLoaded && this.bundleAvailable();
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

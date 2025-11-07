/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Dependency manager for BabylonJS Gaussian Splat Viewer
 * Handles loading and cleanup of BabylonJS dependencies via dynamic script loading
 */
export class BabylonJSGaussianSplatDependencyManager {
    private static readonly PLUGIN_ID = "babylonjs-gaussian-splat-viewer";
    private static loaded = false;
    private static babylonEngine: any = null;
    private static activeEngines: Set<any> = new Set();

    /**
     * Load BabylonJS dependencies dynamically from public directory
     * @returns Promise that resolves to BabylonJS module
     */
    static async loadBabylonJS(): Promise<any> {
        if (this.loaded && this.babylonEngine) {
            console.log("BabylonJS already loaded for BabylonJS Gaussian Splat viewer");
            return this.babylonEngine;
        }

        try {
            console.log("Loading BabylonJS for BabylonJS Gaussian Splat viewer...");

            // Load the BabylonJS bundle dynamically from public directory
            await this.loadScript("/viewers/babylonjs/babylonjs.bundle.js");

            // Wait for BABYLON to be available on window
            await this.waitForBABYLON();

            // Get BABYLON from window
            const BABYLON = (window as any).BABYLON;
            if (!BABYLON) {
                throw new Error("BABYLON library not loaded");
            }

            this.babylonEngine = BABYLON;
            this.loaded = true;

            console.log("BabylonJS loaded successfully for BabylonJS Gaussian Splat viewer");
            return BABYLON;
        } catch (error) {
            console.error("Failed to load BabylonJS:", error);
            this.loaded = false;
            this.babylonEngine = null;
            throw new Error(
                `Failed to load required dependencies for BabylonJS Gaussian Splat viewer: ${
                    error instanceof Error ? error.message : "Unknown error"
                }`
            );
        }
    }

    /**
     * Load a script dynamically
     * @param src - Script source URL
     */
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

    /**
     * Wait for BABYLON to be available on window
     */
    private static waitForBABYLON(): Promise<void> {
        return new Promise((resolve, reject) => {
            const maxAttempts = 50;
            let attempts = 0;

            const checkBABYLON = () => {
                if (typeof (window as any).BABYLON !== "undefined") {
                    resolve();
                } else if (attempts < maxAttempts) {
                    attempts++;
                    setTimeout(checkBABYLON, 100);
                } else {
                    reject(new Error("Timeout waiting for BABYLON to load"));
                }
            };

            checkBABYLON();
        });
    }

    /**
     * Register a BabylonJS engine instance for cleanup tracking
     * @param engine - BabylonJS engine instance
     */
    static registerEngine(engine: any): void {
        this.activeEngines.add(engine);
        console.log(`Registered BabylonJS engine, total active: ${this.activeEngines.size}`);
    }

    /**
     * Unregister a BabylonJS engine instance
     * @param engine - BabylonJS engine instance
     */
    static unregisterEngine(engine: any): void {
        this.activeEngines.delete(engine);
        console.log(`Unregistered BabylonJS engine, total active: ${this.activeEngines.size}`);
    }

    /**
     * Cleanup all resources and dependencies
     */
    static cleanup(): void {
        console.log("Cleaning up BabylonJS Gaussian Splat viewer dependencies...");

        // Dispose all active engines
        this.activeEngines.forEach((engine) => {
            try {
                if (engine && typeof engine.dispose === "function") {
                    engine.dispose();
                }
            } catch (error) {
                console.warn("Error disposing BabylonJS engine:", error);
            }
        });

        this.activeEngines.clear();

        // Remove the script tag
        const script = document.querySelector(
            'script[src="/viewers/babylonjs/babylonjs.bundle.js"]'
        );
        if (script) {
            script.remove();
        }

        // Clean up the global BABYLON object by setting to undefined instead of deleting
        // (delete is not allowed on window properties in strict mode)
        if ((window as any).BABYLON) {
            (window as any).BABYLON = undefined;
        }

        // Reset state
        this.loaded = false;
        this.babylonEngine = null;

        console.log("BabylonJS Gaussian Splat viewer dependencies cleaned up");
    }

    /**
     * Get current dependency status
     * @returns Dependency status information
     */
    static getStatus(): {
        loaded: boolean;
        activeEngines: number;
        babylonAvailable: boolean;
    } {
        return {
            loaded: this.loaded,
            activeEngines: this.activeEngines.size,
            babylonAvailable: this.babylonEngine !== null,
        };
    }
}

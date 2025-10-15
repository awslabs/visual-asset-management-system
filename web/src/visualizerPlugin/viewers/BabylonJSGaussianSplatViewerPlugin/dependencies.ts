/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Dependency manager for BabylonJS Gaussian Splat Viewer
 * Handles loading and cleanup of BabylonJS dependencies
 */
export class BabylonJSGaussianSplatDependencyManager {
    private static readonly PLUGIN_ID = "babylonjs-gaussian-splat-viewer";
    private static loaded = false;
    private static babylonEngine: any = null;
    private static activeEngines: Set<any> = new Set();

    /**
     * Load BabylonJS dependencies
     * @returns Promise that resolves to BabylonJS module
     */
    static async loadBabylonJS(): Promise<any> {
        if (this.loaded && this.babylonEngine) {
            console.log("BabylonJS already loaded for BabylonJS Gaussian Splat viewer");
            return this.babylonEngine;
        }

        try {
            console.log("Loading BabylonJS for BabylonJS Gaussian Splat viewer...");

            // Dynamic import of BabylonJS with proper error handling
            const BABYLON = await import("babylonjs").catch(() => {
                throw new Error("BabylonJS package not found. Please install babylonjs package.");
            });

            // Load additional modules
            await import("babylonjs-loaders").catch(() => {
                console.warn("babylonjs-loaders not found, some features may be limited");
            });

            // Make BabylonJS available globally for the splat loader
            (window as any).BABYLON = BABYLON;

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

        // Reset state but keep BabylonJS loaded for other potential uses
        // this.loaded = false;
        // this.babylonEngine = null;

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

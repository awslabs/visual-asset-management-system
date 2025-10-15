/**
 * PlayCanvas Gaussian Splat Dependency Manager
 *
 * Handles dynamic loading and cleanup of PlayCanvas engine for Gaussian Splat viewing.
 * Follows VAMS plugin dependency management patterns.
 */

export class PlayCanvasGaussianSplatDependencyManager {
    private static readonly PLUGIN_ID = "playcanvas-gaussian-splat-viewer";
    private static loaded = false;
    private static playcanvas: any = null;
    private static loadPromise: Promise<any> | null = null;

    /**
     * Load PlayCanvas engine dynamically
     * @returns Promise that resolves to the PlayCanvas module
     */
    static async loadPlayCanvas(): Promise<any> {
        // Return existing instance if already loaded
        if (this.loaded && this.playcanvas) {
            return this.playcanvas;
        }

        // Return existing promise if loading is in progress
        if (this.loadPromise) {
            return this.loadPromise;
        }

        // Start loading PlayCanvas
        this.loadPromise = this.performLoad();

        try {
            const result = await this.loadPromise;
            this.loadPromise = null;
            return result;
        } catch (error) {
            this.loadPromise = null;
            throw error;
        }
    }

    /**
     * Perform the actual loading of PlayCanvas
     */
    private static async performLoad(): Promise<any> {
        try {
            console.log(`[${this.PLUGIN_ID}] Loading PlayCanvas engine...`);

            // Dynamic import of PlayCanvas
            const pc = await import("playcanvas");

            // Verify PlayCanvas loaded correctly
            if (!pc || typeof pc.Application !== "function") {
                throw new Error("PlayCanvas module did not load correctly");
            }

            // Store the loaded module
            this.playcanvas = pc;
            this.loaded = true;

            console.log(`[${this.PLUGIN_ID}] PlayCanvas engine loaded successfully`);
            console.log(`[${this.PLUGIN_ID}] PlayCanvas version:`, pc.version || "Unknown");

            return pc;
        } catch (error) {
            console.error(`[${this.PLUGIN_ID}] Failed to load PlayCanvas:`, error);

            // Reset state on failure
            this.loaded = false;
            this.playcanvas = null;

            // Provide user-friendly error message
            const errorMessage = error instanceof Error ? error.message : "Unknown error";
            throw new Error(`Failed to load PlayCanvas engine: ${errorMessage}`);
        }
    }

    /**
     * Cleanup PlayCanvas resources and reset state
     */
    static cleanup(): void {
        try {
            console.log(`[${this.PLUGIN_ID}] Cleaning up PlayCanvas resources...`);

            // Reset state
            this.loaded = false;
            this.playcanvas = null;
            this.loadPromise = null;

            console.log(`[${this.PLUGIN_ID}] PlayCanvas cleanup completed`);
        } catch (error) {
            console.error(`[${this.PLUGIN_ID}] Error during cleanup:`, error);
        }
    }

    /**
     * Check if PlayCanvas is currently loaded
     */
    static isLoaded(): boolean {
        return this.loaded && this.playcanvas !== null;
    }

    /**
     * Get the loaded PlayCanvas module (if available)
     */
    static getPlayCanvas(): any | null {
        return this.isLoaded() ? this.playcanvas : null;
    }

    /**
     * Get plugin information
     */
    static getPluginInfo() {
        return {
            id: this.PLUGIN_ID,
            loaded: this.loaded,
            hasModule: this.playcanvas !== null,
            version: this.playcanvas?.version || null,
        };
    }
}

export default PlayCanvasGaussianSplatDependencyManager;

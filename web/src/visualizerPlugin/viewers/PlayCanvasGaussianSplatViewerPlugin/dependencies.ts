/**
 * PlayCanvas Gaussian Splat Dependency Manager
 *
 * Handles dynamic loading and cleanup of PlayCanvas engine for Gaussian Splat viewing.
 * Follows VAMS plugin dependency management patterns with script tag loading.
 */

export class PlayCanvasGaussianSplatDependencyManager {
    private static readonly PLUGIN_ID = "playcanvas-gaussian-splat-viewer";
    private static loaded = false;
    private static playcanvas: any = null;
    private static loadPromise: Promise<any> | null = null;

    /**
     * Load PlayCanvas engine dynamically from public directory
     * @returns Promise that resolves to the PlayCanvas module
     */
    static async loadPlayCanvas(): Promise<any> {
        // Return existing instance if already loaded
        if (this.loaded && this.playcanvas) {
            console.log(`[${this.PLUGIN_ID}] PlayCanvas already loaded`);
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
     * Perform the actual loading of PlayCanvas via script tag
     */
    private static async performLoad(): Promise<any> {
        try {
            console.log(`[${this.PLUGIN_ID}] Loading PlayCanvas engine...`);

            // Load the PlayCanvas bundle dynamically from public directory
            await this.loadScript("/viewers/playcanvas/playcanvas.bundle.js");

            // Wait for pc to be available on window
            await this.waitForPC();

            // Get pc from window
            const pc = (window as any).pc;
            if (!pc) {
                throw new Error("PlayCanvas library not loaded");
            }

            // Verify PlayCanvas loaded correctly
            if (typeof pc.Application !== "function") {
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
     * Wait for pc to be available on window
     */
    private static waitForPC(): Promise<void> {
        return new Promise((resolve, reject) => {
            const maxAttempts = 50;
            let attempts = 0;

            const checkPC = () => {
                if (typeof (window as any).pc !== "undefined") {
                    resolve();
                } else if (attempts < maxAttempts) {
                    attempts++;
                    setTimeout(checkPC, 100);
                } else {
                    reject(new Error("Timeout waiting for PlayCanvas to load"));
                }
            };

            checkPC();
        });
    }

    /**
     * Cleanup PlayCanvas resources and reset state
     */
    static cleanup(): void {
        try {
            console.log(`[${this.PLUGIN_ID}] Cleaning up PlayCanvas resources...`);

            // Remove the script tag
            const script = document.querySelector(
                'script[src="/viewers/playcanvas/playcanvas.bundle.js"]'
            );
            if (script) {
                script.remove();
            }

            // Clean up the global pc object by setting to undefined instead of deleting
            // (delete is not allowed on window properties in strict mode)
            if ((window as any).pc) {
                (window as any).pc = undefined;
            }

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

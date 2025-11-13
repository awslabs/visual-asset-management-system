/**
 * VNTANA Viewer Dependency Manager
 *
 * Handles dynamic loading and cleanup of VNTANA viewer from bundled files.
 * Follows VAMS plugin dependency management patterns similar to Potree.
 */

import { StylesheetManager } from "../../core/StylesheetManager";

export class VntanaDependencyManager {
    private static vntanaInstance: any = null;
    private static loadedDependencies = new Set<string>();
    private static readonly PLUGIN_ID = "vntana-viewer";
    private static loadPromise: Promise<any> | null = null;

    /**
     * Load VNTANA viewer dynamically from bundled files
     * @returns Promise that resolves to the VNTANA viewer module
     */
    static async loadVntana(): Promise<any> {
        // Return existing instance if already loaded
        if (this.vntanaInstance) {
            return this.vntanaInstance;
        }

        // Return existing promise if loading is in progress
        if (this.loadPromise) {
            return this.loadPromise;
        }

        // Start loading VNTANA viewer
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
     * Perform the actual loading of VNTANA viewer from bundled files
     */
    private static async performLoad(): Promise<any> {
        try {
            console.log(`[${this.PLUGIN_ID}] Loading VNTANA viewer from bundle...`);

            // Load VNTANA viewer dependencies from public folder
            await this.loadVntanaFromAssets();

            // Get the viewer module from window object (exposed by UMD bundle)
            // The bundle exports an object with named exports like { VntanaViewer, VntanaHotspot, etc. }
            const vntanaModule = (window as any).VntanaViewer;

            if (!vntanaModule) {
                throw new Error(
                    "VNTANA viewer module not found on window object after loading bundle"
                );
            }

            // Store the entire module (contains VntanaViewer, VntanaHotspot, etc.)
            this.vntanaInstance = vntanaModule;

            console.log(`[${this.PLUGIN_ID}] VNTANA viewer loaded successfully from bundle`);

            // Wait a brief moment for custom elements to register
            await new Promise((resolve) => setTimeout(resolve, 100));

            // Verify the custom element is registered
            if (!customElements.get("vntana-viewer")) {
                console.warn(
                    `[${this.PLUGIN_ID}] vntana-viewer custom element not registered yet, but continuing...`
                );
            }

            return this.vntanaInstance;
        } catch (error) {
            console.error(`[${this.PLUGIN_ID}] Failed to load VNTANA viewer:`, error);

            // Reset state on failure
            this.vntanaInstance = null;

            // Provide user-friendly error message
            const errorMessage = error instanceof Error ? error.message : "Unknown error";
            throw new Error(`Failed to load VNTANA viewer: ${errorMessage}`);
        }
    }

    /**
     * Load VNTANA viewer from bundled assets in public folder
     */
    private static async loadVntanaFromAssets(): Promise<void> {
        // Load CSS first using StylesheetManager
        const stylesheets = ["/viewers/vntana/vntana-viewer.css"];

        for (const stylesheet of stylesheets) {
            await StylesheetManager.loadStylesheet(this.PLUGIN_ID, stylesheet);
        }

        // Then load the bundled JavaScript
        const scripts = ["/viewers/vntana/vntana-viewer.bundle.js"];

        for (const script of scripts) {
            await this.loadScript(script);
        }
    }

    /**
     * Load a script dynamically
     */
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
                console.log(`[${this.PLUGIN_ID}] Loaded script: ${src}`);
                resolve();
            };
            script.onerror = () => reject(new Error(`Failed to load script: ${src}`));
            document.head.appendChild(script);
        });
    }

    /**
     * Cleanup VNTANA viewer resources and reset state
     */
    static cleanup(): void {
        try {
            console.log(`[${this.PLUGIN_ID}] Cleaning up VNTANA viewer resources...`);

            // Remove all stylesheets managed by this plugin
            StylesheetManager.removePluginStylesheets(this.PLUGIN_ID);

            // Clear loaded dependencies tracking
            this.loadedDependencies.clear();

            // Reset state
            this.vntanaInstance = null;
            this.loadPromise = null;

            console.log(`[${this.PLUGIN_ID}] VNTANA viewer cleanup completed`);
        } catch (error) {
            console.error(`[${this.PLUGIN_ID}] Error during cleanup:`, error);
        }
    }

    /**
     * Check if VNTANA viewer is currently loaded
     */
    static isLoaded(): boolean {
        return this.vntanaInstance !== null;
    }

    /**
     * Get the loaded VNTANA viewer module (if available)
     */
    static getVntanaViewer(): any | null {
        return this.isLoaded() ? this.vntanaInstance : null;
    }

    /**
     * Get plugin information
     */
    static getPluginInfo() {
        return {
            id: this.PLUGIN_ID,
            loaded: this.vntanaInstance !== null,
            hasModule: this.vntanaInstance !== null,
            customElementRegistered: customElements.get("vntana-viewer") !== undefined,
            loadedScripts: Array.from(this.loadedDependencies),
        };
    }
}

export default VntanaDependencyManager;

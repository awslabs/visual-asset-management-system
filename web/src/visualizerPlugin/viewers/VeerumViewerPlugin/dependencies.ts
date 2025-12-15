/**
 * VEERUM Viewer Dependency Manager
 *
 * Handles dynamic loading and cleanup of VEERUM viewer from bundled files.
 * Follows VAMS plugin dependency management patterns similar to VNTANA.
 */

export class VeerumDependencyManager {
    private static veerumInstance: any = null;
    private static loadedDependencies = new Set<string>();
    private static readonly PLUGIN_ID = "veerum-viewer";
    private static loadPromise: Promise<any> | null = null;

    /**
     * Load VEERUM viewer dynamically from bundled files
     * @returns Promise that resolves to the VEERUM viewer module
     */
    static async loadVeerum(): Promise<any> {
        // Return existing instance if already loaded
        if (this.veerumInstance) {
            return this.veerumInstance;
        }

        // Return existing promise if loading is in progress
        if (this.loadPromise) {
            return this.loadPromise;
        }

        // Start loading VEERUM viewer
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
     * Perform the actual loading of VEERUM viewer from bundled files
     */
    private static async performLoad(): Promise<any> {
        try {
            console.log(`[${this.PLUGIN_ID}] Loading VEERUM viewer from bundle...`);

            // Load VEERUM viewer dependencies from public folder
            await this.loadVeerumFromAssets();

        // Get the viewer module from window object (exposed by UMD bundle)
        const veerumModule = (window as any).VeerumViewerModule;

        if (!veerumModule) {
            throw new Error(
                "VEERUM viewer module not found on window object after loading bundle"
            );
        }

        // Store the entire module (contains VeerumViewer, ViewerController, etc.)
        this.veerumInstance = veerumModule;

            console.log(`[${this.PLUGIN_ID}] VEERUM viewer loaded successfully from bundle`);

            // Wait a brief moment for module to initialize
            await new Promise((resolve) => setTimeout(resolve, 100));

            return this.veerumInstance;
        } catch (error) {
            console.error(`[${this.PLUGIN_ID}] Failed to load VEERUM viewer:`, error);

            // Reset state on failure
            this.veerumInstance = null;

            // Provide user-friendly error message
            const errorMessage = error instanceof Error ? error.message : "Unknown error";
            throw new Error(`Failed to load VEERUM viewer: ${errorMessage}`);
        }
    }

    /**
     * Load VEERUM viewer from bundled assets in public folder
     */
    private static async loadVeerumFromAssets(): Promise<void> {
        // Ensure React and ReactDOM are available globally
        if (typeof window !== 'undefined') {
            const React = await import('react');
            const ReactDOM = await import('react-dom');
            
            (window as any).React = React;
            (window as any).ReactDOM = ReactDOM;
            
            // The Veerum bundle needs access to React's jsx-runtime
            // We need to expose it in a way that matches the webpack externals configuration
            // The bundle expects to find it at React['jsx-runtime'] based on our externals config
            if (React && typeof React === 'object') {
                // Create a jsx-runtime object that the bundle can access
                const jsxRuntime = {
                    jsx: (React as any).createElement,
                    jsxs: (React as any).createElement,
                    Fragment: (React as any).Fragment
                };
                
                // Expose it on the React object where the bundle will look for it
                (React as any)['jsx-runtime'] = jsxRuntime;
            }
            
            // The Veerum viewer internally uses React 18's createRoot API
            // Since the host app uses React 17, we need to provide a polyfill
            // The bundle looks for it at ReactDOM.client.createRoot based on our webpack externals config
            const createRootPolyfill = function(container: HTMLElement) {
                return {
                    render: (element: any) => {
                        (ReactDOM as any).render(element, container);
                    },
                    unmount: () => {
                        (ReactDOM as any).unmountComponentAtNode(container);
                    }
                };
            };
            
            // Add createRoot to both ReactDOM and ReactDOM.client for compatibility
            if (ReactDOM) {
                console.log('[veerum-viewer] Creating React 18 createRoot polyfill for React 17');
                
                // Add to base ReactDOM
                if (!(ReactDOM as any).createRoot) {
                    (ReactDOM as any).createRoot = createRootPolyfill;
                }
                
                // Add to ReactDOM.client (where the bundle will look for it based on externals config)
                if (!(ReactDOM as any).client) {
                    (ReactDOM as any).client = {};
                }
                (ReactDOM as any).client.createRoot = createRootPolyfill;
                (ReactDOM as any).client.hydrateRoot = createRootPolyfill; // Also add hydrateRoot for completeness
            }
            
            console.log('[veerum-viewer] React, ReactDOM (with createRoot polyfill on ReactDOM.client), and jsx-runtime loaded and exposed globally');
        }

        // Load the bundled JavaScript
        const scripts = ["/viewers/veerum/veerum-viewer.bundle.js"];

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
     * Cleanup VEERUM viewer resources and reset state
     */
    static cleanup(): void {
        try {
            console.log(`[${this.PLUGIN_ID}] Cleaning up VEERUM viewer resources...`);

            // Clear loaded dependencies tracking
            this.loadedDependencies.clear();

            // Reset state
            this.veerumInstance = null;
            this.loadPromise = null;

            console.log(`[${this.PLUGIN_ID}] VEERUM viewer cleanup completed`);
        } catch (error) {
            console.error(`[${this.PLUGIN_ID}] Error during cleanup:`, error);
        }
    }

    /**
     * Check if VEERUM viewer is currently loaded
     */
    static isLoaded(): boolean {
        return this.veerumInstance !== null;
    }

    /**
     * Get the loaded VEERUM viewer module (if available)
     */
    static getVeerumViewer(): any | null {
        return this.isLoaded() ? this.veerumInstance : null;
    }

    /**
     * Get plugin information
     */
    static getPluginInfo() {
        return {
            id: this.PLUGIN_ID,
            loaded: this.veerumInstance !== null,
            hasModule: this.veerumInstance !== null,
            loadedScripts: Array.from(this.loadedDependencies),
        };
    }
}

export default VeerumDependencyManager;

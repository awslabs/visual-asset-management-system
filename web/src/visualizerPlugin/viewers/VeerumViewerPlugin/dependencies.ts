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
        if (typeof window !== "undefined") {
            const React = await import("react");
            const ReactDOM = await import("react-dom");

            // The Veerum bundle's UMD factory looks up window.React["jsx-runtime"].
            // In Vite production builds, the ESM React module object is frozen/sealed,
            // so we can't add properties to it directly. Create a writable wrapper
            // that includes all React exports plus the jsx-runtime.
            const jsxRuntime = {
                jsx: (React as any).createElement,
                jsxs: (React as any).createElement,
                Fragment: (React as any).Fragment,
            };

            const reactWithJsx = Object.create(null);
            // Copy all React exports to the writable object
            for (const key of Object.keys(React)) {
                reactWithJsx[key] = (React as any)[key];
            }
            // Also copy common React properties that may be on the prototype
            reactWithJsx.createElement = (React as any).createElement;
            reactWithJsx.Fragment = (React as any).Fragment;
            reactWithJsx.createContext = (React as any).createContext;
            reactWithJsx.useState = (React as any).useState;
            reactWithJsx.useEffect = (React as any).useEffect;
            reactWithJsx.useRef = (React as any).useRef;
            reactWithJsx.useCallback = (React as any).useCallback;
            reactWithJsx.useMemo = (React as any).useMemo;
            reactWithJsx.forwardRef = (React as any).forwardRef;
            reactWithJsx.memo = (React as any).memo;
            reactWithJsx.lazy = (React as any).lazy;
            reactWithJsx.Suspense = (React as any).Suspense;
            reactWithJsx.Children = (React as any).Children;
            reactWithJsx.cloneElement = (React as any).cloneElement;
            reactWithJsx.isValidElement = (React as any).isValidElement;
            reactWithJsx.default = (React as any).default || React;
            // Add jsx-runtime where the bundle expects it
            reactWithJsx["jsx-runtime"] = jsxRuntime;

            (window as any).React = reactWithJsx;

            // ReactDOM is also frozen in Vite production builds — create a writable proxy
            const createRootPolyfill = function (container: HTMLElement) {
                return {
                    render: (element: any) => {
                        (ReactDOM as any).render(element, container);
                    },
                    unmount: () => {
                        (ReactDOM as any).unmountComponentAtNode(container);
                    },
                };
            };

            const reactDomWithPolyfill = Object.create(null);
            for (const key of Object.keys(ReactDOM)) {
                reactDomWithPolyfill[key] = (ReactDOM as any)[key];
            }
            reactDomWithPolyfill.render = (ReactDOM as any).render;
            reactDomWithPolyfill.unmountComponentAtNode = (ReactDOM as any).unmountComponentAtNode;
            reactDomWithPolyfill.findDOMNode = (ReactDOM as any).findDOMNode;
            reactDomWithPolyfill.createPortal = (ReactDOM as any).createPortal;
            reactDomWithPolyfill.default = (ReactDOM as any).default || ReactDOM;
            reactDomWithPolyfill.createRoot = createRootPolyfill;
            reactDomWithPolyfill.client = {
                createRoot: createRootPolyfill,
                hydrateRoot: createRootPolyfill,
            };

            (window as any).ReactDOM = reactDomWithPolyfill;

            console.log(
                "[veerum-viewer] React (with jsx-runtime) and ReactDOM (with createRoot polyfill) loaded and exposed globally"
            );
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

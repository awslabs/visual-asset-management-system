/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Manages stylesheet loading and cleanup for viewer plugins
 * Provides CSS isolation by tracking and removing stylesheets when viewers are unloaded
 */
export class StylesheetManager {
    private static loadedStylesheets = new Map<string, HTMLLinkElement>();
    private static pluginStylesheets = new Map<string, Set<string>>();

    /**
     * Load a stylesheet for a specific plugin
     * @param pluginId - The plugin ID that owns this stylesheet
     * @param href - The stylesheet URL
     * @param scoped - Whether to scope the CSS to a specific container (future enhancement)
     */
    static async loadStylesheet(
        pluginId: string,
        href: string,
        scoped: boolean = false
    ): Promise<void> {
        return new Promise((resolve, reject) => {
            // Check if already loaded
            if (this.loadedStylesheets.has(href)) {
                this.addStylesheetToPlugin(pluginId, href);
                resolve();
                return;
            }

            // Check if already in DOM
            const existingLink = document.querySelector(`link[href="${href}"]`) as HTMLLinkElement;
            if (existingLink) {
                this.loadedStylesheets.set(href, existingLink);
                this.addStylesheetToPlugin(pluginId, href);
                resolve();
                return;
            }

            // Create new stylesheet
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = href;

            // Add data attributes for tracking
            link.setAttribute("data-plugin-id", pluginId);
            link.setAttribute("data-managed-by", "visualizer-plugin");

            if (scoped) {
                link.setAttribute("data-scoped", "true");
            }

            link.onload = () => {
                this.loadedStylesheets.set(href, link);
                this.addStylesheetToPlugin(pluginId, href);
                console.log(`Loaded stylesheet for plugin ${pluginId}: ${href}`);
                resolve();
            };

            link.onerror = () => {
                console.error(`Failed to load stylesheet: ${href}`);
                reject(new Error(`Failed to load stylesheet: ${href}`));
            };

            document.head.appendChild(link);
        });
    }

    /**
     * Load multiple stylesheets for a plugin
     */
    static async loadStylesheets(pluginId: string, hrefs: string[]): Promise<void> {
        const promises = hrefs.map((href) => this.loadStylesheet(pluginId, href));
        await Promise.all(promises);
    }

    /**
     * Remove all stylesheets for a specific plugin
     */
    static removePluginStylesheets(pluginId: string): void {
        const stylesheets = this.pluginStylesheets.get(pluginId);
        if (!stylesheets) return;

        // Convert Set to Array for iteration compatibility
        const stylesheetArray = Array.from(stylesheets);
        for (let i = 0; i < stylesheetArray.length; i++) {
            this.removeStylesheet(stylesheetArray[i]);
        }

        this.pluginStylesheets.delete(pluginId);
        console.log(`Removed all stylesheets for plugin: ${pluginId}`);
    }

    /**
     * Remove a specific stylesheet
     */
    static removeStylesheet(href: string): void {
        const link = this.loadedStylesheets.get(href);
        if (link && link.parentNode) {
            link.parentNode.removeChild(link);
            this.loadedStylesheets.delete(href);

            // Remove from all plugin references
            const pluginEntries = Array.from(this.pluginStylesheets.entries());
            for (let i = 0; i < pluginEntries.length; i++) {
                const [pluginId, stylesheets] = pluginEntries[i];
                stylesheets.delete(href);
                if (stylesheets.size === 0) {
                    this.pluginStylesheets.delete(pluginId);
                }
            }

            console.log(`Removed stylesheet: ${href}`);
        }
    }

    /**
     * Get all stylesheets loaded by a plugin
     */
    static getPluginStylesheets(pluginId: string): string[] {
        const stylesheets = this.pluginStylesheets.get(pluginId);
        return stylesheets ? Array.from(stylesheets) : [];
    }

    /**
     * Check if a stylesheet is loaded
     */
    static isStylesheetLoaded(href: string): boolean {
        return this.loadedStylesheets.has(href);
    }

    /**
     * Get all loaded stylesheets
     */
    static getAllLoadedStylesheets(): string[] {
        return Array.from(this.loadedStylesheets.keys());
    }

    /**
     * Clean up all managed stylesheets (for complete cleanup)
     */
    static cleanup(): void {
        // Remove all managed stylesheets
        const managedLinks = document.querySelectorAll('link[data-managed-by="visualizer-plugin"]');
        managedLinks.forEach((link) => {
            if (link.parentNode) {
                link.parentNode.removeChild(link);
            }
        });

        // Clear internal tracking
        this.loadedStylesheets.clear();
        this.pluginStylesheets.clear();

        console.log("StylesheetManager: Cleaned up all managed stylesheets");
    }

    /**
     * Add a stylesheet reference to a plugin's tracking
     */
    private static addStylesheetToPlugin(pluginId: string, href: string): void {
        if (!this.pluginStylesheets.has(pluginId)) {
            this.pluginStylesheets.set(pluginId, new Set());
        }
        this.pluginStylesheets.get(pluginId)!.add(href);
    }

    /**
     * Create a scoped CSS container class name for a plugin
     * This can be used for future CSS scoping enhancements
     */
    static getScopedClassName(pluginId: string): string {
        return `visualizer-plugin-${pluginId.replace(/[^a-zA-Z0-9-]/g, "-")}`;
    }

    /**
     * Get statistics about loaded stylesheets
     */
    static getStats(): {
        totalStylesheets: number;
        pluginCount: number;
        stylesheetsByPlugin: Record<string, number>;
    } {
        const stylesheetsByPlugin: Record<string, number> = {};

        const pluginEntries = Array.from(this.pluginStylesheets.entries());
        for (let i = 0; i < pluginEntries.length; i++) {
            const [pluginId, stylesheets] = pluginEntries[i];
            stylesheetsByPlugin[pluginId] = stylesheets.size;
        }

        return {
            totalStylesheets: this.loadedStylesheets.size,
            pluginCount: this.pluginStylesheets.size,
            stylesheetsByPlugin,
        };
    }
}

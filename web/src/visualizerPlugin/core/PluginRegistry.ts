/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ViewerPluginConfig, ViewerConfig, ViewerPluginProps } from "./types";
import viewerConfig from "../config/viewerConfig.json";
import { VIEWER_COMPONENTS, DEPENDENCY_MANAGERS } from "../viewers/manifest";
import { Cache } from "aws-amplify";
import React from "react";

export interface ViewerPlugin {
    config: ViewerPluginConfig;
    component: React.ComponentType<ViewerPluginProps>;
    dependencyManager?: any;
}

export class PluginRegistry {
    private static instance: PluginRegistry;
    private plugins: Map<string, ViewerPlugin> = new Map();
    private config!: ViewerConfig;
    private initialized = false;

    static getInstance(): PluginRegistry {
        if (!PluginRegistry.instance) {
            PluginRegistry.instance = new PluginRegistry();
        }
        return PluginRegistry.instance;
    }

    // Dynamic component loader using manifest constants
    private async loadViewerComponent(
        componentPath: string
    ): Promise<React.ComponentType<ViewerPluginProps>> {
        const relativePath = VIEWER_COMPONENTS[componentPath as keyof typeof VIEWER_COMPONENTS];

        if (!relativePath) {
            throw new Error(
                `Component path not found in manifest: ${componentPath}. Please add it to VIEWER_COMPONENTS in manifest.ts`
            );
        }

        // Use dynamic import with the relative path from manifest (already includes ./ prefix)
        const module = await import(`../viewers/${relativePath}`);
        return module.default;
    }

    // Dynamic dependency manager loader using manifest constants
    private async loadDependencyManager(dependencyPath: string): Promise<any> {
        const relativePath =
            DEPENDENCY_MANAGERS[dependencyPath as keyof typeof DEPENDENCY_MANAGERS];

        if (!relativePath) {
            console.warn(
                `Dependency manager not found in manifest: ${dependencyPath}. Add it to DEPENDENCY_MANAGERS in manifest.ts if needed.`
            );
            return null;
        }

        // Use dynamic import with the relative path from manifest (already includes ./ prefix)
        return await import(`../viewers/${relativePath}`);
    }

    async initialize(): Promise<void> {
        if (this.initialized) return;

        try {
            // Load configuration
            this.config = viewerConfig as ViewerConfig;

            // Register all plugins
            for (const viewerConfig of this.config.viewers) {
                await this.registerPlugin(viewerConfig);
            }

            this.initialized = true;
            console.log(`PluginRegistry initialized with ${this.plugins.size} plugins`);
        } catch (error) {
            console.error("Failed to initialize PluginRegistry:", error);
            throw error;
        }
    }

    // Check if plugin's feature requirements are met
    private checkFeatureRestrictions(config: ViewerPluginConfig): boolean {
        // If no feature restrictions are defined, plugin is always available
        if (!config.featuresEnabledRestriction || config.featuresEnabledRestriction.length === 0) {
            return true;
        }

        try {
            // Get the current configuration from cache (same pattern as ViewAsset.tsx)
            const appConfig = Cache.getItem("config");

            if (!appConfig || !appConfig.featuresEnabled) {
                console.warn(
                    `Plugin ${config.id} requires features but no featuresEnabled config found`
                );
                return false;
            }

            // Check if ALL required features are enabled
            const allFeaturesEnabled = config.featuresEnabledRestriction.every((requiredFeature) =>
                appConfig.featuresEnabled.includes(requiredFeature)
            );

            if (!allFeaturesEnabled) {
                const missingFeatures = config.featuresEnabledRestriction.filter(
                    (feature) => !appConfig.featuresEnabled.includes(feature)
                );
                console.log(
                    `Plugin ${config.id} (${
                        config.name
                    }) excluded due to missing features: ${missingFeatures.join(", ")}`
                );
            }

            return allFeaturesEnabled;
        } catch (error) {
            console.error(`Error checking feature restrictions for plugin ${config.id}:`, error);
            return false;
        }
    }

    async registerPlugin(config: ViewerPluginConfig): Promise<void> {
        try {
            // Check feature restrictions before attempting to load the plugin
            if (!this.checkFeatureRestrictions(config)) {
                console.log(
                    `Skipping plugin ${config.id} (${config.name}) due to unmet feature requirements`
                );
                return;
            }

            console.log(`Attempting to load component from: ${config.componentPath}`);

            // Use internal helper function to load component
            const Component = await this.loadViewerComponent(config.componentPath);

            if (!Component) {
                throw new Error(
                    `Component at ${config.componentPath} does not have a default export`
                );
            }

            // Load dependency manager if specified
            let dependencyManager = null;
            if (config.dependencyManager) {
                console.log(
                    `Attempting to load dependency manager from: ${config.dependencyManager}`
                );

                try {
                    dependencyManager = await this.loadDependencyManager(config.dependencyManager);
                } catch (error) {
                    console.warn(`Failed to load dependency manager for ${config.id}:`, error);
                }
            }

            // Create plugin object
            const plugin: ViewerPlugin = {
                config,
                component: Component,
                dependencyManager,
            };

            this.plugins.set(config.id, plugin);
            console.log(`Registered plugin: ${config.name} (${config.id})`);
        } catch (error) {
            console.error(`Failed to register plugin ${config.id}:`, error);
            // Don't throw here - continue with other plugins
        }
    }

    getCompatibleViewers(
        fileExtensions: string[],
        isMultiFile: boolean,
        isPreview: boolean = false
    ): ViewerPlugin[] {
        if (!this.initialized) {
            console.warn("PluginRegistry not initialized. Call initialize() first.");
            return [];
        }

        // For preview mode, ONLY return the preview viewer
        if (isPreview) {
            return Array.from(this.plugins.values())
                .filter((plugin) => plugin.config.isPreviewViewer)
                .sort((a, b) => a.config.priority - b.config.priority);
        }

        // For non-preview mode, return all compatible viewers EXCEPT preview viewers
        return Array.from(this.plugins.values())
            .filter((plugin) => {
                // Skip preview viewer for non-preview files
                if (plugin.config.isPreviewViewer) {
                    return false;
                }

                return this.canHandle(plugin.config, fileExtensions, isMultiFile);
            })
            .sort((a, b) => a.config.priority - b.config.priority);
    }

    private canHandle(
        config: ViewerPluginConfig,
        fileExtensions: string[],
        isMultiFile: boolean
    ): boolean {
        // Check if viewer supports multi-file when needed
        const multiFileSupport = !isMultiFile || config.supportsMultiFile;
        if (!multiFileSupport) {
            return false;
        }

        // Check if viewer supports any of the file extensions
        const extensionMatch = fileExtensions.some(
            (ext) =>
                config.supportedExtensions.includes(ext.toLowerCase()) ||
                config.supportedExtensions.includes("*") // Support wildcard for preview viewer
        );

        return extensionMatch;
    }

    getViewer(id: string): ViewerPlugin | undefined {
        return this.plugins.get(id);
    }

    // Get viewers by category
    getViewersByCategory(category: string): ViewerPlugin[] {
        return Array.from(this.plugins.values()).filter(
            (plugin) => plugin.config.category === category
        );
    }

    // Get all available categories
    getCategories(): string[] {
        const categories = new Set<string>();
        this.plugins.forEach((plugin) => categories.add(plugin.config.category));
        return Array.from(categories);
    }

    // Get all registered plugins
    getAllPlugins(): ViewerPlugin[] {
        return Array.from(this.plugins.values());
    }

    // Check if registry is initialized
    isInitialized(): boolean {
        return this.initialized;
    }

    // Load dependencies for a plugin using configuration
    async loadPluginDependencies(pluginId: string): Promise<void> {
        const plugin = this.plugins.get(pluginId);
        if (!plugin) {
            throw new Error(`Plugin ${pluginId} not found`);
        }

        // If plugin has a dependency manager, use it generically
        if (
            plugin.dependencyManager &&
            plugin.config.dependencyManagerClass &&
            plugin.config.dependencyManagerMethod
        ) {
            const depClass = plugin.dependencyManager[plugin.config.dependencyManagerClass];
            if (depClass && depClass[plugin.config.dependencyManagerMethod]) {
                await depClass[plugin.config.dependencyManagerMethod]();
            }
        } else if (plugin.dependencyManager && plugin.dependencyManager.loadDependencies) {
            // Fallback for generic loadDependencies method
            await plugin.dependencyManager.loadDependencies();
        }

        console.log(`Dependencies loaded for plugin: ${pluginId}`);
    }

    // Cleanup all plugins using configuration
    cleanup(): void {
        this.plugins.forEach((plugin) => {
            try {
                if (
                    plugin.dependencyManager &&
                    plugin.config.dependencyManagerClass &&
                    plugin.config.dependencyCleanupMethod
                ) {
                    const depClass = plugin.dependencyManager[plugin.config.dependencyManagerClass];
                    if (depClass && depClass[plugin.config.dependencyCleanupMethod]) {
                        depClass[plugin.config.dependencyCleanupMethod]();
                    }
                } else if (plugin.dependencyManager && plugin.dependencyManager.cleanup) {
                    // Fallback for generic cleanup method
                    plugin.dependencyManager.cleanup();
                }
            } catch (error) {
                console.error(`Error cleaning up plugin ${plugin.config.id}:`, error);
            }
        });
    }
}

// Utility function to get file extension from filename
export function getFileExtension(filename: string): string {
    const lastDotIndex = filename.lastIndexOf(".");
    if (lastDotIndex === -1) return "";
    return filename.substring(lastDotIndex).toLowerCase();
}

// Utility function to get all unique file extensions from multiple files
export function getFileExtensions(
    files: Array<{ filename: string; isDirectory: boolean }>
): string[] {
    if (files.length === 0) return [];

    // Filter out directories and get unique extensions
    const actualFiles = files.filter((f) => !f.isDirectory);
    const extensions = new Set<string>();

    actualFiles.forEach((file) => {
        const ext = getFileExtension(file.filename);
        if (ext) {
            extensions.add(ext);
        }
    });

    return Array.from(extensions);
}

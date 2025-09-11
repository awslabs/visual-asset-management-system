/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ViewerPluginConfig, ViewerConfig, ViewerPluginProps } from "./types";
import viewerConfig from "../config/viewerConfig.json";
import { VIEWER_COMPONENTS, DEPENDENCY_MANAGERS } from "../viewers/manifest";
import { Cache } from "aws-amplify";
import { StylesheetManager } from "./StylesheetManager";
import React from "react";

export interface ViewerPlugin {
    config: ViewerPluginConfig;
    component: React.ComponentType<ViewerPluginProps>;
    dependencyManager?: any;
    isLoaded?: boolean;
}

export interface ViewerPluginMetadata {
    config: ViewerPluginConfig;
    isLoaded: boolean;
}

export class PluginRegistry {
    private static instance: PluginRegistry;
    private plugins: Map<string, ViewerPlugin> = new Map();
    private pluginMetadata: Map<string, ViewerPluginMetadata> = new Map();
    private config!: ViewerConfig;
    private initialized = false;
    private currentlyLoadedPlugin: string | null = null;

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

            // Register plugin metadata only (no component loading)
            for (const viewerConfig of this.config.viewers) {
                await this.registerPluginMetadata(viewerConfig);
            }

            this.initialized = true;
            console.log(
                `PluginRegistry initialized with ${this.pluginMetadata.size} plugin metadata entries`
            );
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

    async registerPluginMetadata(config: ViewerPluginConfig): Promise<void> {
        try {
            // Check feature restrictions before registering metadata
            if (!this.checkFeatureRestrictions(config)) {
                console.log(
                    `Skipping plugin ${config.id} (${config.name}) due to unmet feature requirements`
                );
                return;
            }

            // Create metadata object (no component loading)
            const metadata: ViewerPluginMetadata = {
                config,
                isLoaded: false,
            };

            this.pluginMetadata.set(config.id, metadata);
            console.log(`Registered plugin metadata: ${config.name} (${config.id})`);
        } catch (error) {
            console.error(`Failed to register plugin metadata ${config.id}:`, error);
            // Don't throw here - continue with other plugins
        }
    }

    async loadPlugin(pluginId: string): Promise<ViewerPlugin> {
        // Check if already loaded
        const existingPlugin = this.plugins.get(pluginId);
        if (existingPlugin && existingPlugin.isLoaded) {
            return existingPlugin;
        }

        // Get metadata
        const metadata = this.pluginMetadata.get(pluginId);
        if (!metadata) {
            throw new Error(`Plugin metadata not found: ${pluginId}`);
        }

        const config = metadata.config;

        try {
            console.log(`Loading plugin component: ${config.name} (${pluginId})`);

            // Load component
            const Component = await this.loadViewerComponent(config.componentPath);
            if (!Component) {
                throw new Error(
                    `Component at ${config.componentPath} does not have a default export`
                );
            }

            // Load dependency manager if specified
            let dependencyManager = null;
            if (config.dependencyManager) {
                console.log(`Loading dependency manager: ${config.dependencyManager}`);
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
                isLoaded: true,
            };

            this.plugins.set(pluginId, plugin);
            metadata.isLoaded = true;

            console.log(`Successfully loaded plugin: ${config.name} (${pluginId})`);
            return plugin;
        } catch (error) {
            console.error(`Failed to load plugin ${pluginId}:`, error);
            throw error;
        }
    }

    async unloadPlugin(pluginId: string): Promise<void> {
        const plugin = this.plugins.get(pluginId);
        if (!plugin || !plugin.isLoaded) {
            return; // Already unloaded or never loaded
        }

        try {
            console.log(`Unloading plugin: ${plugin.config.name} (${pluginId})`);

            // Clean up dependencies
            if (plugin.dependencyManager) {
                if (plugin.config.dependencyManagerClass && plugin.config.dependencyCleanupMethod) {
                    const depClass = plugin.dependencyManager[plugin.config.dependencyManagerClass];
                    if (depClass && depClass[plugin.config.dependencyCleanupMethod]) {
                        await depClass[plugin.config.dependencyCleanupMethod]();
                    }
                } else if (plugin.dependencyManager.cleanup) {
                    await plugin.dependencyManager.cleanup();
                }
            }

            // Remove plugin stylesheets
            StylesheetManager.removePluginStylesheets(pluginId);

            // Remove from loaded plugins but keep metadata
            this.plugins.delete(pluginId);
            const metadata = this.pluginMetadata.get(pluginId);
            if (metadata) {
                metadata.isLoaded = false;
            }

            console.log(`Successfully unloaded plugin: ${pluginId}`);
        } catch (error) {
            console.error(`Error unloading plugin ${pluginId}:`, error);
            throw error;
        }
    }

    getCompatibleViewers(
        fileExtensions: string[],
        isMultiFile: boolean,
        isPreview: boolean = false
    ): ViewerPluginMetadata[] {
        if (!this.initialized) {
            console.warn("PluginRegistry not initialized. Call initialize() first.");
            return [];
        }

        // For preview mode, ONLY return the preview viewer metadata
        if (isPreview) {
            return Array.from(this.pluginMetadata.values())
                .filter((metadata) => metadata.config.isPreviewViewer)
                .sort((a, b) => a.config.priority - b.config.priority);
        }

        // For non-preview mode, return all compatible viewer metadata EXCEPT preview viewers
        return Array.from(this.pluginMetadata.values())
            .filter((metadata) => {
                // Skip preview viewer for non-preview files
                if (metadata.config.isPreviewViewer) {
                    return false;
                }

                return this.canHandle(metadata.config, fileExtensions, isMultiFile);
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

    getViewerMetadata(id: string): ViewerPluginMetadata | undefined {
        return this.pluginMetadata.get(id);
    }

    isPluginLoaded(id: string): boolean {
        const plugin = this.plugins.get(id);
        return plugin ? plugin.isLoaded === true : false;
    }

    // Get viewers by category (metadata only)
    getViewersByCategory(category: string): ViewerPluginMetadata[] {
        return Array.from(this.pluginMetadata.values()).filter(
            (metadata) => metadata.config.category === category
        );
    }

    // Get all available categories
    getCategories(): string[] {
        const categories = new Set<string>();
        this.pluginMetadata.forEach((metadata) => categories.add(metadata.config.category));
        return Array.from(categories);
    }

    // Get all registered plugin metadata
    getAllPluginMetadata(): ViewerPluginMetadata[] {
        return Array.from(this.pluginMetadata.values());
    }

    // Get all loaded plugins
    getAllLoadedPlugins(): ViewerPlugin[] {
        return Array.from(this.plugins.values()).filter((plugin) => plugin.isLoaded);
    }

    // Check if registry is initialized
    isInitialized(): boolean {
        return this.initialized;
    }

    // Load dependencies for a plugin using configuration
    async loadPluginDependencies(pluginId: string): Promise<void> {
        const plugin = this.plugins.get(pluginId);
        if (!plugin) {
            throw new Error(`Plugin ${pluginId} not found or not loaded`);
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

    // Switch to a different plugin (unload current, load new)
    async switchToPlugin(newPluginId: string): Promise<ViewerPlugin> {
        // Unload current plugin if different
        if (this.currentlyLoadedPlugin && this.currentlyLoadedPlugin !== newPluginId) {
            await this.unloadPlugin(this.currentlyLoadedPlugin);
        }

        // Load new plugin
        const plugin = await this.loadPlugin(newPluginId);
        this.currentlyLoadedPlugin = newPluginId;

        return plugin;
    }

    // Get currently loaded plugin ID
    getCurrentlyLoadedPlugin(): string | null {
        return this.currentlyLoadedPlugin;
    }

    // Cleanup all plugins using configuration
    cleanup(): void {
        const loadedPlugins = Array.from(this.plugins.values());
        loadedPlugins.forEach((plugin) => {
            try {
                if (plugin.isLoaded) {
                    // Use synchronous cleanup for immediate cleanup
                    this.unloadPluginSync(plugin.config.id);
                }
            } catch (error) {
                console.error(`Error cleaning up plugin ${plugin.config.id}:`, error);
            }
        });

        // Clean up all stylesheets
        StylesheetManager.cleanup();

        // Reset state
        this.currentlyLoadedPlugin = null;
        console.log("PluginRegistry: Complete cleanup performed");
    }

    // Synchronous version of unloadPlugin for cleanup scenarios
    private unloadPluginSync(pluginId: string): void {
        const plugin = this.plugins.get(pluginId);
        if (!plugin || !plugin.isLoaded) {
            return; // Already unloaded or never loaded
        }

        try {
            console.log(`Unloading plugin (sync): ${plugin.config.name} (${pluginId})`);

            // Clean up dependencies synchronously
            if (plugin.dependencyManager) {
                if (plugin.config.dependencyManagerClass && plugin.config.dependencyCleanupMethod) {
                    const depClass = plugin.dependencyManager[plugin.config.dependencyManagerClass];
                    if (depClass && depClass[plugin.config.dependencyCleanupMethod]) {
                        depClass[plugin.config.dependencyCleanupMethod]();
                    }
                } else if (plugin.dependencyManager.cleanup) {
                    plugin.dependencyManager.cleanup();
                }
            }

            // Remove plugin stylesheets
            StylesheetManager.removePluginStylesheets(pluginId);

            // Remove from loaded plugins but keep metadata
            this.plugins.delete(pluginId);
            const metadata = this.pluginMetadata.get(pluginId);
            if (metadata) {
                metadata.isLoaded = false;
            }

            console.log(`Successfully unloaded plugin (sync): ${pluginId}`);
        } catch (error) {
            console.error(`Error unloading plugin ${pluginId}:`, error);
        }
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

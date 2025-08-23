/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Core exports
export { PluginRegistry, getFileExtension, getFileExtensions } from "./core/PluginRegistry";
export type { ViewerPlugin } from "./core/PluginRegistry";
export type {
    ViewerPluginConfig,
    ViewerPluginProps,
    FileInfo,
    ViewerConfig,
    ViewerOption,
} from "./core/types";

// Component exports
export { DynamicViewer } from "./components/DynamicViewer";
export type { DynamicViewerProps } from "./components/DynamicViewer";
export { StandaloneViewer } from "./components/StandaloneViewer";
export type { StandaloneViewerProps } from "./components/StandaloneViewer";
export { ViewerSelector } from "./components/ViewerSelector";

// Initialize the plugin registry on import
import { PluginRegistry } from "./core/PluginRegistry";

// Auto-initialize the registry when the module is imported
let registryInitialized = false;

export const initializePluginRegistry = async (): Promise<void> => {
    if (!registryInitialized) {
        const registry = PluginRegistry.getInstance();
        await registry.initialize();
        registryInitialized = true;
    }
};

// Optional: Auto-initialize on import (can be disabled if needed)
if (typeof window !== "undefined") {
    // Only initialize in browser environment
    initializePluginRegistry().catch(console.error);
}

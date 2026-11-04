/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Viewer manifest for the add-on viewers: the optional add-ons' viewers (Physna) and the licensed
// viewers (VNTANA, VEERUM), which ship disabled. Same shape as manifest.ts; PluginRegistry merges
// both, and config/addonViewerConfig.json holds the matching viewer entries.

export const ADDON_VIEWER_COMPONENTS = {
    "./viewers/VntanaViewerPlugin/VntanaViewerComponent":
        "VntanaViewerPlugin/VntanaViewerComponent",
    "./viewers/VeerumViewerPlugin/VeerumViewerComponent":
        "VeerumViewerPlugin/VeerumViewerComponent",
    "./viewers/PhysnaViewerPlugin/PhysnaViewerComponent":
        "PhysnaViewerPlugin/PhysnaViewerComponent",
} as const;

export const ADDON_DEPENDENCY_MANAGERS = {
    "./viewers/VntanaViewerPlugin/dependencies": "VntanaViewerPlugin/dependencies",
    "./viewers/VeerumViewerPlugin/dependencies": "VeerumViewerPlugin/dependencies",
} as const;

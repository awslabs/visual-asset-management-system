/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Viewer manifest for the optional add-ons. Same shape as manifest.ts; PluginRegistry merges both,
// and config/addonViewerConfig.json holds the matching viewer entries.

export const ADDON_VIEWER_COMPONENTS = {
    "./viewers/PhysnaViewerPlugin/PhysnaViewerComponent":
        "PhysnaViewerPlugin/PhysnaViewerComponent",
} as const;

export const ADDON_DEPENDENCY_MANAGERS = {} as const;

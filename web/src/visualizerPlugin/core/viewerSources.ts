/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// The viewer entries and manifest paths PluginRegistry registers, composed from the core files
// (config/viewerConfig.json, viewers/manifest.ts), the add-on files (config/addonViewerConfig.json,
// viewers/addonManifest.ts) and the fork-owned files (config/customViewerConfig.json,
// viewers/custom/customManifest.ts). Kept out of PluginRegistry so it can be unit-tested: the
// registry's import.meta.glob loaders do not run under jest.

import { ViewerConfig, ViewerPluginConfig } from "./types";
import viewerConfig from "../config/viewerConfig.json";
import addonViewerConfig from "../config/addonViewerConfig.json";
import customViewerConfig from "../config/customViewerConfig.json";
import { VIEWER_COMPONENTS, DEPENDENCY_MANAGERS } from "../viewers/manifest";
import { ADDON_VIEWER_COMPONENTS, ADDON_DEPENDENCY_MANAGERS } from "../viewers/addonManifest";
import {
    CUSTOM_VIEWER_COMPONENTS,
    CUSTOM_DEPENDENCY_MANAGERS,
} from "../viewers/custom/customManifest";

/**
 * The shipped viewers followed by the custom ones. A custom entry whose id repeats an earlier
 * entry's id is skipped with a console error, so a fork's viewer can never replace a core or add-on
 * viewer (nor an earlier custom one).
 */
export function mergeViewerConfigs(
    shipped: ViewerPluginConfig[],
    custom: ViewerPluginConfig[]
): ViewerPluginConfig[] {
    const merged = [...shipped];
    const ids = new Set(shipped.map((viewer) => viewer.id));
    for (const viewer of custom) {
        if (ids.has(viewer.id)) {
            console.error(
                `Custom viewer ${viewer.id} skipped: its id is already registered by another viewer`
            );
            continue;
        }
        ids.add(viewer.id);
        merged.push(viewer);
    }
    return merged;
}

/** Manifest paths with the shipped entries taking precedence over custom ones with the same key. */
export function mergeManifests(
    shipped: Record<string, string>,
    custom: Record<string, string>
): Record<string, string> {
    return { ...custom, ...shipped };
}

/** Every viewer entry the registry registers: core, then add-on, then custom. */
export const ALL_VIEWER_CONFIGS: ViewerPluginConfig[] = mergeViewerConfigs(
    [...(viewerConfig as ViewerConfig).viewers, ...(addonViewerConfig as ViewerConfig).viewers],
    (customViewerConfig as ViewerConfig).viewers
);

/** componentPath -> path below viewers/, for core, add-on and custom viewers. */
export const ALL_VIEWER_COMPONENTS: Record<string, string> = mergeManifests(
    { ...VIEWER_COMPONENTS, ...ADDON_VIEWER_COMPONENTS },
    CUSTOM_VIEWER_COMPONENTS
);

/** dependencyManager -> path below viewers/, for core, add-on and custom viewers. */
export const ALL_DEPENDENCY_MANAGERS: Record<string, string> = mergeManifests(
    { ...DEPENDENCY_MANAGERS, ...ADDON_DEPENDENCY_MANAGERS },
    CUSTOM_DEPENDENCY_MANAGERS
);

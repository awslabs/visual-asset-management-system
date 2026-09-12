/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ViewerPluginConfig } from "./types";
import { supportsAllExtensions } from "./extensionMatching";
import { CompareContext, admitsCompareShape } from "./compareShape";

/**
 * Per-viewer admission predicates for the two selection paths `PluginRegistry.getCompatibleViewers`
 * walks. Kept free of the registry (which pulls in Vite's `import.meta.glob`) so both paths can be
 * unit-tested against real and synthetic viewer configs.
 */

/** True when the viewer exists only to compare files and must never be offered on the visualize path. */
export function isCompareOnlyViewer(config: ViewerPluginConfig): boolean {
    return config.compareMode?.compareOnly === true;
}

/**
 * Visualize-path compatibility gate. A viewer is offered in visualize mode only when ALL hold:
 *  - it is not compare-only (`compareMode.compareOnly`): such a viewer reads `compareFiles`, which the
 *    visualize path never passes, so offering it there renders an immediate "needs N files" error
 *  - a multi-file selection requires `supportsMultiFile`
 *  - it can render every selected extension (supportsAllExtensions)
 *
 * A viewer that declares `compareMode.enabled` WITHOUT `compareOnly` is treated like any other viewer
 * here — compare capability is orthogonal to visualize capability unless the config says otherwise.
 */
export function admitsVisualizeSelection(
    config: ViewerPluginConfig,
    fileExtensions: string[],
    isMultiFile: boolean
): boolean {
    if (isCompareOnlyViewer(config)) {
        return false;
    }

    // Check if viewer supports multi-file when needed
    const multiFileSupport = !isMultiFile || config.supportsMultiFile;
    if (!multiFileSupport) {
        return false;
    }

    // Every selected file must be renderable by this viewer — see supportsAllExtensions.
    return supportsAllExtensions(config.supportedExtensions, fileExtensions);
}

/**
 * Compare-path compatibility gate. A viewer is offered in compare mode only when ALL hold:
 *  - it declares `compareMode.enabled`
 *  - the selected file count is within [minFiles, maxFiles]
 *  - it can render every selected extension (supportsAllExtensions)
 *  - the selection SHAPE is allowed (see admitsCompareShape): N versions of one file requires
 *    `allowSameFileDifferentVersions`, N distinct files requires `allowDifferentFiles`, and
 *    entries spanning assets require `allowCrossAsset`.
 *
 * `compareContext` describes the selection shape. When absent, only the count/extension checks
 * apply (the caller has not resolved the shape yet).
 */
export function admitsCompareSelection(
    config: ViewerPluginConfig,
    fileExtensions: string[],
    compareContext?: CompareContext
): boolean {
    const compare = config.compareMode;
    if (!compare || !compare.enabled) {
        return false;
    }

    // File-count window.
    const count = compareContext?.fileCount ?? fileExtensions.length;
    if (count < compare.minFiles || count > compare.maxFiles) {
        return false;
    }

    // Extension support: every selected extension must be renderable.
    if (!supportsAllExtensions(config.supportedExtensions, fileExtensions)) {
        return false;
    }

    // Selection-shape gating (same-file-versions / different-files / cross-asset).
    if (compareContext && !admitsCompareShape(compare, compareContext)) {
        return false;
    }

    return true;
}

/**
 * Which host surfaces a selection can be opened on. A surface is "available" when at least one
 * registered viewer of that kind admits the selection; a compare-only viewer never counts towards
 * `visualize`, and a viewer without an enabled `compareMode` never counts towards `compare`.
 */
export interface ViewerModeAvailability {
    visualize: boolean;
    compare: boolean;
}

/** Preview viewers serve the preview surface only and are never candidates for either path. */
const isCandidate = (config: ViewerPluginConfig): boolean => !config.isPreviewViewer;

/** True when at least one (non-compare-only, non-preview) viewer can visualize the selection. */
export function hasVisualizeViewer(
    configs: ViewerPluginConfig[],
    fileExtensions: string[],
    isMultiFile: boolean
): boolean {
    return configs.some(
        (config) =>
            isCandidate(config) && admitsVisualizeSelection(config, fileExtensions, isMultiFile)
    );
}

/** True when at least one compare-capable (non-preview) viewer admits the selection. */
export function hasCompareViewer(
    configs: ViewerPluginConfig[],
    fileExtensions: string[],
    compareContext?: CompareContext
): boolean {
    return configs.some(
        (config) =>
            isCandidate(config) && admitsCompareSelection(config, fileExtensions, compareContext)
    );
}

/**
 * Mode availability for a selection: the input for a Visualize/Compare toggle. Callers show only the
 * available modes, force the single one when exactly one is available, and show an empty state when
 * neither is. `isMultiFile` gates the visualize path; `compareContext` (count + shape) gates compare.
 */
export function availableViewerModes(
    configs: ViewerPluginConfig[],
    fileExtensions: string[],
    isMultiFile: boolean,
    compareContext?: CompareContext
): ViewerModeAvailability {
    return {
        visualize: hasVisualizeViewer(configs, fileExtensions, isMultiFile),
        compare: hasCompareViewer(configs, fileExtensions, compareContext),
    };
}

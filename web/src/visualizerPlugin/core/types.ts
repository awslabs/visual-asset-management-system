/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

export interface ViewerPluginConfig {
    id: string;
    name: string;
    description: string;
    componentPath: string;
    dependencyManager?: string;
    dependencyManagerClass?: string;
    dependencyManagerMethod?: string;
    dependencyCleanupMethod?: string;
    supportedExtensions: string[];
    supportsMultiFile: boolean;
    canFullscreen: boolean;
    priority: number;
    dependencies: string[];
    loadStrategy: "lazy" | "eager";
    category: string;
    requiresPreprocessing?: boolean;
    isPreviewViewer?: boolean;
    customParameters?: Record<string, any>;
    featuresEnabledRestriction?: string[];
    enabled?: boolean;
    /**
     * Optional compare-mode capability. When present and `enabled`, this viewer is offered in
     * compare mode ("mode" === "compare" in PluginRegistry.getCompatibleViewers). A viewer without
     * this block is never surfaced in compare mode; the visualize path ignores it entirely.
     */
    compareMode?: CompareModeConfig;
}

export interface CompareModeConfig {
    /** Whether this viewer participates in compare mode at all. */
    enabled: boolean;
    /** Minimum number of files the compare view accepts (inclusive). */
    minFiles: number;
    /** Maximum number of files the compare view accepts (inclusive). */
    maxFiles: number;
    /** Allow comparing N versions of a SINGLE file (one asset-relative key, distinct versionIds). */
    allowSameFileDifferentVersions: boolean;
    /** Allow comparing N DISTINCT files (different asset-relative keys). */
    allowDifferentFiles: boolean;
}

export interface ViewerPluginProps {
    assetId: string;
    databaseId: string;
    assetKey?: string;
    multiFileKeys?: string[];
    /** Per-file context for multi-file viewing (Decision #3). When present, viewers must
     *  build each file's stream URL from that file's own assetId/databaseId rather than the
     *  shared top-level pair — this is what lets a multi-file selection span assets. Indices
     *  align with multiFileKeys. Falls back to top-level assetId/databaseId when absent. */
    multiFiles?: FileInfo[];
    versionId?: string;
    assetVersionId?: string;
    viewerMode: string;
    onViewerModeChange: (mode: string) => void;
    onDeletePreview?: () => void;
    isPreviewFile?: boolean;
    customParameters?: Record<string, any>;
    /** True when the viewer is being rendered in compare mode. Viewers that declare
     *  `compareMode.enabled` should read `compareFiles` (ordered) instead of the single-file props. */
    compareMode?: boolean;
    /** Ordered files to compare (compare mode only). Order is significant — index 0 is the base /
     *  "left" side. Each entry keeps its own versionId (and per-file assetId/databaseId, Decision #3)
     *  so a viewer can compare N versions of one key or N distinct keys. */
    compareFiles?: FileInfo[];
}

export interface FileInfo {
    filename: string;
    key: string;
    isDirectory: boolean;
    /** Per-file owning asset (Decision #3). Optional: when absent, the viewer falls back to
     *  the top-level assetId/databaseId passed to FileViewerModal/DynamicViewer. */
    assetId?: string;
    /** Per-file owning database (Decision #3). See assetId note above. */
    databaseId?: string;
    versionId?: string;
    size?: number;
    dateCreatedCurrentVersion?: string;
    isArchived?: boolean;
    primaryType?: string | null;
    previewFile?: string;
}

export interface ViewerConfig {
    viewers: ViewerPluginConfig[];
}

export interface ViewerOption {
    text: string;
    id: string;
    description?: string;
}

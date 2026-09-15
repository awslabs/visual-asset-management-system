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
     * this block is never surfaced in compare mode; the visualize path ignores it entirely unless
     * `compareOnly` is set, which removes the viewer from the visualize path altogether.
     */
    compareMode?: CompareModeConfig;
}

export interface CompareModeConfig {
    /** Whether this viewer participates in compare mode at all. */
    enabled: boolean;
    /**
     * The viewer can ONLY compare: it reads `compareFiles` and has no single- or multi-file visualize
     * rendering. When true the visualize path never offers it (regardless of `supportsMultiFile` or
     * extension match), so a selection of N matching files does not surface a viewer that would
     * immediately fail with "needs exactly N files". Off when absent: compare capability is then
     * orthogonal to the viewer's visualize capability.
     */
    compareOnly?: boolean;
    /** Minimum number of files the compare view accepts (inclusive). */
    minFiles: number;
    /** Maximum number of files the compare view accepts (inclusive). */
    maxFiles: number;
    /** Allow comparing N versions of a SINGLE file (one asset-relative key, distinct versionIds). */
    allowSameFileDifferentVersions: boolean;
    /** Allow comparing N DISTINCT files (different asset-relative keys). */
    allowDifferentFiles: boolean;
    /**
     * Allow entries that belong to DIFFERENT assets (or databases). Off when absent: a viewer must
     * opt in because each entry is then fetched under its own asset and authorized independently
     * (Casbin, per asset), so the viewer has to render a per-entry "not authorized / unavailable"
     * state rather than a single failure for the whole comparison.
     */
    allowCrossAsset?: boolean;
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
     *  "left" side. Each entry is a fully resolved {databaseId, assetId, key, versionId?}: DynamicViewer
     *  fills a missing per-entry database/asset from its top-level props before handing the list over,
     *  so a viewer fetches each entry under ITS OWN asset (never the shared top-level pair) and treats a
     *  missing versionId as "latest". Entries may span assets when the viewer declares
     *  `compareMode.allowCrossAsset`; each asset is authorized independently, so a viewer must handle a
     *  per-entry 401/403/410 without collapsing the other entries. */
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

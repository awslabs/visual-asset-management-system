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
}

export interface ViewerPluginProps {
    assetId: string;
    databaseId: string;
    assetKey?: string;
    multiFileKeys?: string[];
    versionId?: string;
    viewerMode: string;
    onViewerModeChange: (mode: string) => void;
    onDeletePreview?: () => void;
    isPreviewFile?: boolean;
    customParameters?: Record<string, any>;
}

export interface FileInfo {
    filename: string;
    key: string;
    isDirectory: boolean;
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

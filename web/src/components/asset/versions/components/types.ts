/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Asset version types
export interface AssetVersion {
    Version: number;
    DateModified?: string;
    Comment?: string;
    createdBy?: string;
    isCurrent?: boolean;
}

export interface FileVersion {
    relativeKey: string;
    versionId: string;
    isLatestVersionArchived: boolean;
    isPermanentlyDeleted?: boolean;
}

// S3 file types
export interface S3File {
    fileName: string;
    key: string;
    relativePath: string;
    isFolder: boolean;
    size?: number;
    dateCreatedCurrentVersion: string;
    versionId: string;
    storageClass?: string;
    isArchived: boolean;
    currentAssetVersionFileVersionMismatch?: boolean;
}

export interface SelectedFile {
    relativeKey: string;
    versionId: string;
    isArchived: boolean;
    isCurrent?: boolean;
    versionMismatch?: boolean;
}

export interface S3FileVersion {
    versionId: string;
    lastModified?: string;
    size?: number;
    isArchived: boolean;
    isLatest?: boolean;
}

// Creation mode type
export type CreationMode = 'current' | 'select' | 'modify';

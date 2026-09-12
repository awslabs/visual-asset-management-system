/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { CompareModeConfig } from "./types";
import { fileIdentity, FileIdentityParts } from "./fileIdentity";

/**
 * Pure classification and gating for compare mode. Kept free of the registry (which pulls in Vite's
 * `import.meta.glob`) so it can be unit-tested and reused by callers that only need the shape.
 */

/** The shape of a compare selection, used to gate allowSameFileDifferentVersions vs
 *  allowDifferentFiles. */
export type CompareShape = "same-file-versions" | "different-files";

export interface CompareContext {
    /** Number of files in the compare selection. */
    fileCount: number;
    /** Whether the selection is N versions of one file or N distinct files. */
    shape: CompareShape;
    /** True when the entries belong to more than one asset (or database). Each such entry is fetched
     *  under its own asset and authorized independently, so a viewer must opt in via
     *  `compareMode.allowCrossAsset`. Absent/false = every entry is in the same asset. */
    crossAsset?: boolean;
}

/** The parts of a compare entry that matter for classification. */
export type CompareEntry = FileIdentityParts & { versionId?: string };

/** Owning-asset identity: database + asset, ignoring the key. */
const ownerIdentity = (file: CompareEntry): string =>
    fileIdentity({ databaseId: file?.databaseId, assetId: file?.assetId, key: "" });

/**
 * True when the entries span more than one owning asset. Entries without an assetId/databaseId are
 * assumed to belong to the caller's top-level asset, so a list where NO entry names an asset is never
 * cross-asset; callers that can resolve the ids should do so before asking.
 */
export function deriveCrossAsset(files: CompareEntry[]): boolean {
    return new Set(files.map(ownerIdentity)).size > 1;
}

/**
 * Classify a compare selection into its shape. When every file is the SAME file — same owning
 * database and asset AND same asset-relative key — and only their versionIds differ, the selection is
 * N versions of one file; otherwise it spans distinct files.
 *
 * Identity is database + asset + key, never the key alone: `config.json` under assetA and
 * `config.json` under assetB share a key but are two different files, and classifying them as
 * "same-file-versions" both mislabelled the comparison and admitted it through the wrong gate.
 */
export function deriveCompareShape(files: CompareEntry[]): CompareShape {
    const uniqueFiles = new Set(files.map(fileIdentity));
    return uniqueFiles.size <= 1 ? "same-file-versions" : "different-files";
}

/** Build the full compare context for a selection in one call. */
export function deriveCompareContext(files: CompareEntry[]): CompareContext {
    return {
        fileCount: files.length,
        shape: deriveCompareShape(files),
        crossAsset: deriveCrossAsset(files),
    };
}

/**
 * Selection-shape gating for a viewer's `compareMode` block. Returns false when the context describes
 * a selection the viewer has not opted into:
 *  - N versions of one file requires `allowSameFileDifferentVersions`
 *  - N distinct files requires `allowDifferentFiles`
 *  - entries spanning assets require `allowCrossAsset` (default: not allowed)
 *
 * The file-count window and extension support are checked by the caller; this covers shape only.
 */
export function admitsCompareShape(compare: CompareModeConfig, context: CompareContext): boolean {
    if (context.shape === "same-file-versions" && !compare.allowSameFileDifferentVersions) {
        return false;
    }
    if (context.shape === "different-files" && !compare.allowDifferentFiles) {
        return false;
    }
    if (context.crossAsset && !compare.allowCrossAsset) {
        return false;
    }
    return true;
}

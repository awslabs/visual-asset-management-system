/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ExecuteInputFile } from "../../../features/orchestration/types";

/**
 * Maps a file-manager selection onto workflow input files for the Automation group.
 *
 * Kept out of the panel component so the four selection shapes (whole asset, folder, one file, many
 * files) can be verified directly — they are what the backend's arity and assetScope gates read, and a
 * lost trailing slash silently turns a folder selection into a file selection.
 */

export interface SelectionItem {
    relativePath?: string;
    versionId?: string;
    isArchived?: boolean;
    isPermanentlyDeleted?: boolean;
}

export interface AutomationSelectionInput {
    databaseId?: string;
    assetId?: string;
    /** True when the tree is in multi-select mode with more than one item selected. */
    isMultiSelect: boolean;
    selectedItems: SelectionItem[];
    selectedItem?: SelectionItem | null;
    /** Whether the single selected item is a folder (or the asset root). */
    isFolder: boolean;
    /**
     * The asset version being browsed, when not viewing the live state. A file entry in that mode
     * already carries the version-resolved S3 `versionId` rather than the live one, so pinning it is
     * what makes the launch read the bytes on screen. A folder or whole-asset selection carries no
     * version and is expanded at launch against the live state, so it cannot stand for the version
     * being browsed at all.
     */
    assetVersionId?: string;
}

/** Asset-relative key with exactly one leading slash. */
const toKey = (path?: string): string => `/${(path || "").replace(/^\/+/, "")}`;

/** A folder key keeps its trailing slash; the asset root is a bare '/'. */
const toFolderKey = (path?: string): string => {
    const key = toKey(path);
    return key === "/" ? "/" : `${key.replace(/\/+$/, "")}/`;
};

/** Selections that cannot be processed: an archived or permanently-deleted entry has no live object. */
const isUsable = (item: SelectionItem): boolean => !item.isArchived && !item.isPermanentlyDeleted;

export function deriveAutomationInputFiles(input: AutomationSelectionInput): ExecuteInputFile[] {
    const { databaseId, assetId, isMultiSelect, selectedItems, selectedItem, isFolder } = input;
    if (!databaseId || !assetId) return [];

    if (isMultiSelect) {
        return (selectedItems || []).filter(isUsable).map((item) => ({
            databaseId,
            assetId,
            relativeFileKey: toKey(item.relativePath),
            versionId: item.versionId || undefined,
        }));
    }

    if (!selectedItem || !isUsable(selectedItem)) return [];

    if (isFolder) {
        // A folder carries no version: the workflow expands it at launch, and the files inside have
        // their own versions. Nothing in the launch names the asset version, so a folder browsed under
        // an earlier version would silently run on the live files.
        if (input.assetVersionId) return [];
        return [{ databaseId, assetId, relativeFileKey: toFolderKey(selectedItem.relativePath) }];
    }

    return [
        {
            databaseId,
            assetId,
            relativeFileKey: toKey(selectedItem.relativePath),
            versionId: selectedItem.versionId || undefined,
        },
    ];
}

/**
 * Why the Automation group is unavailable, or undefined when it is usable. Explaining the block beats
 * a silently greyed-out menu item the user cannot account for.
 */
export function automationDisabledReason(
    input: AutomationSelectionInput,
    derived: ExecuteInputFile[]
): string | undefined {
    const { isMultiSelect, selectedItems, selectedItem, isFolder } = input;

    if (isMultiSelect) {
        const dropped = (selectedItems || []).length - derived.length;
        if (derived.length === 0) return "The selected items cannot be processed.";
        if (dropped > 0) {
            return (
                `${dropped} selected item${dropped === 1 ? "" : "s"} cannot be processed ` +
                "(archived or deleted)."
            );
        }
        return undefined;
    }

    if (!selectedItem) return "Select a file, folder, or asset first.";
    if (selectedItem.isPermanentlyDeleted) return "A deleted selection cannot be processed.";
    if (selectedItem.isArchived) return "An archived selection cannot be processed.";
    if (isFolder && input.assetVersionId) {
        return "Select individual files to run against this version, or switch back to the current version.";
    }
    if (derived.length === 0) return "Select a file, folder, or asset first.";
    return undefined;
}

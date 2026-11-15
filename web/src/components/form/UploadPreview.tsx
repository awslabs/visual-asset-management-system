/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Preview of an upload selection before it starts (#1462): the final asset path, size and status
 * of every file, the single-folder override, the info line when the folder rule changes, and the
 * hint for loose files whose format references other files. Used by both upload pages.
 */

import React, { useMemo, useState } from "react";
import { useCollection } from "@cloudscape-design/collection-hooks";
import {
    Alert,
    Box,
    Button,
    FormField,
    Header,
    Icon,
    Pagination,
    RadioGroup,
    SegmentedControl,
    SpaceBetween,
    StatusIndicator,
    Table,
    TextFilter,
} from "@cloudscape-design/components";
import {
    PlannedFile,
    UploadFlow,
    UploadRoot,
    FolderNameMode,
    DUPLICATE_PATH_ERROR,
    INVALID_PATH_ERROR,
    PREVIEW_EXTENSION_ERROR,
    defaultFolderNameMode,
    isSingleFolderSelection,
    looseReferencingFiles,
} from "./uploadPaths";
import { paginationLabels } from "../../pages/AssetUpload/FileUploadTable";

export const EXISTS_STATUS_TEXT = "Exists: uploads a new version";
export const ARCHIVED_STATUS_TEXT = "Archived: uploading restores it with a new version";

export type ExistingCheckState = "none" | "checking" | "done" | "incomplete";

export interface UploadPreviewProps {
    flow: UploadFlow;
    destination: string;
    roots: UploadRoot[];
    planned: PlannedFile[];
    folderNameMode: FolderNameMode;
    onFolderNameModeChange: (mode: FolderNameMode) => void;
    onRemove: (ids: string[]) => void;
    onRemoveAll: () => void;
    /** Add Files only: state of the existing-path check. */
    existingCheck?: ExistingCheckState;
}

export function formatBytes(n: number): string {
    const k = n > 0 ? Math.min(4, Math.floor(Math.log2(n) / 10)) : 0;
    const unit = k > 0 ? `${"KMGT"[k - 1]}B` : "B";
    const value = n / Math.pow(1024, k);
    return `${k > 0 ? value.toFixed(1) : value} ${unit}`;
}

/** Errors that block the upload, in one sentence naming their kinds; undefined when none. */
export function blockingReason(planned: PlannedFile[]): string | undefined {
    const withErrors = planned.filter((p) => p.errors.length > 0);
    if (withErrors.length === 0) return undefined;
    const kinds = new Set<string>();
    for (const p of withErrors) {
        for (const e of p.errors) {
            if (e === DUPLICATE_PATH_ERROR) kinds.add("duplicate path");
            else if (e === INVALID_PATH_ERROR) kinds.add("invalid path");
            else if (e === PREVIEW_EXTENSION_ERROR) kinds.add("preview file format not allowed");
            else kinds.add("extension not allowed");
        }
    }
    const n = withErrors.length;
    return `${n} ${n === 1 ? "file can't" : "files can't"} be uploaded (${Array.from(kinds).join(
        ", "
    )}). Remove them from the selection before uploading.`;
}

function statusCell(item: PlannedFile) {
    if (item.errors.length > 0) {
        return <StatusIndicator type="error">{item.errors.join("; ")}</StatusIndicator>;
    }
    if (item.existing === "exists") {
        return <StatusIndicator type="warning">{EXISTS_STATUS_TEXT}</StatusIndicator>;
    }
    if (item.existing === "archived") {
        return <StatusIndicator type="info">{ARCHIVED_STATUS_TEXT}</StatusIndicator>;
    }
    return <StatusIndicator type="success">Ready</StatusIndicator>;
}

type ViewFilter = "all" | "errors" | "existing";

export default function UploadPreview({
    flow,
    destination,
    roots,
    planned,
    folderNameMode,
    onFolderNameModeChange,
    onRemove,
    onRemoveAll,
    existingCheck = "none",
}: UploadPreviewProps) {
    const [view, setView] = useState<ViewFilter>("all");
    const [hintDismissed, setHintDismissed] = useState<string>("");

    const singleFolder = isSingleFolderSelection(roots);
    const effectiveMode = folderNameMode === "auto" ? defaultFolderNameMode(flow) : folderNameMode;
    const hasFolder = roots.some((r) => r.kind === "folder");
    const referencing = useMemo(() => looseReferencingFiles(roots), [roots]);
    const hintKey = referencing.join("|");

    const errorCount = planned.filter((p) => p.errors.length > 0).length;
    const existingChecked =
        flow === "addFiles" && (existingCheck === "done" || existingCheck === "incomplete");
    const existingCount = planned.filter((p) => p.existing).length;
    const totalSize = planned.reduce((n, p) => n + p.size, 0);

    const visible = useMemo(() => {
        if (view === "errors") return planned.filter((p) => p.errors.length > 0);
        if (view === "existing" && existingChecked) return planned.filter((p) => !!p.existing);
        return planned;
    }, [planned, view, existingChecked]);

    const { items, actions, collectionProps, filterProps, paginationProps, filteredItemsCount } =
        useCollection(visible, {
            filtering: {
                filteringFunction: (item, text) =>
                    item.finalPath.toLowerCase().includes(text.toLowerCase()),
                empty: <Box textAlign="center">No files selected</Box>,
                noMatch: <Box textAlign="center">No files match the filter</Box>,
            },
            pagination: { pageSize: 50 },
            sorting: {
                defaultState: {
                    sortingColumn: {
                        sortingField: "finalPath",
                        sortingComparator: (a: PlannedFile, b: PlannedFile) =>
                            a.finalPath.localeCompare(b.finalPath),
                    },
                },
            },
            selection: { trackBy: "id" },
        });

    const selectedIds = (collectionProps.selectedItems || []).map((i) => i.id);
    // Ids are positions in the selection, so they shift when files are removed: clear the
    // table selection on every removal.
    const remove = (ids: string[]) => {
        actions.setSelectedItems([]);
        onRemove(ids);
    };
    const removeAll = () => {
        actions.setSelectedItems([]);
        onRemoveAll();
    };
    const reason = blockingReason(planned);

    if (planned.length === 0) return null;

    const sample = (keep: boolean) => {
        const root = roots[0];
        if (!root || root.kind !== "folder" || root.files.length === 0) return "";
        return `${destination}${keep ? `${root.name}/` : ""}${root.files[0].path}`;
    };

    return (
        <SpaceBetween direction="vertical" size="m">
            {singleFolder && (
                <FormField
                    label="How to add this folder"
                    description={`Applies while the selection is the single folder "${roots[0].name}".`}
                >
                    <RadioGroup
                        value={effectiveMode}
                        onChange={({ detail }) =>
                            onFolderNameModeChange(detail.value as FolderNameMode)
                        }
                        items={[
                            {
                                value: "dropName",
                                label: "Upload folder contents",
                                description: `The folder's files go directly into ${destination}, for example ${sample(
                                    false
                                )}`,
                            },
                            {
                                value: "keepName",
                                label: "Upload folder",
                                description: `The folder keeps its name, for example ${sample(
                                    true
                                )}`,
                            },
                        ]}
                    />
                </FormField>
            )}

            {flow === "createAsset" && hasFolder && !singleFolder && (
                <Alert type="info">
                    Each folder keeps its name as a top-level folder because the selection holds
                    more than one item. Only a single folder added on its own becomes the
                    asset&apos;s contents.
                </Alert>
            )}

            {referencing.length > 0 && hintDismissed !== hintKey && (
                <Alert type="info" dismissible onDismiss={() => setHintDismissed(hintKey)}>
                    {referencing.slice(0, 3).join(", ")}
                    {referencing.length > 3 ? ` and ${referencing.length - 3} more` : ""} can
                    reference other files. To keep those references working, add the whole folder
                    with Add folder.
                </Alert>
            )}

            {existingCheck === "incomplete" && (
                <Alert type="info">
                    Could not check all existing files. A file uploaded to a path that already
                    exists gets a new version.
                </Alert>
            )}

            {/* Stated above the table so an error is seen even when a row is off screen. */}
            {reason && (
                <Alert
                    type="error"
                    action={
                        view !== "errors" ? (
                            <Button onClick={() => setView("errors")}>Show errors only</Button>
                        ) : undefined
                    }
                >
                    {reason}
                </Alert>
            )}

            <Table
                {...collectionProps}
                items={items}
                trackBy="id"
                selectionType="multi"
                variant="container"
                // Long paths wrap; status and size sit under each path in the same cell, so
                // nothing needs a sideways scroll at narrow widths.
                wrapLines
                ariaLabels={{
                    selectionGroupLabel: "File selection",
                    allItemsSelectionLabel: () => "Select all files",
                    itemSelectionLabel: (_s, item) => `Select ${item.finalPath}`,
                }}
                header={
                    <Header
                        counter={`(${planned.length})`}
                        description={`Destination: ${destination} · Total size ${formatBytes(
                            totalSize
                        )} · ${errorCount} with errors${
                            flow === "addFiles"
                                ? existingCheck === "checking"
                                    ? " · checking for existing files..."
                                    : ` · ${existingCount} existing`
                                : ""
                        }`}
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button
                                    disabled={selectedIds.length === 0}
                                    onClick={() => remove(selectedIds)}
                                >
                                    Remove selected
                                </Button>
                                <Button onClick={removeAll}>Remove all</Button>
                            </SpaceBetween>
                        }
                    >
                        Files to upload
                    </Header>
                }
                filter={
                    <SpaceBetween direction="horizontal" size="s">
                        <TextFilter
                            {...filterProps}
                            filteringPlaceholder="Filter by path"
                            filteringAriaLabel="Filter files by path"
                            countText={`${filteredItemsCount} matches`}
                        />
                        <SegmentedControl
                            selectedId={view === "existing" && !existingChecked ? "all" : view}
                            onChange={({ detail }) => setView(detail.selectedId as ViewFilter)}
                            label="Show"
                            options={[
                                { id: "all", text: "All" },
                                { id: "errors", text: `Errors (${errorCount})` },
                                // Offered once the existing-path check has finished.
                                ...(existingChecked
                                    ? [{ id: "existing", text: `Existing (${existingCount})` }]
                                    : []),
                            ]}
                        />
                    </SpaceBetween>
                }
                pagination={<Pagination {...paginationProps} ariaLabels={paginationLabels} />}
                columnDefinitions={[
                    {
                        // Path, status and size in one cell, so a row's status is always next to
                        // its path, even in a narrow table, without sideways scrolling.
                        id: "finalPath",
                        header: "Asset path and status",
                        cell: (item) => (
                            <div data-testid="upload-preview-file-cell">
                                <div style={{ overflowWrap: "anywhere" }}>{item.finalPath}</div>
                                <SpaceBetween direction="horizontal" size="xs">
                                    {statusCell(item)}
                                    <Box variant="small" color="text-body-secondary">
                                        {formatBytes(item.size)}
                                    </Box>
                                </SpaceBetween>
                            </div>
                        ),
                        sortingField: "finalPath",
                        sortingComparator: (a, b) => a.finalPath.localeCompare(b.finalPath),
                        isRowHeader: true,
                    },
                    {
                        // Icon only: the header is labelled for screen readers but takes no
                        // width, so it never wraps.
                        id: "remove",
                        header: <Icon name="remove" ariaLabel="Remove" />,
                        cell: (item) => (
                            <Button
                                variant="icon"
                                iconName="remove"
                                ariaLabel={`Remove ${item.finalPath}`}
                                onClick={() => remove([item.id])}
                            />
                        ),
                    },
                ]}
            />
        </SpaceBetween>
    );
}

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import VirtualList from "../components/VirtualList";
import { btnSecondary } from "../components/controlStyles";
import type { ExecuteInputFile } from "../types";
import type { ResolvedRestrictions } from "./resolveRestrictions";
import { MAX_INPUT_FILES_PER_EXECUTION } from "./reviewBlockers";
import {
    describeInputFiles,
    describeKey,
    filterRows,
    inputFileCompatibility,
    inputFileKey,
    pluralize,
    rowsByAsset,
    summarizeInputFiles,
    type InputFileCompatibility,
    type InputFileRow,
} from "./selectedInputFiles";

/** One row of the list, in pixels; every row is one line, so the list can be windowed. */
export const INPUT_FILE_ROW_HEIGHT = 36;
/** The viewport: nine rows, whatever the selection holds. */
export const INPUT_FILE_LIST_HEIGHT = INPUT_FILE_ROW_HEIGHT * 9;

/** Whether the chain admits the entry, as a chip. The reason is the chip's hover text. */
export const CompatibilityBadge: React.FC<{ compatibility: InputFileCompatibility }> = ({
    compatibility,
}) => (
    <span
        title={compatibility.ok ? undefined : compatibility.reason}
        className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${
            compatibility.ok
                ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300"
                : "bg-red-100 text-red-900 dark:bg-red-900/30 dark:text-red-200"
        }`}
    >
        {compatibility.ok ? "Compatible" : "Not compatible"}
    </span>
);

interface SelectedInputFilesListProps {
    files: ExecuteInputFile[];
    restrictions: ResolvedRestrictions;
    /** Indexes shown elsewhere as editable rows, left out of this list. */
    skipIndexes?: Set<number>;
    onRemove: (index: number) => void;
    onEdit: (index: number) => void;
    onClearAll: () => void;
}

/**
 * The selected input files as one compact, windowed list: grouped by asset, each row the asset, the
 * key, whether the resolved chain admits it, and Edit / Remove. A selection of a thousand entries
 * mounts a screenful of rows, and nothing in a row opens a request — the compatibility is computed
 * once per selection from the already-resolved restrictions.
 */
const SelectedInputFilesList: React.FC<SelectedInputFilesListProps> = ({
    files,
    restrictions,
    skipIndexes,
    onRemove,
    onEdit,
    onClearAll,
}) => {
    const [filter, setFilter] = React.useState("");
    const [confirmingClear, setConfirmingClear] = React.useState(false);
    const idPrefix = React.useId();

    const summary = React.useMemo(() => summarizeInputFiles(files), [files]);
    // Rows and their compatibility are derived per selection identity, not per render of a row.
    const rows = React.useMemo(() => {
        const compatibility = new Map<string, InputFileCompatibility>();
        return rowsByAsset(files, skipIndexes).map((row) => {
            const key = inputFileKey(row.file);
            let compat = compatibility.get(key);
            if (!compat) {
                compat = inputFileCompatibility(row.file, restrictions);
                compatibility.set(key, compat);
            }
            return { ...row, compatibility: compat };
        });
    }, [files, skipIndexes, restrictions]);
    const visible = React.useMemo(() => filterRows(rows, filter), [rows, filter]);
    const overCap = summary.total > MAX_INPUT_FILES_PER_EXECUTION;
    const incompatible = rows.filter((r) => !r.compatibility.ok).length;

    const renderRow = (row: InputFileRow & { compatibility: InputFileCompatibility }) => {
        const keyId = `${idPrefix}-key-${row.index}`;
        return (
            <div
                className="orch-outline flex h-full items-center gap-2 border-b border-border-default px-2 text-xs"
                data-testid="input-file-row"
            >
                <span
                    className="w-40 shrink-0 truncate text-text-secondary"
                    title={`${row.file.databaseId} / ${row.file.assetId}`}
                >
                    {row.file.databaseId} / {row.file.assetId}
                </span>
                <span
                    id={keyId}
                    className="min-w-0 flex-1 truncate font-mono text-text-primary"
                    title={row.file.relativeFileKey}
                >
                    {describeKey(row.file.relativeFileKey)}
                </span>
                {row.file.versionId && (
                    <span
                        className="shrink-0 rounded bg-surface-secondary px-1.5 font-mono text-[11px] text-text-secondary"
                        title={`Pinned file version ${row.file.versionId}`}
                    >
                        v {row.file.versionId.slice(0, 8)}
                    </span>
                )}
                <CompatibilityBadge compatibility={row.compatibility} />
                <button
                    type="button"
                    aria-describedby={keyId}
                    onClick={() => onEdit(row.index)}
                    className="shrink-0 text-blue-600 hover:underline dark:text-blue-400"
                >
                    Edit
                </button>
                <button
                    type="button"
                    aria-describedby={keyId}
                    onClick={() => onRemove(row.index)}
                    className="shrink-0 text-red-600 hover:underline dark:text-red-400"
                >
                    Remove
                </button>
            </div>
        );
    };

    return (
        <div className="space-y-2">
            {/* The count is the one line that has to stay true at any size; the cap sits beside it
                because that is where a selection outgrows what a run may carry. */}
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <p className="text-text-primary" data-testid="input-file-count">
                    <span className="font-medium">{describeInputFiles(summary)}</span>
                    <span className={overCap ? "ml-1 text-red-600 dark:text-red-400" : "ml-1"}>
                        {overCap
                            ? `· over the ${MAX_INPUT_FILES_PER_EXECUTION} limit`
                            : `· max ${MAX_INPUT_FILES_PER_EXECUTION}`}
                    </span>
                    {incompatible > 0 && (
                        <span className="ml-1 text-red-600 dark:text-red-400">
                            · {pluralize(incompatible, "file")} not compatible
                        </span>
                    )}
                </p>
                <div className="flex items-center gap-2">
                    <input
                        type="text"
                        aria-label="Filter selected files"
                        placeholder="Filter by key or asset…"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        className="w-48 rounded border border-border-input bg-surface-input px-2 py-1 text-xs text-text-primary"
                    />
                    {/* Clearing hundreds of rows is not undoable, so the first click asks. */}
                    {confirmingClear ? (
                        <span
                            className="flex items-center gap-1"
                            role="group"
                            aria-label="Clear all"
                        >
                            <span className="text-text-secondary">Remove all {summary.total}?</span>
                            <button
                                type="button"
                                onClick={() => {
                                    setConfirmingClear(false);
                                    onClearAll();
                                }}
                                className="font-medium text-red-600 hover:underline dark:text-red-400"
                            >
                                Yes, clear
                            </button>
                            <button
                                type="button"
                                onClick={() => setConfirmingClear(false)}
                                className="hover:underline"
                            >
                                Keep
                            </button>
                        </span>
                    ) : (
                        <button
                            type="button"
                            onClick={() => setConfirmingClear(true)}
                            className={`${btnSecondary} !px-2 !py-1 !text-xs !font-medium`}
                        >
                            Clear all
                        </button>
                    )}
                </div>
            </div>

            {filter && (
                <p className="text-xs text-text-secondary">
                    Showing {visible.length} of {rows.length}
                </p>
            )}

            {visible.length === 0 ? (
                <p className="text-xs text-text-secondary">No selected file matches the filter.</p>
            ) : (
                <VirtualList
                    items={visible}
                    rowHeight={INPUT_FILE_ROW_HEIGHT}
                    height={INPUT_FILE_LIST_HEIGHT}
                    rowKey={(row) => row.index}
                    renderRow={renderRow}
                    aria-label="Selected input files"
                    testId="input-file-list"
                    className="orch-outline rounded border border-border-default bg-surface-secondary"
                />
            )}
        </div>
    );
};

export default SelectedInputFilesList;

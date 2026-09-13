/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Dialog from "../components/Dialog";
import Callout from "../components/Callout";
import SearchableSelect from "../components/SearchableSelect";
import VirtualList from "../components/VirtualList";
import { btnPrimary, btnSecondary } from "../components/controlStyles";
import { useAssetSearch, useAssetFilePages } from "../api/queries";
import type { ExecuteInputFile } from "../types";
import type { ResolvedRestrictions } from "./resolveRestrictions";
import { MAX_INPUT_FILES_PER_EXECUTION } from "./reviewBlockers";
import { CompatibilityBadge } from "./SelectedInputFilesList";
import {
    inputFileCompatibility,
    inputFileKey,
    normalizeRelativeKey,
    parsePastedKeys,
    pluralize,
    type InputFileCompatibility,
} from "./selectedInputFiles";

/** How long a typed folder prefix settles before it becomes the listing's server-side scope. */
export const PREFIX_COMMIT_DEBOUNCE_MS = 300;

const PICKER_ROW_HEIGHT = 32;
const PICKER_LIST_HEIGHT = PICKER_ROW_HEIGHT * 9;

const selectClass =
    "w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary focus:outline-none focus:ring-2 focus:ring-blue-500";
const fieldClass =
    "w-full rounded border border-border-input bg-surface-input px-2 py-1.5 text-sm text-text-primary";
const linkButton = "text-xs font-medium text-blue-600 hover:underline dark:text-blue-400";

interface BulkFilePickerProps {
    onClose: () => void;
    databaseOptions: { databaseId: string }[];
    initialDatabaseId?: string;
    initialAssetId?: string;
    restrictions: ResolvedRestrictions;
    /** Keys already in the selection ({@link inputFileKey}), marked and not offered again. */
    selectedKeys: ReadonlySet<string>;
    /** Entries the selection already holds; the picker stops offering at the execution cap. */
    existingCount: number;
    onAdd: (files: ExecuteInputFile[]) => void;
}

interface PickerRow {
    key: string;
    compatibility: InputFileCompatibility;
    alreadySelected: boolean;
}

/** The trailing-slash folder key for a typed prefix, or '' when nothing is typed. */
export const folderKeyFor = (prefix: string): string => {
    const body = (prefix || "").trim().replace(/^\/+|\/+$/g, "");
    return body ? `/${body}/` : "";
};

/**
 * The dialog behind "Add files": database and asset, then the asset's files as a searchable, paged,
 * check-to-select list — or, for the operator who already holds the keys, a paste box. Everything it
 * adds goes through `onAdd` as ordinary input-file entries; the caller deduplicates.
 *
 * Mounted only while open, so each opening starts from the caller's seed.
 */
const BulkFilePicker: React.FC<BulkFilePickerProps> = ({
    onClose,
    databaseOptions,
    initialDatabaseId = "",
    initialAssetId = "",
    restrictions,
    selectedKeys,
    existingCount,
    onAdd,
}) => {
    const [mode, setMode] = React.useState<"browse" | "paste">("browse");
    const [databaseId, setDatabaseId] = React.useState(initialDatabaseId);
    const [assetId, setAssetId] = React.useState(initialAssetId);
    const [assetQuery, setAssetQuery] = React.useState("");
    const [prefixInput, setPrefixInput] = React.useState("");
    const [prefix, setPrefix] = React.useState("");
    const [filter, setFilter] = React.useState("");
    const [checked, setChecked] = React.useState<Set<string>>(() => new Set());
    const [walking, setWalking] = React.useState(false);
    const [note, setNote] = React.useState("");
    const [pasteText, setPasteText] = React.useState("");
    const prefixTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
    const unmounted = React.useRef(false);

    React.useEffect(
        () => () => {
            unmounted.current = true;
            if (prefixTimer.current !== null) clearTimeout(prefixTimer.current);
        },
        []
    );

    const capacity = Math.max(0, MAX_INPUT_FILES_PER_EXECUTION - existingCount);

    // Assets resolve server-side per search term, as in the row selector.
    const assetSearch = useAssetSearch(assetQuery, databaseId, !!databaseId);
    const assets = assetSearch.data?.items || [];
    const assetFooter =
        (assetSearch.data?.total ?? 0) > assets.length
            ? `Showing ${assets.length} of ${assetSearch.data?.total} — refine the search`
            : undefined;

    const pages = useAssetFilePages(databaseId, assetId, prefix);
    const loaded = React.useMemo(
        () => (pages.data?.pages || []).flatMap((page) => page.items),
        [pages.data]
    );

    // Compatibility and the already-selected mark, once per loaded set rather than per row render.
    const rows = React.useMemo<PickerRow[]>(
        () =>
            loaded.map((file) => {
                const entry = { databaseId, assetId, relativeFileKey: file.relativePath };
                return {
                    key: file.relativePath,
                    compatibility: inputFileCompatibility(entry, restrictions),
                    alreadySelected: selectedKeys.has(inputFileKey(entry)),
                };
            }),
        [loaded, databaseId, assetId, restrictions, selectedKeys]
    );
    const needle = filter.trim().toLowerCase();
    const visible = React.useMemo(
        () => (needle ? rows.filter((r) => r.key.toLowerCase().includes(needle)) : rows),
        [rows, needle]
    );
    const selectable = (row: PickerRow) => row.compatibility.ok && !row.alreadySelected;

    const commitPrefix = (value: string) => {
        if (prefixTimer.current !== null) clearTimeout(prefixTimer.current);
        prefixTimer.current = null;
        const next = folderKeyFor(value);
        if (next !== prefix) {
            setPrefix(next);
            setChecked(new Set());
            setNote("");
        }
    };
    const schedulePrefix = (value: string) => {
        if (prefixTimer.current !== null) clearTimeout(prefixTimer.current);
        prefixTimer.current = setTimeout(() => commitPrefix(value), PREFIX_COMMIT_DEBOUNCE_MS);
    };

    const handleDatabase = (next: string) => {
        setDatabaseId(next);
        setAssetId("");
        setAssetQuery("");
        setChecked(new Set());
        setNote("");
    };
    const handleAsset = (next: string) => {
        setAssetId(next);
        setChecked(new Set());
        setNote("");
    };

    /** Add `keys` to the checked set, in order, stopping at what the run can still take. */
    const checkUpTo = (keys: string[]) => {
        const next = new Set(checked);
        let capped = false;
        for (const key of keys) {
            if (next.has(key)) continue;
            if (next.size >= capacity) {
                capped = true;
                break;
            }
            next.add(key);
        }
        setChecked(next);
        setNote(capped ? `Stopped at the ${MAX_INPUT_FILES_PER_EXECUTION}-file limit.` : "");
    };

    const toggle = (key: string) => {
        const next = new Set(checked);
        if (next.has(key)) next.delete(key);
        else if (next.size < capacity) next.add(key);
        else {
            setNote(`Stopped at the ${MAX_INPUT_FILES_PER_EXECUTION}-file limit.`);
            return;
        }
        setChecked(next);
    };

    const selectAllShown = () => checkUpTo(visible.filter(selectable).map((r) => r.key));

    /**
     * Every file matching the filter and prefix, on every page — walked until the listing ends or the
     * cap is reached, so an asset far larger than a run may take is never pulled down whole. Only a
     * file the run could still take counts toward the cap: a page of already-selected or rejected
     * files would otherwise end the walk before the pages that hold anything to add.
     */
    const selectAllMatching = async () => {
        setWalking(true);
        setNote("");
        try {
            let result: { data?: typeof pages.data; hasNextPage: boolean } = {
                data: pages.data,
                hasNextPage: pages.hasNextPage,
            };
            const matching = (data: typeof pages.data) =>
                (data?.pages || [])
                    .flatMap((page) => page.items)
                    .map((file) => file.relativePath)
                    .filter((key) => !needle || key.toLowerCase().includes(needle));
            const addable = (keys: string[]) =>
                keys.filter((key) => {
                    const entry = { databaseId, assetId, relativeFileKey: key };
                    return (
                        inputFileCompatibility(entry, restrictions).ok &&
                        !selectedKeys.has(inputFileKey(entry))
                    );
                });
            while (result.hasNextPage && addable(matching(result.data)).length < capacity) {
                const next = await pages.fetchNextPage();
                if (unmounted.current) return;
                result = { data: next.data, hasNextPage: !!next.hasNextPage };
                if (!next.data) break;
            }
            const matched = matching(result.data);
            const keys = addable(matched);
            if (keys.length === 0 && matched.length > 0) {
                setNote("Every matching file is already selected or not compatible.");
                return;
            }
            checkUpTo(keys);
        } finally {
            if (!unmounted.current) setWalking(false);
        }
    };

    const addChecked = () => {
        // Emitted in listing order rather than in click order, so the selection reads like the asset.
        const inOrder = rows.filter((r) => checked.has(r.key)).map((r) => r.key);
        const seen = new Set(inOrder);
        checked.forEach((key) => {
            if (!seen.has(key)) inOrder.push(key);
        });
        onAdd(inOrder.map((key) => ({ databaseId, assetId, relativeFileKey: key })));
        onClose();
    };

    const addContainer = (relativeFileKey: string) => {
        onAdd([{ databaseId, assetId, relativeFileKey }]);
        onClose();
    };

    // Paste mode: one key per line, judged the same way as a listed file.
    const pasted = React.useMemo(() => {
        const keys = parsePastedKeys(pasteText);
        const judged = keys.map((key) => {
            const entry = { databaseId, assetId, relativeFileKey: key };
            return {
                key,
                compatibility: inputFileCompatibility(entry, restrictions),
                alreadySelected: selectedKeys.has(inputFileKey(entry)),
            };
        });
        const addable = judged
            .filter((row) => row.compatibility.ok && !row.alreadySelected)
            .slice(0, capacity);
        return {
            total: judged.length,
            incompatible: judged.filter((row) => !row.compatibility.ok).length,
            alreadySelected: judged.filter((row) => row.alreadySelected).length,
            addable,
        };
    }, [pasteText, databaseId, assetId, restrictions, selectedKeys, capacity]);

    const addPasted = () => {
        onAdd(pasted.addable.map((row) => ({ databaseId, assetId, relativeFileKey: row.key })));
        onClose();
    };

    const folderKey = folderKeyFor(prefixInput);
    const scopeChosen = !!databaseId && !!assetId;
    const footer =
        mode === "browse" ? (
            <>
                <button type="button" onClick={onClose} className={btnSecondary}>
                    Cancel
                </button>
                <button
                    type="button"
                    onClick={addChecked}
                    disabled={checked.size === 0}
                    className={btnPrimary}
                >
                    Add {pluralize(checked.size, "file")}
                </button>
            </>
        ) : (
            <>
                <button type="button" onClick={onClose} className={btnSecondary}>
                    Cancel
                </button>
                <button
                    type="button"
                    onClick={addPasted}
                    disabled={!scopeChosen || pasted.addable.length === 0}
                    className={btnPrimary}
                >
                    Add {pluralize(pasted.addable.length, "key")}
                </button>
            </>
        );

    const renderRow = (row: PickerRow) => (
        <label
            className={`orch-outline flex h-full items-center gap-2 border-b border-border-default px-2 text-xs ${
                selectable(row) ? "cursor-pointer hover:bg-surface-hover" : "opacity-70"
            }`}
            data-testid="picker-file-row"
        >
            <input
                type="checkbox"
                aria-label={row.key}
                checked={checked.has(row.key)}
                disabled={!selectable(row)}
                onChange={() => toggle(row.key)}
            />
            <span className="min-w-0 flex-1 truncate font-mono text-text-primary" title={row.key}>
                {row.key}
            </span>
            {row.alreadySelected && (
                <span className="shrink-0 text-text-secondary">Already selected</span>
            )}
            <CompatibilityBadge compatibility={row.compatibility} />
        </label>
    );

    return (
        <Dialog open onOpenChange={() => onClose()} title="Add input files" footer={footer}>
            <div className="space-y-3">
                {/* Two ways in: browse the asset, or paste the keys. */}
                <div className="flex gap-1 text-sm" role="tablist" aria-label="How to add files">
                    {(["browse", "paste"] as const).map((m) => (
                        <button
                            key={m}
                            type="button"
                            role="tab"
                            aria-selected={mode === m}
                            onClick={() => setMode(m)}
                            className={`orch-outline rounded border px-3 py-1 ${
                                mode === m
                                    ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300"
                                    : "border-border-default text-text-secondary hover:bg-surface-hover"
                            }`}
                        >
                            {m === "browse" ? "Browse files" : "Paste keys"}
                        </button>
                    ))}
                </div>

                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    <label className="block">
                        <span className="block text-xs text-text-secondary mb-1">Database</span>
                        <select
                            aria-label="Database"
                            value={databaseId}
                            onChange={(e) => handleDatabase(e.target.value)}
                            className={selectClass}
                        >
                            <option value="">Select a database…</option>
                            {databaseOptions.map((d) => (
                                <option key={d.databaseId} value={d.databaseId}>
                                    {d.databaseId}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="block">
                        <span className="block text-xs text-text-secondary mb-1">Asset</span>
                        <SearchableSelect
                            ariaLabel="Asset"
                            value={assetId}
                            disabled={!databaseId}
                            loading={assetSearch.isFetching}
                            placeholder={databaseId ? "Search assets…" : "Select a database first"}
                            onChange={handleAsset}
                            onQueryChange={setAssetQuery}
                            footerNote={assetFooter}
                            options={assets.map((a) => ({
                                value: a.assetId,
                                label: a.assetName || a.assetId,
                                detail: a.assetName ? a.assetId : undefined,
                            }))}
                        />
                    </label>
                </div>

                {capacity === 0 && (
                    <Callout tone="warning">
                        The selection already holds {MAX_INPUT_FILES_PER_EXECUTION} files — the most
                        a run may take. Remove some before adding more.
                    </Callout>
                )}

                {mode === "browse" && (
                    <div className="space-y-2">
                        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                            <label className="block">
                                <span className="block text-xs text-text-secondary mb-1">
                                    Folder prefix (optional)
                                </span>
                                {/* The prefix scopes the LISTING on the server: typing settles, or
                                    Enter applies it at once. */}
                                <input
                                    type="text"
                                    aria-label="Folder prefix"
                                    placeholder="/folder/"
                                    value={prefixInput}
                                    disabled={!scopeChosen}
                                    onChange={(e) => {
                                        setPrefixInput(e.target.value);
                                        schedulePrefix(e.target.value);
                                    }}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter") {
                                            e.preventDefault();
                                            commitPrefix(prefixInput);
                                        }
                                    }}
                                    className={fieldClass}
                                />
                            </label>
                            <label className="block">
                                <span className="block text-xs text-text-secondary mb-1">
                                    Filter loaded files
                                </span>
                                <input
                                    type="text"
                                    aria-label="Filter files"
                                    placeholder="Type part of a file name…"
                                    value={filter}
                                    disabled={!scopeChosen}
                                    onChange={(e) => setFilter(e.target.value)}
                                    className={fieldClass}
                                />
                            </label>
                        </div>

                        {!scopeChosen ? (
                            <p className="text-sm text-text-secondary">
                                Choose a database and an asset to list its files.
                            </p>
                        ) : pages.isError ? (
                            <Callout tone="error">
                                {(pages.error as Error)?.message || "Failed to load files."}
                            </Callout>
                        ) : pages.isLoading ? (
                            <p className="text-sm text-text-secondary">Loading files…</p>
                        ) : (
                            <>
                                <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                                    <div className="flex flex-wrap items-center gap-3">
                                        <button
                                            type="button"
                                            onClick={selectAllShown}
                                            disabled={walking || visible.length === 0}
                                            className={linkButton}
                                        >
                                            Select all shown
                                        </button>
                                        <button
                                            type="button"
                                            onClick={selectAllMatching}
                                            disabled={
                                                walking ||
                                                (loaded.length === 0 && !pages.hasNextPage)
                                            }
                                            className={linkButton}
                                        >
                                            Select all matching
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setChecked(new Set());
                                                setNote("");
                                            }}
                                            disabled={checked.size === 0}
                                            className={linkButton}
                                        >
                                            Clear selection
                                        </button>
                                    </div>
                                    <span className="text-text-secondary" aria-live="polite">
                                        {pluralize(checked.size, "file")} selected
                                        {walking && " · loading every matching file…"}
                                    </span>
                                </div>

                                {visible.length === 0 ? (
                                    <p className="text-sm text-text-secondary">
                                        {loaded.length === 0
                                            ? "No files under this prefix."
                                            : "No loaded file matches the filter."}
                                    </p>
                                ) : (
                                    <VirtualList
                                        items={visible}
                                        rowHeight={PICKER_ROW_HEIGHT}
                                        height={PICKER_LIST_HEIGHT}
                                        rowKey={(row) => row.key}
                                        renderRow={renderRow}
                                        aria-label="Files"
                                        testId="picker-file-list"
                                        className="orch-outline rounded border border-border-default bg-surface-secondary"
                                    />
                                )}

                                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-text-secondary">
                                    <span>
                                        {needle
                                            ? `${visible.length} of ${loaded.length} loaded match`
                                            : `${pluralize(loaded.length, "file")} loaded`}
                                        {pages.hasNextPage && " · more available"}
                                    </span>
                                    {pages.hasNextPage && (
                                        <button
                                            type="button"
                                            onClick={() => pages.fetchNextPage()}
                                            disabled={pages.isFetchingNextPage || walking}
                                            className={linkButton}
                                        >
                                            {pages.isFetchingNextPage ? "Loading…" : "Load more"}
                                        </button>
                                    )}
                                </div>
                                {note && (
                                    <p className="text-xs text-yellow-800 dark:text-yellow-300">
                                        {note}
                                    </p>
                                )}
                            </>
                        )}

                        {/* Container selections stay one click away: the whole asset, or the folder
                            the prefix names, each offered only when the resolved chain admits it. */}
                        {scopeChosen &&
                            (restrictions.wholeAssetAllowed ||
                                (restrictions.folderAllowed && folderKey)) && (
                                <div className="flex flex-wrap gap-2 pt-1">
                                    {restrictions.wholeAssetAllowed && (
                                        <button
                                            type="button"
                                            onClick={() => addContainer("/")}
                                            className={`${btnSecondary} !py-1 !text-xs`}
                                        >
                                            Add whole asset
                                        </button>
                                    )}
                                    {restrictions.folderAllowed && folderKey && (
                                        <button
                                            type="button"
                                            onClick={() => addContainer(folderKey)}
                                            className={`${btnSecondary} !py-1 !text-xs`}
                                        >
                                            Add folder {folderKey}
                                        </button>
                                    )}
                                </div>
                            )}
                    </div>
                )}

                {mode === "paste" && (
                    <div className="space-y-2">
                        <label className="block">
                            <span className="block text-xs text-text-secondary mb-1">
                                Relative file keys, one per line (for the asset above)
                            </span>
                            <textarea
                                aria-label="Relative file keys"
                                rows={8}
                                value={pasteText}
                                disabled={!scopeChosen}
                                onChange={(e) => setPasteText(e.target.value)}
                                placeholder={"/scans/001.e57\n/scans/002.e57"}
                                className={`${fieldClass} font-mono`}
                            />
                        </label>
                        <p className="text-xs text-text-secondary" aria-live="polite">
                            {pluralize(pasted.total, "key")}
                            {pasted.incompatible > 0 && ` · ${pasted.incompatible} not compatible`}
                            {pasted.alreadySelected > 0 &&
                                ` · ${pasted.alreadySelected} already selected`}
                            {pasted.addable.length <
                                pasted.total - pasted.incompatible - pasted.alreadySelected &&
                                ` · stopped at the ${MAX_INPUT_FILES_PER_EXECUTION}-file limit`}
                        </p>
                        {pasted.incompatible > 0 && (
                            <p className="text-xs text-text-secondary">
                                Keys the resolved filters reject are left out; a key ending in
                                {" / "}is a folder, and a bare {normalizeRelativeKey("/")} is the
                                whole asset.
                            </p>
                        )}
                    </div>
                )}
            </div>
        </Dialog>
    );
};

export default BulkFilePicker;

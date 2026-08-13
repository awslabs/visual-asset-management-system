/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useAssetSearch, useAssetFileSearch, useFileVersions } from "../api/queries";
import SearchableSelect from "../components/SearchableSelect";
import { applyInputFileFilters } from "./ExecuteWizard";
import type { ExecuteInputFile } from "../types";

type InputFileFilters = { allow?: string[]; exclude?: string[] };

interface InputFileSelectorProps {
    /** Databases the user may pick from (from useDatabases). GLOBAL is not a real asset database. */
    databaseOptions: { databaseId: string }[];
    /** When set, the database is fixed (database-scoped page) and the database picker is hidden. */
    lockedDatabaseId?: string;
    value: ExecuteInputFile;
    onChange: (file: ExecuteInputFile) => void;
    /** Whether to offer the optional version selector (defaults true). */
    showVersion?: boolean;
    /** Whether "Whole asset (all files)" is an allowed file choice. Defaults off: an omitted scope
     *  key is not a grant anywhere else in the chain either, so the option is offered only when a
     *  caller has resolved that every level accepts it. */
    allowWholeAsset?: boolean;
    /** Whether a folder is an allowed file choice, resolved the same way as `allowWholeAsset`. */
    allowFolder?: boolean;
    /** Fetch the version list on first interaction with the selector instead of on mount. Set by a
     *  caller rendering many rows, so one launch does not open a request per row up front. */
    deferVersions?: boolean;
    /** The RESOLVED input-file filters — the workflow's, its pipelines' and the chosen templates'.
     *  Files the run would reject are hidden rather than offered and then failed at validation. */
    inputFileFilters?: InputFileFilters;
}

const selectClass =
    "w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary focus:outline-none focus:ring-2 focus:ring-blue-500";

/**
 * Whether a picker's page describes the scope that is now selected.
 *
 * Both pickers keep the previous page on screen while the next one loads, so the list does not flash
 * empty on every keystroke. That hold also spans a DATABASE or ASSET change, where the held page
 * describes entities that are not in the new scope at all — a pick from it emits a pair that does not
 * exist. A page is offered only once the scope it was fetched for is the scope selected.
 */
function useCurrentScope(scope: string, isPreviousPage?: boolean): boolean {
    const settled = React.useRef(scope);
    if (!isPreviousPage) settled.current = scope;
    return settled.current === scope;
}

/** The folders the given file paths sit in, as selectable keys (leading and trailing '/'). */
function foldersOf(fileKeys: string[]): string[] {
    const dirs = new Set<string>();
    fileKeys.forEach((key) => {
        const segments = (key || "").split("/").filter(Boolean);
        segments.pop();
        let prefix = "";
        segments.forEach((segment) => {
            prefix += `/${segment}`;
            dirs.add(`${prefix}/`);
        });
    });
    return Array.from(dirs).sort();
}

/**
 * Cascading selector for one workflow input file: Database -> Asset -> File -> (optional) Version.
 * Replaces the free-text databaseId/assetId/relativeFileKey/versionId fields — the user searches
 * and picks from existing entities instead of typing identifiers, matching the asset/version UX
 * elsewhere in the app. `relativeFileKey` is the asset-relative file path (leading '/').
 */
const InputFileSelector: React.FC<InputFileSelectorProps> = ({
    databaseOptions,
    lockedDatabaseId,
    value,
    onChange,
    showVersion = true,
    allowWholeAsset = false,
    allowFolder = false,
    deferVersions = false,
    inputFileFilters,
}) => {
    const databaseId = lockedDatabaseId || value.databaseId || "";
    const assetId = value.assetId || "";

    // Asset lookup goes to the SERVER per search term rather than loading a database's whole asset list
    // for client-side filtering — a database can hold thousands of assets. The empty initial term returns
    // the first page, which seeds the picker before the user types.
    const [assetQuery, setAssetQuery] = React.useState("");
    const assetSearch = useAssetSearch(assetQuery, databaseId, !!databaseId);
    const assetsAreCurrent = useCurrentScope(databaseId, assetSearch.isPlaceholderData);
    const assetPage = assetsAreCurrent ? assetSearch.data : undefined;
    const assetsLoading = assetSearch.isFetching || !assetsAreCurrent;
    const assets = assetPage?.items || [];
    const assetTotal = assetPage?.total ?? 0;
    // Says so when the list is a capped page of a larger result set, so a missing asset reads as "refine
    // the search" rather than "not there".
    const assetFooter =
        assetTotal > assets.length
            ? `Showing ${assets.length} of ${assetTotal} — refine the search to narrow it` +
              (assetPage?.listFallback ? " (search unavailable; filtered locally)" : "")
            : undefined;
    // Files resolve server-side per search term too — an asset can hold thousands of files, so the
    // same reasoning as the asset picker applies.
    const [fileQuery, setFileQuery] = React.useState("");
    const fileSearch = useAssetFileSearch(fileQuery, databaseId, assetId);
    const filesAreCurrent = useCurrentScope(
        `${databaseId}/${assetId}`,
        fileSearch.isPlaceholderData
    );
    const filePage = filesAreCurrent ? fileSearch.data : undefined;
    const filesLoading = fileSearch.isFetching || !filesAreCurrent;
    // Only files the RUN admits are offered. Showing a file the resolved filters reject would let the
    // user select it and then fail validation on the next step for a reason the picker already knew.
    const files = React.useMemo(
        () =>
            applyInputFileFilters(
                (filePage?.items || []).map((f) => ({
                    databaseId,
                    assetId,
                    relativeFileKey: f.relativePath,
                })),
                inputFileFilters
            ),
        [filePage, inputFileFilters, databaseId, assetId]
    );
    // Folders are derived from the file page rather than listed: both file-resolution paths drop
    // folder entries, so the paths themselves are the only record of the asset's folder structure.
    const folders = React.useMemo(
        () =>
            allowFolder
                ? applyInputFileFilters(
                      foldersOf((filePage?.items || []).map((f) => f.relativePath)).map((key) => ({
                          databaseId,
                          assetId,
                          relativeFileKey: key,
                      })),
                      inputFileFilters
                  )
                : [],
        [allowFolder, filePage, inputFileFilters, databaseId, assetId]
    );
    const fileTotal = filePage?.total ?? 0;
    const hiddenByFilters = (filePage?.items?.length || 0) - files.length;
    const fileFooter = [
        fileTotal > (filePage?.items?.length || 0)
            ? `Showing ${filePage?.items?.length} of ${fileTotal} — refine the search to narrow it`
            : undefined,
        hiddenByFilters > 0
            ? `${hiddenByFilters} file${hiddenByFilters === 1 ? "" : "s"} hidden by the ` +
              `input-file filters`
            : undefined,
        filePage?.listFallback ? "search unavailable; filtered locally" : undefined,
    ]
        .filter(Boolean)
        .join(" · ");
    // Version history is fetched only once this row's selector is reached, when the caller defers it:
    // a launch can carry hundreds of rows, and a list nobody opens is a request nobody needed.
    const [versionsWanted, setVersionsWanted] = React.useState(!deferVersions);
    // Scoped to the SELECTED FILE, not the asset: `versionId` is sent to S3 as the object version of
    // this exact key, so each row's list is its own file's version history.
    const { data: versions, isFetching: versionsLoading } = useFileVersions(
        showVersion && versionsWanted ? databaseId : undefined,
        assetId,
        value.relativeFileKey
    );

    // Default file selection: whole asset when allowed, else empty (forces an explicit file pick).
    const defaultFileKey = allowWholeAsset ? "/" : "";
    // Selecting a new database resets the downstream asset/file/version selection.
    const handleDatabase = (dbId: string) => {
        // Clear the asset search as well: a term typed for the previous database would otherwise carry
        // over and silently narrow the new database's first page.
        setAssetQuery("");
        setFileQuery("");
        onChange({ databaseId: dbId, assetId: "", relativeFileKey: defaultFileKey });
    };
    // Selecting a new asset resets the downstream file/version selection. The file search term is
    // cleared for the same reason the asset term is on a database change.
    const handleAsset = (aId: string) => {
        setFileQuery("");
        onChange({ databaseId, assetId: aId, relativeFileKey: defaultFileKey });
    };
    const handleFile = (relativeFileKey: string) => {
        onChange({ ...value, databaseId, assetId, relativeFileKey, versionId: undefined });
    };
    const handleVersion = (versionId: string) => {
        onChange({ ...value, versionId: versionId || undefined });
    };
    // A whole-asset ('/') or folder ('/dir/') selection is not a single object, so it has no version.
    const isConcreteFile = !!value.relativeFileKey && !value.relativeFileKey.endsWith("/");

    return (
        <div className="space-y-2">
            {!lockedDatabaseId && (
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
            )}

            <label className="block">
                <span className="block text-xs text-text-secondary mb-1">Asset</span>
                {/* Searchable so a large database's assets can be typed-to-filter, not scrolled. */}
                <SearchableSelect
                    ariaLabel="Asset"
                    value={assetId}
                    disabled={!databaseId}
                    loading={assetsLoading}
                    placeholder={databaseId ? "Search assets…" : "Select a database first"}
                    onChange={handleAsset}
                    onQueryChange={setAssetQuery}
                    footerNote={assetFooter}
                    options={(assets || []).map((a) => ({
                        value: a.assetId,
                        label: a.assetName || a.assetId,
                        detail: a.assetName ? a.assetId : undefined,
                    }))}
                />
            </label>

            <label className="block">
                <span className="block text-xs text-text-secondary mb-1">File</span>
                {/* '/' = the whole asset (all files); the arity/handler treats a bare slash as the
                    asset root rather than a single file. Offered as the leading option ONLY when the
                    pipeline/workflow allows a whole-asset input — otherwise the user must pick a
                    specific file. A trailing-slash key is a folder, offered on the same condition. */}
                <SearchableSelect
                    ariaLabel="File"
                    value={value.relativeFileKey || ""}
                    disabled={!assetId}
                    loading={filesLoading}
                    placeholder={assetId ? "Search files…" : "Select an asset first"}
                    onChange={handleFile}
                    onQueryChange={setFileQuery}
                    footerNote={fileFooter || undefined}
                    leadingOption={
                        allowWholeAsset
                            ? { value: "/", label: "Whole asset (all files)" }
                            : undefined
                    }
                    options={[
                        ...folders.map((f) => ({
                            value: f.relativeFileKey,
                            label: f.relativeFileKey,
                            detail: "Folder (all files under it)",
                        })),
                        ...files.map((f) => ({
                            value: f.relativeFileKey,
                            label: f.relativeFileKey,
                        })),
                    ]}
                />
            </label>

            {/* Only a concrete file has versions — a whole-asset ('/') or folder selection spans many
                files, so there is no single object version to pin. */}
            {showVersion && isConcreteFile && (
                <label className="block">
                    <span className="block text-xs text-text-secondary mb-1">
                        File version (optional)
                    </span>
                    <select
                        aria-label="File version"
                        value={value.versionId || ""}
                        onChange={(e) => handleVersion(e.target.value)}
                        // Reaching the control is what asks for the history, so a deferred row costs
                        // nothing until someone actually looks at its versions.
                        onFocus={() => setVersionsWanted(true)}
                        onMouseDown={() => setVersionsWanted(true)}
                        className={selectClass}
                        disabled={versionsLoading && !versions}
                    >
                        {/* Empty is the default, so a run reads whatever is current at launch rather
                            than a version pinned when the form was filled in. */}
                        <option value="">
                            {versionsLoading && !versions ? "Loading versions…" : "Latest"}
                        </option>
                        {(versions || []).map((v) => (
                            <option key={v.versionId} value={v.versionId}>
                                {v.isLatest ? "Current" : v.versionId}
                                {v.lastModified ? ` — ${v.lastModified}` : ""}
                                {v.isLatest ? ` (${v.versionId})` : ""}
                            </option>
                        ))}
                    </select>
                </label>
            )}
        </div>
    );
};

export default InputFileSelector;

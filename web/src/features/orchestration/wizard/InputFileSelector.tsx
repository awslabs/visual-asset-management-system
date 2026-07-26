/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useAssets, useAssetFiles, useAssetVersions } from "../api/queries";
import SearchableSelect from "../components/SearchableSelect";
import type { ExecuteInputFile } from "../types";

interface InputFileSelectorProps {
    /** Databases the user may pick from (from useDatabases). GLOBAL is not a real asset database. */
    databaseOptions: { databaseId: string }[];
    /** When set, the database is fixed (database-scoped page) and the database picker is hidden. */
    lockedDatabaseId?: string;
    value: ExecuteInputFile;
    onChange: (file: ExecuteInputFile) => void;
    /** Whether to offer the optional version selector (defaults true). */
    showVersion?: boolean;
    /** Whether "Whole asset (all files)" is an allowed file choice. When false (a pipeline that
     *  requires specific files), the whole-asset option is hidden so the user must pick a file. */
    allowWholeAsset?: boolean;
}

const selectClass =
    "w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary focus:outline-none focus:ring-2 focus:ring-blue-500";

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
    allowWholeAsset = true,
}) => {
    const databaseId = lockedDatabaseId || value.databaseId || "";
    const assetId = value.assetId || "";

    const { data: assets, isLoading: assetsLoading } = useAssets(databaseId, !!databaseId);
    const { data: files, isLoading: filesLoading } = useAssetFiles(databaseId, assetId);
    const { data: versions } = useAssetVersions(showVersion ? databaseId : undefined, assetId);

    // Default file selection: whole asset when allowed, else empty (forces an explicit file pick).
    const defaultFileKey = allowWholeAsset ? "/" : "";
    // Selecting a new database resets the downstream asset/file/version selection.
    const handleDatabase = (dbId: string) => {
        onChange({ databaseId: dbId, assetId: "", relativeFileKey: defaultFileKey });
    };
    // Selecting a new asset resets the downstream file/version selection.
    const handleAsset = (aId: string) => {
        onChange({ databaseId, assetId: aId, relativeFileKey: defaultFileKey });
    };
    const handleFile = (relativeFileKey: string) => {
        onChange({ ...value, databaseId, assetId, relativeFileKey, versionId: undefined });
    };
    const handleVersion = (versionId: string) => {
        onChange({ ...value, versionId: versionId || undefined });
    };

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
                    specific file. */}
                <SearchableSelect
                    ariaLabel="File"
                    value={value.relativeFileKey || ""}
                    disabled={!assetId}
                    loading={filesLoading}
                    placeholder={assetId ? "Search files…" : "Select an asset first"}
                    onChange={handleFile}
                    leadingOption={
                        allowWholeAsset
                            ? { value: "/", label: "Whole asset (all files)" }
                            : undefined
                    }
                    options={(files || []).map((f) => ({
                        value: f.relativePath,
                        label: f.relativePath,
                    }))}
                />
            </label>

            {showVersion && versions && versions.length > 0 && value.relativeFileKey !== "/" && (
                <label className="block">
                    <span className="block text-xs text-text-secondary mb-1">
                        Version (optional)
                    </span>
                    <select
                        aria-label="Version"
                        value={value.versionId || ""}
                        onChange={(e) => handleVersion(e.target.value)}
                        className={selectClass}
                    >
                        <option value="">Latest</option>
                        {versions.map((v) => (
                            <option key={v.versionId} value={v.versionId}>
                                {v.versionId}
                                {v.dateCreated ? ` — ${v.dateCreated}` : ""}
                            </option>
                        ))}
                    </select>
                </label>
            )}
        </div>
    );
};

export default InputFileSelector;

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useAssetSearch } from "../api/queries";
import SearchableSelect from "../components/SearchableSelect";
import type { MetadataSourceAsset } from "../types";

interface MetadataSourceSelectorProps {
    /** Databases the user may pick from (from useDatabases). */
    databaseOptions: { databaseId: string }[];
    value: MetadataSourceAsset;
    onChange: (source: MetadataSourceAsset) => void;
}

const selectClass =
    "w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary focus:outline-none focus:ring-2 focus:ring-blue-500";

/**
 * Cascading selector for ONE metadata-source asset: Database -> Asset.
 *
 * A reduced {@link InputFileSelector}: a metadata source is an entity, never a file, so there is no
 * file or version step — the asset's stored metadata is what the run reads. Assets resolve server-side
 * per search term, the same way the input-file pickers do (a database can hold thousands).
 */
const MetadataSourceSelector: React.FC<MetadataSourceSelectorProps> = ({
    databaseOptions,
    value,
    onChange,
}) => {
    const databaseId = value.databaseId || "";
    const assetId = value.assetId || "";

    const [assetQuery, setAssetQuery] = React.useState("");
    const { data: assetPage, isFetching: assetsLoading } = useAssetSearch(
        assetQuery,
        databaseId,
        !!databaseId
    );
    const assets = assetPage?.items || [];
    const assetTotal = assetPage?.total ?? 0;
    // Says so when the list is a capped page of a larger result set, so a missing asset reads as
    // "refine the search" rather than "not there".
    const assetFooter =
        assetTotal > assets.length
            ? `Showing ${assets.length} of ${assetTotal} — refine the search to narrow it` +
              (assetPage?.listFallback ? " (search unavailable; filtered locally)" : "")
            : undefined;

    // Selecting a new database resets the asset, and clears the search term so a term typed for the
    // previous database does not silently narrow the new one's first page.
    const handleDatabase = (dbId: string) => {
        setAssetQuery("");
        onChange({ databaseId: dbId, assetId: "" });
    };

    return (
        <div className="space-y-2">
            <label className="block">
                <span className="block text-xs text-text-secondary mb-1">Database</span>
                {/* Labelled distinctly from the run's single "Metadata source database" (the
                    databaseMetadata source): this one only scopes the asset search below it. */}
                <select
                    aria-label="Metadata source asset database"
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
                    ariaLabel="Metadata source asset"
                    value={assetId}
                    disabled={!databaseId}
                    loading={assetsLoading}
                    placeholder={databaseId ? "Search assets…" : "Select a database first"}
                    onChange={(aId) => onChange({ databaseId, assetId: aId })}
                    onQueryChange={setAssetQuery}
                    footerNote={assetFooter}
                    options={(assets || []).map((a) => ({
                        value: a.assetId,
                        label: a.assetName || a.assetId,
                        detail: a.assetName ? a.assetId : undefined,
                    }))}
                />
            </label>
        </div>
    );
};

export default MetadataSourceSelector;

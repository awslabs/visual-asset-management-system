/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    fetchDatabaseAssets,
    fetchAllAssets,
    fetchAssetS3Files,
    fetchFileInfo,
    searchAssets,
} from "../../../services/APIService";
import { appCache } from "../../../services/appCache";

/**
 * Asset / file / version lookups for the execute wizard's selectors. These are core-entity reads, so
 * they delegate to the registered functions in `services/APIService.ts` (Rule 3 — only service files
 * touch apiClient) and adapt the various legacy return shapes into the orchestration module's
 * `[ok, data]` tuple.
 */

/**
 * An EXACT-match filter on an id field.
 *
 * Two constraints have to hold at once:
 *
 * 1. The backend's `SearchFilterModel` REQUIRES a `query_string` key (models/search.py) — a bare
 *    `term` filter is rejected with `filters.0.query_string: field required` (400).
 * 2. These id fields are analyzed, so the standard analyzer splits on hyphens. A quoted phrase on the
 *    ANALYZED field searches for adjacent tokens: `str_databaseid:("smoke-db")` matches [smoke, db],
 *    which `smoke-db-2` ([smoke, db, 2]) also contains. Verified against the deployed index — that
 *    filter returned 24 smoke-db assets PLUS 1 from smoke-db-2.
 *
 * Targeting the `.keyword` subfield THROUGH a query_string satisfies both: exact match, accepted shape.
 * Verified live: `str_databaseid.keyword:"smoke-db"` returns exactly the 24.
 */
function exactTermFilter(field: string, value: string): object {
    // Quote the value so a hyphenated id is one term rather than several, and escape any embedded
    // quote or backslash so the query_string stays parseable.
    const escaped = value.replace(/[\\"]/g, (c) => "\\" + c);
    return { query_string: { query: `${field}.keyword:"${escaped}"` } };
}

/**
 * Whether a databaseId means "all databases" rather than one specific database.
 *
 * "GLOBAL" is the shared PIPELINE/WORKFLOW catalog, not an asset database: passing it to an
 * asset/file endpoint is a 400. Treating it as unscoped is what the user means by it here.
 */
export function isAllDatabases(databaseId?: string): boolean {
    return !databaseId || databaseId === "GLOBAL";
}

/** Exact-match filter for the database id — see {@link exactTermFilter}. */
function databaseIdFilter(databaseId: string): object {
    return exactTermFilter("str_databaseid", databaseId);
}

export interface AssetSummary {
    databaseId: string;
    assetId: string;
    assetName?: string;
}

export interface AssetFileSummary {
    fileName: string;
    key: string;
    relativePath: string; // asset-relative, leading '/'
    isFolder: boolean;
    versionId?: string;
}

/** One S3 object version of a single file. `versionId` is what an execution sends as `versionId`. */
export interface AssetFileVersionSummary {
    versionId: string;
    relativeKey: string;
    isLatest?: boolean;
    lastModified?: string;
    size?: number;
    // Version records carry additional fields (changeSource, etag, ...) we surface opportunistically.
    [k: string]: any;
}

/** One page of asset search results, plus the server's total so a caller can say "showing N of M". */
export interface AssetSearchPage {
    items: AssetSummary[];
    total: number;
    /** True when the result set came from the list endpoint because OpenSearch is disabled. */
    listFallback: boolean;
}

/** Whether this deployment has OpenSearch turned off (the NOOPENSEARCH feature switch). */
function openSearchDisabled(): boolean {
    try {
        const config: any = appCache.getItem("config");
        return !!config?.featuresEnabled?.includes("NOOPENSEARCH");
    } catch {
        // A missing/unreadable config is treated as "search available"; a failed search still falls
        // back below, so this cannot strand the picker.
        return false;
    }
}

/**
 * One page of assets matching `query`, resolved SERVER-side.
 *
 * The picker must scale to thousands of assets per database, so the query goes to the server rather than
 * loading everything and filtering in memory. Two paths, matching the rest of the app
 * (components/searchSmall/AssetSearchTable.tsx):
 *   - OpenSearch `search` endpoint — full-text, server-paginated, with an optional database filter.
 *   - the asset LIST endpoint when NOOPENSEARCH is set, or when a search call fails: the only option
 *     available, so it is filtered client-side and flagged via `listFallback` so the caller can say the
 *     result may be partial.
 *
 * An empty `query` returns the first page unfiltered, which is what seeds the picker before typing.
 */
export async function searchAssetsPaged(
    query: string,
    databaseId?: string,
    pageSize = 100
): Promise<[boolean, AssetSearchPage | string]> {
    const term = (query || "").trim();

    if (!openSearchDisabled()) {
        try {
            const filters: object[] = [];
            // Not filtered when the id means "all databases": GLOBAL is the pipeline/workflow catalog,
            // never a value of str_databaseid, so filtering on it would return nothing at all.
            if (!isAllDatabases(databaseId)) {
                filters.push(databaseIdFilter(databaseId as string));
            }
            const [ok, result] = (await searchAssets({
                query: term,
                from: 0,
                size: pageSize,
                entityTypes: ["asset"],
                includeMetadataInSearch: false,
                filters: filters.length ? filters : undefined,
                aggregations: false,
                includeHighlights: false,
                explainResults: false,
                includeArchived: false,
            })) as [boolean, any];
            if (ok && result?.hits?.hits) {
                return [
                    true,
                    {
                        items: result.hits.hits.map((hit: any) => ({
                            databaseId: hit._source?.str_databaseid || "",
                            assetId: hit._source?.str_assetid || "",
                            assetName: hit._source?.str_assetname || "",
                        })),
                        total: result.hits.total?.value ?? result.hits.hits.length,
                        listFallback: false,
                    },
                ];
            }
            // A search that returns no hit envelope is treated as unavailable rather than as "no
            // matches", so a search outage degrades to the list path instead of showing an empty picker.
        } catch {
            // fall through to the list path
        }
    }

    const [ok, assets] = await listAssets(databaseId);
    if (!ok || typeof assets === "string") {
        return [false, typeof assets === "string" ? assets : "Failed to load assets."];
    }
    const needle = term.toLowerCase();
    const matches = needle
        ? (assets as AssetSummary[]).filter(
              (a) =>
                  (a.assetName || "").toLowerCase().includes(needle) ||
                  (a.assetId || "").toLowerCase().includes(needle)
          )
        : (assets as AssetSummary[]);
    return [true, { items: matches.slice(0, pageSize), total: matches.length, listFallback: true }];
}

/**
 * Assets in one database, or across every database the caller can see.
 *
 * "Every database" is expressed as an empty databaseId OR the literal "GLOBAL". GLOBAL is a real
 * database id for PIPELINES and WORKFLOWS (the shared catalog), but there is no GLOBAL asset
 * database — the assets endpoint rejects it outright with
 * `databaseId is invalid. GLOBAL is not allowed for this field.` (400). A workflow-scoped caller
 * naturally holds "GLOBAL" as its database, so the coercion lives here rather than in each caller.
 */
export async function listAssets(databaseId?: string): Promise<[boolean, AssetSummary[] | string]> {
    try {
        const scoped = databaseId && !isAllDatabases(databaseId) ? databaseId : undefined;
        const result = scoped
            ? await fetchDatabaseAssets({ databaseId: scoped })
            : await fetchAllAssets({});
        if (Array.isArray(result)) return [true, result as AssetSummary[]];
        return [false, "Failed to load assets."];
    } catch (e: any) {
        return [false, e?.message || "Failed to load assets."];
    }
}

/** Non-folder files for an asset (the wizard picks a file, not a folder). */
export async function listAssetFiles(
    databaseId: string,
    assetId: string
): Promise<[boolean, AssetFileSummary[] | string]> {
    try {
        const [ok, data] = await fetchAssetS3Files({ databaseId, assetId, includeArchived: false });
        if (ok && Array.isArray(data)) {
            return [true, (data as AssetFileSummary[]).filter((f) => !f.isFolder)];
        }
        return [false, typeof data === "string" ? data : "Failed to load files."];
    } catch (e: any) {
        return [false, e?.message || "Failed to load files."];
    }
}

/** One page of file results, shaped like AssetSearchPage so the pickers read the same. */
export interface AssetFilePage {
    items: AssetFileSummary[];
    total: number;
    /** True when the rows came from the direct file listing rather than the search index. */
    listFallback: boolean;
}

/**
 * One page of an asset's files matching `query`.
 *
 * Mirrors `searchAssetsPaged`: the search index resolves the term server-side when available, so an
 * asset holding thousands of files does not have to be pulled into the browser to be filtered. The
 * `file` entity type is indexed separately from `asset` (handlers/indexing/fileIndexer.py), keyed by
 * `str_key` (the asset-relative path) with `str_assetid` / `str_databaseid` scoping it.
 *
 * Falls back to the direct listing when OpenSearch is off, when the call fails, or when the response
 * carries no hit envelope — and flags it via `listFallback`, since that path is filtered client-side
 * and so is only as complete as the listing itself.
 */
export async function searchAssetFilesPaged(
    query: string,
    databaseId: string,
    assetId: string,
    pageSize = 100
): Promise<[boolean, AssetFilePage | string]> {
    const term = (query || "").trim();

    if (databaseId && assetId && !openSearchDisabled()) {
        try {
            const [ok, result] = (await searchAssets({
                query: term,
                from: 0,
                size: pageSize,
                entityTypes: ["file"],
                includeMetadataInSearch: false,
                filters: [databaseIdFilter(databaseId), exactTermFilter("str_assetid", assetId)],
                aggregations: false,
                includeHighlights: false,
                explainResults: false,
                includeArchived: false,
            })) as [boolean, any];
            if (ok && result?.hits?.hits) {
                const items = result.hits.hits
                    .map((hit: any) => {
                        // str_key is already asset-relative; normalize the leading slash so the value
                        // matches what the execute request and the filter matcher expect.
                        const key: string = hit._source?.str_key || "";
                        const relativePath = key ? (key.startsWith("/") ? key : `/${key}`) : "";
                        return {
                            fileName: relativePath.split("/").pop() || relativePath,
                            key: relativePath,
                            relativePath,
                            isFolder: relativePath.endsWith("/"),
                        } as AssetFileSummary;
                    })
                    .filter((f: AssetFileSummary) => f.relativePath && !f.isFolder);
                return [
                    true,
                    {
                        items,
                        total: result.hits.total?.value ?? items.length,
                        listFallback: false,
                    },
                ];
            }
        } catch {
            // fall through to the direct listing
        }
    }

    const [ok, files] = await listAssetFiles(databaseId, assetId);
    if (!ok || typeof files === "string") {
        return [false, typeof files === "string" ? files : "Failed to load files."];
    }
    const needle = term.toLowerCase();
    const matches = needle
        ? (files as AssetFileSummary[]).filter((f) =>
              (f.relativePath || "").toLowerCase().includes(needle)
          )
        : (files as AssetFileSummary[]);
    return [true, { items: matches.slice(0, pageSize), total: matches.length, listFallback: true }];
}

/**
 * The S3 object versions of ONE file, for the per-file version selector.
 *
 * Deliberately NOT the asset's VAMS versions. An execution's `versionId` is handed straight to S3
 * (`head_object(VersionId=...)` in `executeWorkflow._input_exists_in_s3`), so it must be an S3 object
 * VersionId for that exact key. An asset-version id would fail the pre-launch existence check, and an
 * asset-scoped list would show the same options for every file in the asset regardless of which one is
 * selected.
 *
 * Delete markers are dropped: selecting one names a version that no longer holds bytes.
 */
export async function listFileVersions(
    databaseId: string,
    assetId: string,
    relativeFileKey: string
): Promise<[boolean, AssetFileVersionSummary[] | string]> {
    try {
        const [ok, data] = (await fetchFileInfo({
            databaseId,
            assetId,
            fileKey: relativeFileKey,
            includeVersions: true,
        })) as [boolean, any];
        if (!ok) {
            return [false, typeof data === "string" ? data : "Failed to load file versions."];
        }
        const versions = Array.isArray(data?.versions) ? data.versions : [];
        return [
            true,
            versions
                .filter((v: any) => v?.versionId && !v.isArchived)
                .map(
                    (v: any) => ({ ...v, relativeKey: relativeFileKey } as AssetFileVersionSummary)
                ),
        ];
    } catch (e: any) {
        return [false, e?.message || "Failed to load file versions."];
    }
}

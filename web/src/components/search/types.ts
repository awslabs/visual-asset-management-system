/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export interface SearchFilters {
    _rectype?: {
        label: string;
        value: "asset" | "file";
    };
    str_databaseid?: {
        label: string;
        value: string;
        values?: string[]; // Support multi-select
    };
    str_assettype?: {
        label: string;
        value: string;
        values?: string[]; // Support multi-select
    };
    str_fileext?: {
        label: string;
        value: string;
        values?: string[]; // Support multi-select
    };
    list_tags?: {
        label: string;
        value: string;
        values?: string[]; // Support multi-select
    };
    includeMetadataInKeywordSearch?: boolean;
    showResultExplanation?: boolean;
    includeSegments?: boolean; // `Search inside files` (NLP mode); false only when cleared
    bool_has_asset_children?: {
        value: boolean;
    } | null;
    bool_has_asset_parents?: {
        value: boolean;
    } | null;
    bool_has_assets_related?: {
        value: boolean;
    } | null;
    date_lastmodified_filter?: {
        operator: ">" | "<" | "=" | "between";
        value: string | string[]; // ISO date string(s)
    } | null;
    num_filesize_filter?: {
        operator: ">" | "<" | "=" | "between";
        value: number | number[]; // Size in bytes
        unit?: "bytes" | "KB" | "MB" | "GB"; // For display purposes
    } | null;
    str_assetname?: {
        value: string;
    } | null;
    geo_filter?: GeoSearchFilter | null;
    [key: string]: any;
}

/**
 * Geospatial filter against the geo_MD_location field on search documents.
 * Mirrors the backend GeoSearchModel: exactly one of point, bbox, or geoJson.
 */
export interface GeoSearchFilter {
    relation?: "intersects" | "within" | "contains" | "disjoint";
    point?: {
        lat: number;
        lon: number;
        radiusMeters?: number;
    };
    bbox?: {
        topLeft: { lat: number; lon: number };
        bottomRight: { lat: number; lon: number };
    };
    geoJson?: any;
}

export interface MetadataFilter {
    key: string;
    value: string;
    operator: "=" | "!=" | ">" | "<" | ">=" | "<=" | "contains";
    type: "string" | "number" | "date" | "boolean";
    // fieldType removed - all metadata searches now use string type
}

export interface SearchQuery {
    query: string;
    filters: SearchFilters;
    metadataFilters: MetadataFilter[];
    sort: any[];
    pagination: {
        from: number;
        size: number;
    };
}

export interface SearchExplanation {
    matched_fields: string[];
    match_reasons: {
        [field: string]: string;
    };
    query_type: string;
    index_type: string;
    score_breakdown: {
        total_score: number;
        field_matches: number;
        highlight_matches: number;
    };
}

/**
 * The segment vector that scored best for a hit: a video time window (`videoTime`, with a
 * millisecond range) or a document content chunk (`textChunk`, range `null`). `null` on the hit
 * when the whole-file vector won.
 */
export interface NlpSegmentRef {
    segmentKey: string;
    segmentKind: string;
    segmentLabel: string;
    segmentStartMs: number | null;
    segmentEndMs: number | null;
}

/** Per-hit vector details the NLP search returns beside `_score`; never part of `_source`. */
export interface NlpVectorInfo {
    distance: number;
    embeddingModelId: string;
    sourceModalities: string[];
    indexedAt: string;
    fileClass: string;
    /** Segment vectors collapsed into this hit; `0` when only the whole-file vector matched. */
    segmentHits: number;
    bestSegment: NlpSegmentRef | null;
}

export interface SearchResult {
    _id: string;
    _score?: number;
    _index_type?: string;
    _vector?: NlpVectorInfo;
    _source: {
        str_assetid?: string;
        str_assetname?: string;
        str_databaseid?: string;
        str_assettype?: string;
        str_description?: string;
        str_key?: string;
        list_tags?: string[];
        date_created?: string;
        str_createdby?: string;
        num_size?: number;
        gp_location?: {
            lat: number;
            lon: number;
        };
        [key: string]: any;
    };
    explanation?: SearchExplanation;
}

export interface SearchResponse {
    hits: {
        total: {
            value: number;
            relation: string;
        };
        hits: SearchResult[];
    };
    aggregations?: {
        [key: string]: {
            buckets: Array<{
                key: string;
                doc_count: number;
            }>;
        };
    };
    aggregationTotal?: number;
}

export type SearchMode = "keyword" | "nlp";

/** Body of `POST /search/nlp`. The OpenSearch-only constraints are ignored (with a warning) when OpenSearch is off. */
export interface NlpSearchRequest {
    query: string;
    entityTypes?: ("asset" | "file")[];
    databaseIds?: string[];
    includeArchived?: boolean;
    includeSegments?: boolean;
    fileClasses?: string[];
    fileExtensions?: string[];
    size?: number;
    filters?: object[];
    metadataQuery?: string;
    metadataSearchMode?: string;
    geoSearch?: GeoSearchFilter;
    tags?: string[];
    enrich?: boolean;
}

export interface NlpSearchSummary {
    query: string;
    embeddingModelId: string;
    databasesSearched: number;
    candidatesEvaluated: number;
    /** Segment items folded into their file's hit. */
    itemsCollapsed: number;
    /** File classes the query's type words named; their hits are listed first. */
    classIntent: string[];
    truncated: boolean;
}

/**
 * One advisory attached to an otherwise successful natural-language response. `code` is one of a
 * fixed set (`truncated:window`, `truncated:targets`, `databases:none_accessible`,
 * `opensearch:fields_ignored`, `opensearch:enrichment_failed`, `segments:window_full` — a segment
 * search window filled while the answer is not already truncated); `message` is the text shown
 * to the user.
 */
export interface NlpSearchWarning {
    code: string;
    message: string;
}

/** The `POST /search` envelope plus the `nlp` summary and `warnings` the NLP route adds. */
export interface NlpSearchResponse extends SearchResponse {
    nlp: NlpSearchSummary;
    warnings: NlpSearchWarning[];
}

export interface SearchPreferences {
    viewMode: "table" | "card" | "map";
    assetTableColumns: string[]; // Columns for asset view
    fileTableColumns: string[]; // Columns for file view
    cardSize: "small" | "medium" | "large";
    pageSize: number;
    sortField: string;
    sortDirection: "asc" | "desc";
    showThumbnails: boolean;
    showMapThumbnails: boolean; // Show map thumbnails for assets with location data
    filterPresets: FilterPreset[];
    lastUsedFilters: SearchFilters;
    sidebarWidth?: number; // Width of the search sidebar (resizable)
    searchMode?: SearchMode; // Keyword vs natural-language search when both engines are enabled
}

export interface FilterPreset {
    id: string;
    name: string;
    filters: SearchFilters;
    metadataFilters: MetadataFilter[];
    createdAt: string;
}

export interface ToastNotification {
    id: string;
    type: "success" | "error" | "warning" | "info";
    title: string;
    message?: string;
    dismissible?: boolean;
    autoHide?: boolean;
    duration?: number;
}

export interface SearchContainerProps {
    mode: "full" | "modal" | "embedded";
    initialFilters?: SearchFilters;
    initialQuery?: string;
    onSelectionChange?: (items: SearchResult[]) => void;
    allowedViews?: ("table" | "card" | "map")[];
    showPreferences?: boolean;
    showBulkActions?: boolean;
    maxHeight?: string;
    databaseId?: string;
    embedded?: {
        title?: string;
        showHeader?: boolean;
        allowNavigation?: boolean;
    };
}

export interface FieldMapping {
    [key: string]: {
        label: string;
        type: "string" | "number" | "date" | "boolean" | "array";
        sortable?: boolean;
        filterable?: boolean;
        searchable?: boolean;
    };
}

export const FIELD_MAPPINGS: FieldMapping = {
    // Asset fields
    str_assetname: {
        label: "Asset Name",
        type: "string",
        sortable: true,
        filterable: true,
        searchable: true,
    },
    str_assetid: {
        label: "Asset ID",
        type: "string",
        sortable: true,
        filterable: true,
        searchable: true,
    },
    str_databaseid: {
        label: "Database",
        type: "string",
        sortable: true,
        filterable: true,
        searchable: true,
    },
    str_bucketid: {
        label: "Bucket ID",
        type: "string",
        sortable: true,
        filterable: true,
        searchable: false,
    },
    str_bucketname: {
        label: "Bucket Name",
        type: "string",
        sortable: true,
        filterable: false,
        searchable: false,
    },
    str_bucketprefix: {
        label: "Bucket Prefix",
        type: "string",
        sortable: true,
        filterable: false,
        searchable: false,
    },
    str_assettype: {
        label: "Asset Type",
        type: "string",
        sortable: true,
        filterable: true,
        searchable: true,
    },
    str_description: {
        label: "Description",
        type: "string",
        sortable: true,
        filterable: false,
        searchable: true,
    },
    bool_isdistributable: {
        label: "Distributable",
        type: "boolean",
        sortable: true,
        filterable: true,
        searchable: false,
    },
    list_tags: {
        label: "Tags",
        type: "array",
        sortable: false,
        filterable: true,
        searchable: true,
    },
    str_asset_version_id: {
        label: "Asset Version ID",
        type: "string",
        sortable: true,
        filterable: false,
        searchable: false,
    },
    date_asset_version_createdate: {
        label: "Version Created",
        type: "date",
        sortable: true,
        filterable: true,
        searchable: false,
    },
    str_asset_version_comment: {
        label: "Version Comment",
        type: "string",
        sortable: false,
        filterable: false,
        searchable: true,
    },
    bool_has_asset_children: {
        label: "Has Child Assets",
        type: "boolean",
        sortable: true,
        filterable: true,
        searchable: false,
    },
    bool_has_asset_parents: {
        label: "Has Parent Assets",
        type: "boolean",
        sortable: true,
        filterable: true,
        searchable: false,
    },
    bool_has_assets_related: {
        label: "Has Related Assets",
        type: "boolean",
        sortable: true,
        filterable: true,
        searchable: false,
    },

    // File fields
    str_key: {
        label: "File Path",
        type: "string",
        sortable: true,
        filterable: false,
        searchable: true,
    },
    str_fileext: {
        label: "Type",
        type: "string",
        sortable: true,
        filterable: true,
        searchable: true,
    },
    num_filesize: {
        label: "Size",
        type: "number",
        sortable: true,
        filterable: true,
        searchable: false,
    },
    num_size: {
        label: "Size",
        type: "number",
        sortable: true,
        filterable: true,
        searchable: false,
    },
    date_lastmodified: {
        label: "Last Modified",
        type: "date",
        sortable: true,
        filterable: true,
        searchable: false,
    },
    str_etag: {
        label: "ETag",
        type: "string",
        sortable: true,
        filterable: false,
        searchable: false,
    },
    str_s3_version_id: {
        label: "S3 Version",
        type: "string",
        sortable: true,
        filterable: false,
        searchable: false,
    },

    // Common fields
    bool_archived: {
        label: "Archived",
        type: "boolean",
        sortable: true,
        filterable: true,
        searchable: false,
    },
    str_rectype: {
        label: "Record Type",
        type: "string",
        sortable: true,
        filterable: true,
        searchable: false,
    },
    // Natural-language search only: the hit's `_score` (1 - cosine distance) shown as a percentage
    relevance: {
        label: "Relevance",
        type: "number",
        sortable: true,
        filterable: false,
        searchable: false,
    },

    // Metadata fields (dynamic)
    "MD_*": {
        label: "Metadata",
        type: "string",
        sortable: false,
        filterable: true,
        searchable: true,
    },
    // Metadata column (always available)
    metadata: {
        label: "Metadata",
        type: "string",
        sortable: false,
        filterable: false,
        searchable: false,
    },
};

/**
 * Get the best available total count from search results.
 * Prefers aggregationTotal (true total from OpenSearch aggregations) over
 * hits.total.value (which may be capped by the backend buffer window).
 */
export function getTotalResultCount(result: SearchResponse | null | undefined): number {
    if (!result) return 0;
    if (result.aggregationTotal != null && result.aggregationTotal > 0) {
        return result.aggregationTotal;
    }
    return result.hits?.total?.value || 0;
}

export const DEFAULT_PREFERENCES: SearchPreferences = {
    viewMode: "table",
    assetTableColumns: [
        "str_assetname",
        "str_databaseid",
        "str_assettype",
        "str_description",
        "str_asset_version_id",
        "list_tags",
        "metadata",
    ],
    fileTableColumns: [
        "str_key",
        "str_assetname",
        "str_databaseid",
        "str_fileext",
        "num_filesize",
        "date_lastmodified",
        "list_tags",
        "metadata",
    ],
    cardSize: "medium",
    pageSize: 50, // Default page size changed to 50
    sortField: "str_assetname",
    sortDirection: "asc",
    showThumbnails: false,
    showMapThumbnails: false, // Default off
    filterPresets: [],
    lastUsedFilters: {
        _rectype: {
            label: "Assets",
            value: "asset",
        },
    },
    sidebarWidth: 400, // Default sidebar width
    searchMode: "keyword",
};

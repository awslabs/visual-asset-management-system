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
    [key: string]: any;
}

export interface MetadataFilter {
    key: string;
    value: string;
    operator: "=" | "!=" | ">" | "<" | ">=" | "<=" | "contains";
    type: "string" | "number" | "date" | "boolean";
    fieldType: "str" | "num" | "bool" | "date" | "list" | "gp" | "gs";
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

export interface SearchResult {
    _id: string;
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
    filterPresets: FilterPreset[];
    lastUsedFilters: SearchFilters;
    sidebarWidth?: number; // Width of the search sidebar (resizable)
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
    _rectype: {
        label: "Record Type",
        type: "string",
        sortable: true,
        filterable: true,
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
    filterPresets: [],
    lastUsedFilters: {
        _rectype: {
            label: "Assets",
            value: "asset",
        },
    },
    sidebarWidth: 400, // Default sidebar width
};

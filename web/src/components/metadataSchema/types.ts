/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * TypeScript interfaces for Metadata Schema management
 */

export type MetadataSchemaEntityType =
    | "databaseMetadata"
    | "assetMetadata"
    | "fileMetadata"
    | "fileAttribute"
    | "assetLinkMetadata";

export type MetadataValueType =
    | "string"
    | "multiline_string"
    | "inline_controlled_list"
    | "number"
    | "boolean"
    | "date"
    | "xyz"
    | "wxyz"
    | "matrix4x4"
    | "geopoint"
    | "geojson"
    | "lla"
    | "json";

export interface MetadataSchemaField {
    metadataFieldKeyName: string;
    metadataFieldValueType: MetadataValueType;
    required: boolean;
    sequence?: number;
    dependsOnFieldKeyName?: string[];
    controlledListKeys?: string[];
    defaultMetadataFieldValue?: string;
}

export interface MetadataSchema {
    metadataSchemaId: string;
    databaseId: string;
    metadataSchemaEntityType: MetadataSchemaEntityType;
    schemaName: string;
    fileKeyTypeRestriction?: string;
    fields: {
        fields: MetadataSchemaField[];
    };
    enabled: boolean;
    dateCreated?: string;
    dateModified?: string;
    createdBy?: string;
    modifiedBy?: string;
}

export interface CreateMetadataSchemaRequest {
    databaseId: string;
    metadataSchemaEntityType: MetadataSchemaEntityType;
    schemaName: string;
    fileKeyTypeRestriction?: string;
    fields: {
        fields: MetadataSchemaField[];
    };
    enabled: boolean;
}

export interface UpdateMetadataSchemaRequest {
    metadataSchemaId: string;
    schemaName?: string;
    fileKeyTypeRestriction?: string;
    fields?: {
        fields: MetadataSchemaField[];
    };
    enabled?: boolean;
}

export interface DeleteMetadataSchemaRequest {
    confirmDelete: boolean;
}

export interface MetadataSchemaOperationResponse {
    success: boolean;
    message: string;
    metadataSchemaId: string;
    operation: "create" | "update" | "delete";
    timestamp: string;
}

export interface GetMetadataSchemasResponse {
    Items: MetadataSchema[];
    NextToken?: string;
    message?: string;
}

export const ENTITY_TYPE_LABELS: Record<MetadataSchemaEntityType, string> = {
    databaseMetadata: "Database Metadata",
    assetMetadata: "Asset Metadata",
    fileMetadata: "File Metadata",
    fileAttribute: "File Attribute",
    assetLinkMetadata: "Asset Link Metadata",
};

export const VALUE_TYPE_LABELS: Record<MetadataValueType, string> = {
    string: "String",
    multiline_string: "Multiline String",
    inline_controlled_list: "Inline Controlled List",
    number: "Number",
    boolean: "Boolean",
    date: "Date",
    xyz: "XYZ (3D Coordinates)",
    wxyz: "WXYZ (Quaternion)",
    matrix4x4: "Matrix 4x4",
    geopoint: "GeoPoint",
    geojson: "GeoJSON",
    lla: "LLA (Lat/Long/Alt)",
    json: "JSON",
};

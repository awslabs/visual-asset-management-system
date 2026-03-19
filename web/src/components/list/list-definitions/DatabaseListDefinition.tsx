/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ListDefinition from "./types/ListDefinition";
import { Link } from "@cloudscape-design/components";
import ColumnDefinition from "./types/ColumnDefinition";
import Synonyms from "../../../synonyms";

/**
 * Creates a DatabaseListDefinition with optional callbacks and map thumbnail support.
 * @param {Object} options
 * @param {Function} options.onMetadataClick - Callback when metadata link is clicked (receives databaseId)
 * @param {boolean} options.showMapThumbnails - Whether to show the map thumbnail column
 * @param {React.ComponentType} options.MapThumbnailComponent - Component to render map thumbnails
 * @param {string} options.mapStyleUrl - Map style URL for thumbnails
 */
export const createDatabaseListDefinition = (options = {}) => {
    const { onMetadataClick, showMapThumbnails, MapThumbnailComponent, mapStyleUrl } = options;

    const columnDefinitions = [
        new ColumnDefinition({
            id: "databaseId",
            header: "Name",
            cellWrapper: (props) => {
                const { item } = props;
                return (
                    <Link href={`#/databases/${item.databaseId}/assets/`}>{props.children}</Link>
                );
            },
            sortingField: "databaseId",
        }),
        new ColumnDefinition({
            id: "description",
            header: "Description",
            cellWrapper: (props) => (
                <span style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                    {props.children}
                </span>
            ),
            sortingField: "description",
        }),
        new ColumnDefinition({
            id: "assetCount",
            header: `${Synonyms.Asset} Count`,
            cellWrapper: (props) => <>{props.children}</>,
            sortingField: "assetCount",
        }),
        new ColumnDefinition({
            id: "metadata",
            header: "Metadata",
            cellWrapper: (props) => {
                const { item } = props;
                return (
                    <Link
                        onFollow={(e) => {
                            e.preventDefault();
                            if (onMetadataClick) {
                                onMetadataClick(item.databaseId);
                            }
                        }}
                    >
                        Metadata
                    </Link>
                );
            },
            sortingField: undefined,
        }),
    ];

    // Add map thumbnail column when enabled
    if (showMapThumbnails && MapThumbnailComponent && mapStyleUrl) {
        columnDefinitions.splice(
            0,
            0,
            new ColumnDefinition({
                id: "mapThumbnail",
                header: "Map",
                cellWrapper: (props) => {
                    const { item } = props;
                    return (
                        <MapThumbnailComponent
                            databaseId={item.databaseId}
                            mapStyleUrl={mapStyleUrl}
                            width={200}
                            height={150}
                        />
                    );
                },
                sortingField: undefined,
            })
        );
    }

    columnDefinitions.push(
        new ColumnDefinition({
            id: "restrictMetadataOutsideSchemas",
            header: "Restrict Metadata Outside Schemas",
            cellWrapper: (props) => {
                const value = props.item?.restrictMetadataOutsideSchemas;
                return <>{value ? "True" : "False"}</>;
            },
            sortingField: "restrictMetadataOutsideSchemas",
        }),
        new ColumnDefinition({
            id: "restrictFileUploadsToExtensions",
            header: "Restrict File Upload Extensions",
            cellWrapper: (props) => {
                const value = props.item?.restrictFileUploadsToExtensions;
                return <>{value && value.trim() !== "" ? value : "No Restrictions"}</>;
            },
            sortingField: "restrictFileUploadsToExtensions",
        }),
        new ColumnDefinition({
            id: "bucketName",
            header: "Bucket Name",
            cellWrapper: (props) => <>{props.children}</>,
            sortingField: "bucketName",
        }),
        new ColumnDefinition({
            id: "baseAssetsPrefix",
            header: "Base Bucket Prefix",
            cellWrapper: (props) => <>{props.children}</>,
            sortingField: "baseAssetsPrefix",
        })
    );

    const visibleColumns = [
        ...(showMapThumbnails && MapThumbnailComponent && mapStyleUrl ? ["mapThumbnail"] : []),
        "databaseId",
        "description",
        "assetCount",
        "metadata",
        "restrictMetadataOutsideSchemas",
        "restrictFileUploadsToExtensions",
        "bucketName",
        "baseAssetsPrefix",
    ];

    return new ListDefinition({
        singularNameTitleCase: Synonyms.Database,
        pluralName: Synonyms.databases,
        pluralNameTitleCase: Synonyms.Databases,
        visibleColumns,
        filterColumns: [
            { name: "bucketName", placeholder: "Bucket Name" },
            { name: "restrictMetadataOutsideSchemas", placeholder: "Restrict Metadata" },
            { name: "restrictFileUploadsToExtensions", placeholder: "Restrict File Uploads" },
        ],
        elementId: "databaseId",
        deleteRoute: "database/{databaseId}",
        columnDefinitions,
    });
};

export const DatabaseListDefinition = createDatabaseListDefinition();

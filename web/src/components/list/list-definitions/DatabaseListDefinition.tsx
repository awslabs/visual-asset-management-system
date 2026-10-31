/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ListDefinition from "./types/ListDefinition";
import { Box, Link } from "@cloudscape-design/components";
import ColumnDefinition from "./types/ColumnDefinition";
import Synonyms from "../../../synonyms";

/**
 * Creates a DatabaseListDefinition with optional callbacks and map thumbnail support.
 * @param {Object} options
 * @param {Function} options.onMetadataClick - Callback when metadata link is clicked (receives databaseId)
 * @param {boolean} options.showMapThumbnails - Whether to show the map thumbnail column
 * @param {React.ComponentType} options.MapThumbnailComponent - Component to render map thumbnails
 * @param {string} options.mapStyleUrl - Map style URL for thumbnails
 * @param {boolean} options.showCompliance - Whether to show the compliance page link, beneath the
 *   database name
 * @param {boolean} options.showMetadata - Whether to show the metadata link column (the default)
 * @param {"assets" | "compliance" | "none"} options.nameLinkTarget - Page the database name links
 *   to: the database's assets (the default), its compliance page, or no link
 */
export const createDatabaseListDefinition = (options: any = {}) => {
    const {
        onMetadataClick,
        showMapThumbnails,
        MapThumbnailComponent,
        mapStyleUrl,
        showCompliance,
        showMetadata = true,
        nameLinkTarget = "assets",
    } = options;

    const columnDefinitions = [
        new ColumnDefinition({
            id: "databaseId",
            header: "Name",
            cellWrapper: (props: any) => {
                const { item } = props;
                let name;
                if (nameLinkTarget === "compliance") {
                    name = (
                        <Link href={`#/databases/${item.databaseId}/compliance`}>
                            {props.children}
                        </Link>
                    );
                } else if (nameLinkTarget === "none") {
                    name = <>{props.children}</>;
                } else {
                    name = (
                        <Link href={`#/databases/${item.databaseId}/assets/`}>
                            {props.children}
                        </Link>
                    );
                }
                if (!showCompliance) {
                    return name;
                }
                // The compliance page link sits beneath the name rather than in a column of its
                // own, so it adds no width to a list that already has many columns.
                return (
                    <>
                        {name}
                        <Box variant="small" display="block">
                            <Link
                                variant="secondary"
                                fontSize="body-s"
                                href={`#/databases/${item.databaseId}/compliance`}
                            >
                                Compliance
                            </Link>
                        </Box>
                    </>
                );
            },
            sortingField: "databaseId",
        }),
        new ColumnDefinition({
            id: "description",
            header: "Description",
            cellWrapper: (props: any) => (
                <span style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                    {props.children}
                </span>
            ),
            sortingField: "description",
        }),
        new ColumnDefinition({
            id: "assetCount",
            header: `Total ${Synonyms.Asset} Count`,
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "assetCount",
        }),
        new ColumnDefinition({
            id: "metadata",
            header: "Metadata",
            cellWrapper: (props: any) => {
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
                cellWrapper: (props: any) => {
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
            cellWrapper: (props: any) => {
                const value = props.item?.restrictMetadataOutsideSchemas;
                return <>{value ? "True" : "False"}</>;
            },
            sortingField: "restrictMetadataOutsideSchemas",
        }),
        new ColumnDefinition({
            id: "restrictFileUploadsToExtensions",
            header: "Restrict File Upload Extensions",
            cellWrapper: (props: any) => {
                const value = props.item?.restrictFileUploadsToExtensions;
                return <>{value && value.trim() !== "" ? value : "No Restrictions"}</>;
            },
            sortingField: "restrictFileUploadsToExtensions",
        }),
        new ColumnDefinition({
            id: "bucketName",
            header: "Bucket Name",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "bucketName",
        }),
        new ColumnDefinition({
            id: "baseAssetsPrefix",
            header: "Base Bucket Prefix",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "baseAssetsPrefix",
        })
    );

    const visibleColumns = [
        ...(showMapThumbnails && MapThumbnailComponent && mapStyleUrl ? ["mapThumbnail"] : []),
        "databaseId",
        "description",
        "assetCount",
        ...(showMetadata ? ["metadata"] : []),
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

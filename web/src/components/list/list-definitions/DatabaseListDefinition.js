/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ListDefinition from "./types/ListDefinition";
import { Link } from "@cloudscape-design/components";
import ColumnDefinition from "./types/ColumnDefinition";
import Synonyms from "../../../synonyms";

export const DatabaseListDefinition = new ListDefinition({
    singularNameTitleCase: Synonyms.Database,
    pluralName: Synonyms.databases,
    pluralNameTitleCase: Synonyms.Databases,
    visibleColumns: ["databaseId", "description", "bucketName", "baseAssetsPrefix", "assetCount"],
    filterColumns: [
        { name: "databaseId", placeholder: "Name" },
        { name: "bucketName", placeholder: "Bucket Name" },
        { name: "assetCount", placeholder: `${Synonyms.Asset} Count` },
    ],
    elementId: "databaseId",
    deleteRoute: "databases/{databaseId}",
    columnDefinitions: [
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
            cellWrapper: (props) => <>{props.children}</>,
            sortingField: "description",
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
        }),
        new ColumnDefinition({
            id: "assetCount",
            header: `${Synonyms.Asset} Count`,
            cellWrapper: (props) => <>{props.children}</>,
            sortingField: "assetCount",
        }),
    ],
});

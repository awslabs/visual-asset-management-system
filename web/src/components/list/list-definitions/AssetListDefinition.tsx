/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ListDefinition from "./types/ListDefinition";
import { Link } from "@cloudscape-design/components";
import ColumnDefinition from "./types/ColumnDefinition";
import Synonyms from "../../../synonyms";

export const AssetListDefinition = new ListDefinition({
    pluralName: Synonyms.assets,
    pluralNameTitleCase: Synonyms.Assets,
    visibleColumns: ["assetName", "databaseId", "description", "assetType", "tags"],
    filterColumns: [
        { name: "databaseId", placeholder: Synonyms.Database },
        { name: "assetType", placeholder: "Type" },
    ],
    elementId: "assetId",
    deleteRoute: "database/{databaseId}/assets/{assetId}",
    columnDefinitions: [
        new ColumnDefinition({
            id: "assetName",
            header: Synonyms.Asset,
            cellWrapper: (props: any) => {
                const { item } = props;
                return (
                    <Link href={`#/databases/${item.databaseId}/assets/${item.assetId}`}>
                        {props.children}
                    </Link>
                );
            },
            sortingField: "assetName",
        }),
        new ColumnDefinition({
            id: "databaseId",
            header: Synonyms.Database,
            cellWrapper: (props: any) => {
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
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "description",
        }),
        new ColumnDefinition({
            id: "assetType",
            header: "Type",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "assetType",
        }),
        new ColumnDefinition({
            id: "tags",
            header: "Tags",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "tags",
        }),
    ],
});

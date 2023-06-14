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
    pluralName: Synonyms.databases,
    pluralNameTitleCase: Synonyms.Databases,
    visibleColumns: ["databaseId", "description", "assetCount"],
    filterColumns: [
        { name: "databaseId", placeholder: "Name" },
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
                return <Link href={`/databases/${item.databaseId}/assets/`}>{props.children}</Link>;
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
            id: "assetCount",
            header: `${Synonyms.Asset} Count`,
            cellWrapper: (props) => <>{props.children}</>,
            sortingField: "assetCount",
        }),
    ],
});

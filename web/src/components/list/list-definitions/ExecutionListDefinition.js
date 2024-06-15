/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ListDefinition from "./types/ListDefinition";
import { Link } from "@cloudscape-design/components";
import ColumnDefinition from "./types/ColumnDefinition";

export const WorkflowListDefinition = new ListDefinition({
    pluralName: "workflows",
    pluralNameTitleCase: "Workflows",
    visibleColumns: ["workflowId", "databaseId", "description", "pipelines"],
    filterColumns: [{ name: "databaseId", placeholder: "Database" }],
    columnDefinitions: [
        new ColumnDefinition({
            id: "workflowId",
            header: "Name",
            cellWrapper: (props) => {
                const { item } = props;
                return (
                    <Link href={`#/databases/${item.databaseId}/workflows/${item.workflowId}`}>
                        {props.children}
                    </Link>
                );
            },
            sortingField: "assetId",
        }),
        new ColumnDefinition({
            id: "databaseId",
            header: "Database",
            cellWrapper: (props) => {
                const { item } = props;
                return (
                    <Link href={`#/databases/${item.databaseId}/workflows/`}>{props.children}</Link>
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
            id: "pipelines",
            header: "Pipelines",
            cellWrapper: (props) => {
                const displayPipelines = props?.item?.specifiedPipelines?.functions
                    .map((item) => item.name)
                    .join(" > ");
                return <>{displayPipelines}</>;
            },
        }),
    ],
});

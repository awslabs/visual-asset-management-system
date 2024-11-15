/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ListDefinition from "./types/ListDefinition";
import { Link } from "@cloudscape-design/components";
import ColumnDefinition from "./types/ColumnDefinition";
import Synonyms from "../../../synonyms";

export const PipelineListDefinition = new ListDefinition({
    pluralName: "pipelines",
    pluralNameTitleCase: "Pipelines",
    visibleColumns: [
        "pipelineId",
        "databaseId",
        "description",
        "pipelineType",
        "assetType",
        "outputType",
    ],
    filterColumns: [
        // { name: "databaseId", placeholder: "Database" },
        { name: "pipelineType", placeholder: "Type" },
        { name: "assetType", placeholder: "Input" },
        { name: "outputType", placeholder: "Output" },
    ],
    elementId: "pipelineId",
    deleteRoute: "database/{databaseId}/pipelines/{pipelineId}",
    columnDefinitions: [
        new ColumnDefinition({
            id: "pipelineId",
            header: "Name",
            cellWrapper: (props) => {
                return <>{props.children}</>;
            },
            sortingField: "pipelineId",
        }),
        new ColumnDefinition({
            id: "databaseId",
            header: Synonyms.Database,
            cellWrapper: (props) => {
                const { item } = props;
                return (
                    <Link href={`#/databases/${item.databaseId}/pipelines/`}>{props.children}</Link>
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
            id: "pipelineType",
            header: "Type",
            cellWrapper: (props) => <>{props.children}</>,
            sortingField: "pipelineType",
        }),
        new ColumnDefinition({
            id: "assetType",
            header: "Input",
            cellWrapper: (props) => <>{props.children}</>,
            sortingField: "assetType",
        }),
        new ColumnDefinition({
            id: "outputType",
            header: "Output",
            cellWrapper: (props) => <>{props.children}</>,
            sortingField: "outputType",
        }),
    ],
});

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ListDefinition from "./types/ListDefinition";
import { Badge, Link, StatusIndicator } from "@cloudscape-design/components";
import ColumnDefinition from "./types/ColumnDefinition";
import Synonyms from "../../../synonyms";

export const WorkflowExecutionListDefinition = new ListDefinition({
    pluralName: "workflow executions",
    pluralNameTitleCase: "Workflow Executions",
    visibleColumns: [
        "name",
        "databaseId",
        "description",
        "pipelines",
        "startDate",
        "stopDate",
        "executionStatus",
    ],
    filterColumns: [{ name: "databaseId", placeholder: Synonyms.Database }],
    elementId: "workflowId",
    //deleteRoute: "database/{databaseId}/workflows/{workflowId}",
    createAction: false,
    columnDefinitions: [
        new ColumnDefinition({
            id: "name",
            header: "Name",
            cellWrapper: (props) => {
                const { item } = props;
                if (!item.name) {
                    return <></>;
                }
                // If this is a workflow (has no parentId), make it a link
                if (!item.parentId) {
                    // Use displayName if available, otherwise fall back to name
                    const displayText = item.displayName || item.name;
                    return (
                        <Link
                            href={`#/databases/${item?.databaseId}/workflows/${item?.workflowId}`}
                        >
                            {item.displayName ? displayText : props.children}
                        </Link>
                    );
                }
                // If this is an execution (has parentId), don't make it a link
                return <>{props.children}</>;
            },
            sortingField: "assetName",
        }),
        new ColumnDefinition({
            id: "databaseId",
            header: Synonyms.Database,
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
            cellWrapper: (props) => {
                const { item } = props;
                if (!item.description) {
                    if (!item.workflowId) {
                        if (
                            !item.Items ||
                            !Array.isArray(item.Items) ||
                            item.Items.length === 0 ||
                            !Array.isArray(item.Items[0]) ||
                            item.Items[0].length === 0
                        ) {
                            return <>{item.name}</>;
                        }
                        const list = item.Items.slice(0);
                        console.log(1, list);
                        list.reverse();
                        console.log(2, list);
                        return (
                            <ol>
                                {list.map((listItemArray, i) => {
                                    const listItem = Array.isArray(listItemArray)
                                        ? listItemArray[0]
                                        : {};
                                    return (
                                        <li key={i}>
                                            <Link
                                                href={`#/databases/${listItem?.databaseId}/assets/${listItem?.assetId}`}
                                            >
                                                {listItem?.assetName}
                                            </Link>
                                        </li>
                                    );
                                })}
                            </ol>
                        );
                    }
                    return <></>;
                }
                return <>{item?.description}</>;
            },
            sortingField: "description",
        }),
        new ColumnDefinition({
            id: "pipelines",
            header: "Pipelines",
            cellWrapper: (props) => {
                const displayPipelines = props?.item?.specifiedPipelines?.functions.map((item) => (
                    <Badge key={item.name} color="grey">
                        {item.name}
                    </Badge>
                ));
                return <>{displayPipelines}</>;
            },
        }),
        new ColumnDefinition({
            id: "startDate",
            header: "Started",
            cellWrapper: (props) => <>{props?.item?.startDate}</>,
            sortingField: "startDate",
        }),
        new ColumnDefinition({
            id: "stopDate",
            header: "Stopped",
            cellWrapper: (props) => <>{props?.item?.stopDate}</>,
            sortingField: "stopDate",
        }),
        new ColumnDefinition({
            id: "executionStatus",
            header: "Status",
            cellWrapper: (props) => {
                if (!props?.item?.executionStatus || props?.item?.executionStatus === "") {
                    return <></>;
                } else {
                    return (
                        <StatusIndicator
                            type={
                                props?.item?.executionStatus === "SUCCEEDED"
                                    ? "success"
                                    : props?.item?.executionStatus === "RUNNING"
                                    ? "pending"
                                    : "error"
                            }
                        >
                            {props?.item?.executionStatus}
                        </StatusIndicator>
                    );
                }
            },
        }),
    ],
});

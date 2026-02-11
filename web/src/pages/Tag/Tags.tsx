/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import CreateTag from "./CreateTag";
import ListDefinition from "../../components/list/list-definitions/types/ListDefinition";
import ColumnDefinition from "../../components/list/list-definitions/types/ColumnDefinition";
import { API } from "aws-amplify";
import ListPageNoDatabase from "../ListPageNoDatabase";
import CreateTagType from "./CreateTagType";
import { fetchTags, fetchtagTypes } from "../../services/APIService";
import { useEffect, useState } from "react";
import { Box } from "@cloudscape-design/components";
var rel;

export const TagsListDefinition = new ListDefinition({
    pluralName: "tags",
    pluralNameTitleCase: "Tags",
    singularNameTitleCase: "Tag",
    visibleColumns: ["tagName", "description", "tagTypeName"],
    filterColumns: [{ name: "tagName", placeholder: "Name" }],
    elementId: "tagName",
    deleteFunction: async (item: any): Promise<[boolean, string, string]> => {
        try {
            const response: any = await API.del("api", `tags/${item.tagName}`, {});
            return [true, response.message, ""];
        } catch (error: any) {
            console.log(error);
            return [false, error?.message, error?.response.data.message];
        }
    },
    columnDefinitions: [
        new ColumnDefinition({
            id: "tagName",
            header: "Name",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "tagName",
        }),
        new ColumnDefinition({
            id: "description",
            header: "Description",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "description",
        }),
        new ColumnDefinition({
            id: "tagTypeName",
            header: "Tag Type",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "tagTypeName",
        }),
    ],
});

export const TagTypesListDefinition = new ListDefinition({
    pluralName: "tag types",
    pluralNameTitleCase: "Tag Types",
    singularNameTitleCase: "Tag Type",
    visibleColumns: ["tagTypeName", "description", "required", "tags"],
    filterColumns: [{ name: "name", placeholder: "Name" }],
    elementId: "tagTypeName",
    deleteFunction: async (item: any): Promise<[boolean, string, string]> => {
        try {
            const response: any = await API.del("api", `tag-types/${item.tagTypeName}`, {});
            return [true, response.message, ""];
        } catch (error: any) {
            console.log(error);
            return [false, error?.message, error?.response.data.message];
        }
    },
    columnDefinitions: [
        new ColumnDefinition({
            id: "tagTypeName",
            header: "Tag Type",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "tagTypeName",
        }),
        new ColumnDefinition({
            id: "description",
            header: "Description",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "description",
        }),
        new ColumnDefinition({
            id: "required",
            header: "Required on Asset",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "required",
        }),
        new ColumnDefinition({
            id: "tags",
            header: "Tags",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "tags",
        }),
    ],
});

export default function Tags() {
    const [reloadKey1, setReloadKey1] = useState(0);
    const [reloadKey2, setReloadKey2] = useState(100);
    const reloadChild1 = () => {
        setReloadKey2(reloadKey2 - 1);
    };

    const reloadChild2 = () => {
        setReloadKey1(reloadKey1 + 1);
    };
    return (
        <>
            <ListPageNoDatabase
                singularName={"tag"}
                singularNameTitleCase={"Tag"}
                pluralName={"tags"}
                pluralNameTitleCase={"Manage Tags"}
                listDefinition={TagsListDefinition}
                CreateNewElement={CreateTag}
                fetchElements={fetchTags}
                fetchAllElements={fetchTags}
                editEnabled={true}
                key={reloadKey1}
                onReload={reloadChild1}
            />
            <ListPageNoDatabase
                singularName={"tag type"}
                singularNameTitleCase={"Tag Type"}
                pluralName={"tag type"}
                pluralNameTitleCase={""}
                listDefinition={TagTypesListDefinition}
                CreateNewElement={CreateTagType}
                fetchElements={fetchtagTypes}
                fetchAllElements={fetchtagTypes}
                editEnabled={true}
                key={reloadKey2}
                onReload={reloadChild2}
            />
        </>
    );
}

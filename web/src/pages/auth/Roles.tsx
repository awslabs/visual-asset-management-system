/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import ListDefinition from "../../components/list/list-definitions/types/ListDefinition";
import ColumnDefinition from "../../components/list/list-definitions/types/ColumnDefinition";
import { API } from "aws-amplify";
import ListPageNoDatabase from "../ListPageNoDatabase";
import { fetchRoles } from "../../services/APIService";
import { Box } from "@cloudscape-design/components";
import CreateRole from "./CreateRoles";
import { useState } from "react";

export const RoleListDefinition = new ListDefinition({
    pluralName: "Roles",
    pluralNameTitleCase: "Roles",
    singularNameTitleCase: "Role",
    visibleColumns: [
        "roleName",
        "source",
        "sourceIdentifier",
        "createdOn",
        "description",
        "mfaRequired",
    ],
    filterColumns: [{ name: "name", placeholder: "Name" }],
    elementId: "roleName",
    deleteFunction: async (item: any): Promise<[boolean, string, string]> => {
        try {
            const response: any = await API.del("api", `roles/${item.roleName}`, {});
            return [true, response.message, ""];
        } catch (error: any) {
            console.log(error);
            return [false, error?.message, error?.response.data.message];
        }
    },
    columnDefinitions: [
        new ColumnDefinition({
            id: "roleName",
            header: "Name",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "roleName",
        }),
        new ColumnDefinition({
            id: "source",
            header: "Source",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "source",
        }),
        new ColumnDefinition({
            id: "sourceIdentifier",
            header: "Source Identifier",
            cellWrapper: (props: any) => {
                const content =
                    props.children === "undefined" || props.children === "null"
                        ? ""
                        : props.children;
                return (
                    <>
                        <Box textAlign="center">{content}</Box>
                    </>
                );
            },
            sortingField: "sourceIdentifier",
        }),
        new ColumnDefinition({
            id: "createdOn",
            header: "Created On",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "createdOn",
        }),
        new ColumnDefinition({
            id: "description",
            header: "Description",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "description",
        }),
        new ColumnDefinition({
            id: "mfaRequired",
            header: "MFA Required",
            cellWrapper: (props: any) => {
                const content =
                    props.children === "undefined" || props.children === "null"
                        ? "false"
                        : props.children;
                return (
                    <>
                        <Box textAlign="center">{content}</Box>
                    </>
                );
            },
            sortingField: "mfaRequired",
        }),
    ],
});

export default function Roles() {
    const [reloadKey, setReloadKey] = useState(0);

    const reloadChild = () => {
        setReloadKey(reloadKey - 1);
    };
    return (
        <>
            <ListPageNoDatabase
                singularName={"Role"}
                singularNameTitleCase={"Role"}
                pluralName={"Roles"}
                pluralNameTitleCase={"Roles"}
                listDefinition={RoleListDefinition}
                CreateNewElement={CreateRole}
                editEnabled={true}
                fetchElements={fetchRoles}
                fetchAllElements={fetchRoles}
                onReload={reloadChild}
            />
        </>
    );
}

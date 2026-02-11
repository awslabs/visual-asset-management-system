/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import ListDefinition from "../../components/list/list-definitions/types/ListDefinition";
import ColumnDefinition from "../../components/list/list-definitions/types/ColumnDefinition";
import { API } from "aws-amplify";
import ListPageNoDatabase from "../ListPageNoDatabase";
import { fetchUserRoles } from "../../services/APIService";
import CreateUserRole from "./CreateUserRole";
import { useState } from "react";

const userRoleBody = {
    userId: "",
};

export const UserRolesListDefinition = new ListDefinition({
    pluralName: "Users in Roles",
    pluralNameTitleCase: "Users in Roles",
    singularNameTitleCase: "Users in Role",
    visibleColumns: ["userId", "roleName"],
    filterColumns: [{ name: "name", placeholder: "Name" }],
    elementId: "userId",
    deleteFunction: async (item: any): Promise<[boolean, string, string]> => {
        userRoleBody.userId = item.userId;
        try {
            const response: any = await API.del("api", "user-roles", {
                body: userRoleBody,
            });
            return [true, response.message, ""];
        } catch (error: any) {
            console.log(error);
            return [false, error?.message, error?.response.data.message];
        }
    },
    columnDefinitions: [
        new ColumnDefinition({
            id: "userId",
            header: "User ID",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "userId",
        }),
        new ColumnDefinition({
            id: "roleName",
            header: "Roles",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "roleName",
        }),
    ],
});

export default function UserRoles() {
    const [reloadKey, setReloadKey] = useState(0);

    const reloadChild = () => {
        setReloadKey(reloadKey - 1);
    };
    return (
        <>
            <ListPageNoDatabase
                singularName={"Users in Role"}
                singularNameTitleCase={"Users in Role"}
                pluralName={"Users in Roles"}
                pluralNameTitleCase={"Users in Roles"}
                listDefinition={UserRolesListDefinition}
                CreateNewElement={CreateUserRole}
                editEnabled={true}
                fetchElements={fetchUserRoles}
                fetchAllElements={fetchUserRoles}
                onReload={reloadChild}
            />
        </>
    );
}

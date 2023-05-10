/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// import { fetchConstraints } from "../../services/APIService";
import CreateConstraint from "./CreateConstraint";
import ListDefinition from "../../components/list/list-definitions/types/ListDefinition";
import ColumnDefinition from "../../components/list/list-definitions/types/ColumnDefinition";
import { API } from "aws-amplify";
import ListPageNoDatabase from "../ListPageNoDatabase";

async function fetchConstraints(api = API) {
    try {
        const response = await api.get("api", "auth/constraints", {});
        if (response.constraints) {
            return response.constraints;
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
}

export const ConstraintsListDefinition = new ListDefinition({
    pluralName: "constraints",
    pluralNameTitleCase: "Constraints",
    visibleColumns: ["name", "description"],
    filterColumns: [{ name: "name", placeholder: "Name" }],
    elementId: "name",
    deleteFunction: (item: any): [boolean, string] => {
        try {
            const response: any = API.del("api", `auth/constraints/${item.constraintId}`, {});
            return [true, response.message];
        } catch (error: any) {
            console.log(error);
            return [false, error?.message];
        }
    },
    columnDefinitions: [
        new ColumnDefinition({
            id: "name",
            header: "Name",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "name",
        }),
        new ColumnDefinition({
            id: "description",
            header: "Description",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "description",
        }),
    ],
});

export default function Constraints() {
    return (
        <ListPageNoDatabase
            singularName={"constraint"}
            singularNameTitleCase={"Constraint"}
            pluralName={"constraints"}
            pluralNameTitleCase={"Constraints"}
            listDefinition={ConstraintsListDefinition}
            CreateNewElement={CreateConstraint}
            fetchElements={fetchConstraints}
            fetchAllElements={fetchConstraints}
            editEnabled={true}
        />
    );
}

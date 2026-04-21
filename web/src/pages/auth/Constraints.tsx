/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import CreateConstraint from "./CreateConstraint";
import ListDefinition from "../../components/list/list-definitions/types/ListDefinition";
import ColumnDefinition from "../../components/list/list-definitions/types/ColumnDefinition";
import ListPageNoDatabase from "../ListPageNoDatabase";
import { fetchConstraints, deleteConstraint } from "../../services/APIService";

export const ConstraintsListDefinition = new ListDefinition({
    pluralName: "constraints",
    pluralNameTitleCase: "Constraints",
    visibleColumns: ["name", "description"],
    filterColumns: [{ name: "name", placeholder: "Name" }],
    elementId: "name",
    deleteFunction: async (item: any): Promise<[boolean, string, string]> => {
        try {
            const result: any = await deleteConstraint({ constraintId: item.constraintId });
            return [result[0], result[1] || "", ""];
        } catch (error: any) {
            console.log(error);
            return [false, error?.message, error?.response?.data?.message];
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
            cellWrapper: (props: any) => (
                <span style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                    {props.children}
                </span>
            ),
            sortingField: "description",
        }),
    ],
});

export default function Constraints() {
    return (
        <ListPageNoDatabase
            singularName={"access control constraint"}
            singularNameTitleCase={"Access Control Constraint"}
            pluralName={"access control constraints"}
            pluralNameTitleCase={"Access Control Constraints"}
            listDefinition={ConstraintsListDefinition}
            CreateNewElement={CreateConstraint}
            fetchElements={fetchConstraints}
            fetchAllElements={fetchConstraints}
            editEnabled={true}
        />
    );
}

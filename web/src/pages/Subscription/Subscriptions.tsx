/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import ListDefinition from "../../components/list/list-definitions/types/ListDefinition";
import ColumnDefinition from "../../components/list/list-definitions/types/ColumnDefinition";
import { API } from "aws-amplify";
import ListPageNoDatabase from "../ListPageNoDatabase";
import { fetchSubscriptionRules } from "../../services/APIService";
import { Link } from "@cloudscape-design/components";
import CreateSubscription from "./CreateSubscription";
import { useState } from "react";

const ruleBody = {
    eventName: "Subscription Change",
    entityName: "",
    entityId: "",
    subscribers: [],
};
export const SubscriptionListDefinition = new ListDefinition({
    pluralName: "Subscriptions",
    pluralNameTitleCase: "Subscriptions",
    singularNameTitleCase: "Subscription",
    visibleColumns: ["eventName", "entityName", "entityValue", "subscribers"],
    filterColumns: [{ name: "name", placeholder: "Name" }],
    elementId: "entityId",
    deleteFunction: async (item: any): Promise<[boolean, string, string]> => {
        console.log(item);
        ruleBody.entityName = item.entityName;
        ruleBody.entityId = item.entityId;
        ruleBody.subscribers = item.subscribers;
        ruleBody.eventName = item.eventName;
        try {
            const response: any = await API.del("api", "subscriptions", {
                body: ruleBody,
            });
            return [true, response.message, ""];
        } catch (error: any) {
            console.log(error);
            return [false, error?.message, error?.response.data.message];
        }
    },
    columnDefinitions: [
        new ColumnDefinition({
            id: "entityValue",
            header: "Entity Name",
            cellWrapper: (props: any) => {
                return (
                    <>
                        <Link
                            href={`#/databases/${props.item.databaseId}/assets/${props.item.entityId}`}
                        >
                            {props.children}
                        </Link>
                    </>
                );
            },
            sortingField: "entityValue",
        }),

        new ColumnDefinition({
            id: "entityName",
            header: "Entity Type",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "entityName",
        }),
        new ColumnDefinition({
            id: "eventName",
            header: "Event Name",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "eventName",
        }),
        new ColumnDefinition({
            id: "subscribers",
            header: "Subscribers",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "subscribers",
        }),
    ],
});

export default function Subscriptions() {
    const [reloadKey, setReloadKey] = useState(0);

    const reloadChild = () => {
        setReloadKey(reloadKey - 1);
    };
    return (
        <>
            <ListPageNoDatabase
                singularName={"Subscription"}
                singularNameTitleCase={"Subscription"}
                pluralName={"Subscriptions"}
                pluralNameTitleCase={"Subscriptions"}
                listDefinition={SubscriptionListDefinition}
                CreateNewElement={CreateSubscription}
                fetchElements={fetchSubscriptionRules}
                fetchAllElements={fetchSubscriptionRules}
                editEnabled={true}
                onReload={reloadChild}
            />
        </>
    );
}

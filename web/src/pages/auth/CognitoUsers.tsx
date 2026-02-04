/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import ListDefinition from "../../components/list/list-definitions/types/ListDefinition";
import ColumnDefinition from "../../components/list/list-definitions/types/ColumnDefinition";
import { fetchCognitoUsers, deleteCognitoUser } from "../../services/APIService";
import CreateCognitoUser from "./CreateCognitoUser";
import ResetCognitoUserPassword from "./ResetCognitoUserPassword";
import { useState, useEffect } from "react";
import { Box, Button, Grid, SpaceBetween, TextContent } from "@cloudscape-design/components";
import TableList from "../../components/list/TableList";

export const CognitoUsersListDefinition = new ListDefinition({
    pluralName: "Cognito User Management",
    pluralNameTitleCase: "Cognito User Management",
    singularNameTitleCase: "Cognito User",
    visibleColumns: [
        "userId",
        "email",
        "phone",
        "userStatus",
        "mfaEnabled",
        "userCreateDate",
        "userLastModifiedDate",
    ],
    filterColumns: [
        { name: "userId", placeholder: "User ID" },
        { name: "email", placeholder: "Email" },
        { name: "phone", placeholder: "Phone Number" },
    ],
    elementId: "userId",
    deleteFunction: async (item: any): Promise<[boolean, string, string]> => {
        try {
            const response = await deleteCognitoUser({ userId: item.userId });
            if (response && response[0]) {
                return [true, response[1], ""];
            } else {
                return [false, response ? response[1] : "Unknown error", ""];
            }
        } catch (error: any) {
            console.log(error);
            return [false, error?.message, error?.response?.data?.message || ""];
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
            id: "email",
            header: "Email",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "email",
        }),
        new ColumnDefinition({
            id: "phone",
            header: "Phone Number",
            cellWrapper: (props: any) => {
                // Handle null, undefined, or empty string
                const value = props.children;
                if (value === null || value === undefined || value === "") {
                    return <></>;
                }
                return <>{value}</>;
            },
            sortingField: "phone",
        }),
        new ColumnDefinition({
            id: "userStatus",
            header: "Status",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "userStatus",
        }),
        new ColumnDefinition({
            id: "mfaEnabled",
            header: "MFA Enabled",
            cellWrapper: (props: any) => {
                return <>{props.children === true ? "Yes" : "No"}</>;
            },
            sortingField: "mfaEnabled",
        }),
        new ColumnDefinition({
            id: "userCreateDate",
            header: "Created At",
            cellWrapper: (props: any) => {
                const date = props.children ? new Date(props.children) : null;
                return <>{date ? date.toLocaleString() : "-"}</>;
            },
            sortingField: "userCreateDate",
        }),
        new ColumnDefinition({
            id: "userLastModifiedDate",
            header: "Last Modified",
            cellWrapper: (props: any) => {
                const date = props.children ? new Date(props.children) : null;
                return <>{date ? date.toLocaleString() : "-"}</>;
            },
            sortingField: "userLastModifiedDate",
        }),
    ],
});

export default function CognitoUsers() {
    const [reload, setReload] = useState(true);
    const [loading, setLoading] = useState(true);
    const [allItems, setAllItems] = useState<Array<any>>([]);
    const [openNewElement, setOpenNewElement] = useState(false);
    const [editOpen, setEditOpen] = useState(false);
    const [resetPasswordOpen, setResetPasswordOpen] = useState(false);
    const [selectedItems, setSelectedItems] = useState<any[]>([]);

    useEffect(() => {
        const getData = async () => {
            setLoading(true);
            let items = await fetchCognitoUsers();

            if (items !== false && Array.isArray(items)) {
                setLoading(false);
                setReload(false);
                setAllItems(items);
            }
        };
        if (reload) {
            getData();
        }
    }, [reload]);

    const reloadData = () => {
        setReload(true);
    };

    return (
        <>
            <Box padding={{ top: "m", horizontal: "l" }}>
                <Grid gridDefinition={[{ colspan: 6 }]}>
                    <div>
                        <TextContent>
                            <h1>Cognito User Management</h1>
                        </TextContent>
                    </div>
                </Grid>
                <Grid gridDefinition={[{ colspan: 12 }]}>
                    <TableList
                        allItems={allItems}
                        loading={loading}
                        listDefinition={CognitoUsersListDefinition}
                        editEnabled={true}
                        setReload={setReload}
                        onReload={reloadData}
                        UpdateSelectedElement={CreateCognitoUser}
                        customHeaderActions={
                            <Button
                                disabled={selectedItems.length !== 1}
                                onClick={() => setResetPasswordOpen(true)}
                            >
                                Reset Password
                            </Button>
                        }
                        onSelectionChange={(items: any[]) => setSelectedItems(items)}
                        createNewElement={
                            <div style={{ float: "right" }}>
                                <SpaceBetween direction={"horizontal"} size={"m"}>
                                    <Button
                                        onClick={() => setOpenNewElement(true)}
                                        variant="primary"
                                        data-testid="create-new-element-button"
                                    >
                                        Create Cognito User
                                    </Button>
                                </SpaceBetween>
                            </div>
                        }
                    />
                </Grid>
            </Box>
            <CreateCognitoUser
                open={openNewElement}
                setOpen={setOpenNewElement}
                setReload={setReload}
                initState={null}
            />
            {selectedItems.length === 1 && (
                <ResetCognitoUserPassword
                    open={resetPasswordOpen}
                    setOpen={setResetPasswordOpen}
                    setReload={reloadData}
                    user={selectedItems[0]}
                />
            )}
        </>
    );
}

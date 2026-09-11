/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Grid from "@cloudscape-design/components/grid";
import SpaceBetween from "@cloudscape-design/components/space-between";
import TextContent from "@cloudscape-design/components/text-content";
import TableList from "../components/list/TableList";
import PropTypes from "prop-types";
import ListDefinition from "../components/list/list-definitions/types/ListDefinition";
import { usePageTitle } from "../hooks/usePageTitle";

export default function ListPageNoDatabase(props: any) {
    const {
        singularNameTitleCase,
        pluralNameTitleCase,
        listDefinition,
        CreateNewElement,
        fetchElements,
        fetchAllElements,
        onCreateCallback,
        editEnabled,
        onReload,
    } = props;
    usePageTitle(pluralNameTitleCase);
    const [reload, setReload] = useState(true);
    const [loading, setLoading] = useState(true);
    const [allItems, setAllItems] = useState<Array<any>>([]);
    const [error, setError] = useState<string | null>(null);

    const [openNewElement, setOpenNewElement] = useState(false);

    useEffect(() => {
        const getData = async () => {
            setLoading(true);
            setError(null);
            try {
                const items = await fetchAllElements();

                if (items !== false && Array.isArray(items)) {
                    setAllItems(items);
                } else if (typeof items === "string" && items.trim() !== "") {
                    // The service layer returns the API error message string on failure.
                    setError(items);
                } else {
                    setError("Failed to load data. Please try refreshing.");
                }
            } catch (err: any) {
                console.error("Error loading data:", err);
                setError(
                    err?.message || "An error occurred while loading data. Please try refreshing."
                );
            } finally {
                setLoading(false);
                setReload(false);
            }
        };
        if (reload) {
            getData();
        }
    }, [reload, fetchAllElements, fetchElements]);

    const handleOpenNewElement = () => {
        if (onCreateCallback) onCreateCallback();
        else if (CreateNewElement) setOpenNewElement(true);
    };

    const handleRefresh = () => {
        setReload(true);
    };

    return (
        <>
            <Box padding={{ top: "m", horizontal: "l" }}>
                <Grid gridDefinition={[{ colspan: 6 }]}>
                    <div>
                        <TextContent>
                            <h1>{pluralNameTitleCase}</h1>
                        </TextContent>
                    </div>
                </Grid>
                {error && (
                    <Grid gridDefinition={[{ colspan: 12 }]}>
                        <Alert
                            type="error"
                            dismissible
                            onDismiss={() => setError(null)}
                            action={
                                <Button onClick={handleRefresh} iconName="refresh">
                                    Retry
                                </Button>
                            }
                        >
                            {error}
                        </Alert>
                    </Grid>
                )}
                <Grid gridDefinition={[{ colspan: 12 }]}>
                    <TableList
                        allItems={allItems}
                        loading={loading}
                        listDefinition={listDefinition}
                        editEnabled={editEnabled}
                        setReload={setReload}
                        onReload={onReload}
                        UpdateSelectedElement={CreateNewElement}
                        createNewElement={
                            (CreateNewElement || onCreateCallback) && (
                                <div style={{ float: "right" }}>
                                    <SpaceBetween direction={"horizontal"} size={"m"}>
                                        <Button
                                            onClick={handleOpenNewElement}
                                            variant="primary"
                                            data-testid="create-new-element-button"
                                        >
                                            Create {singularNameTitleCase}
                                        </Button>
                                    </SpaceBetween>
                                </div>
                            )
                        }
                    />
                </Grid>
            </Box>
            {CreateNewElement && (
                <CreateNewElement
                    open={openNewElement}
                    setOpen={setOpenNewElement}
                    setReload={setReload}
                    reloadChild={onReload}
                />
            )}
        </>
    );
}

ListPageNoDatabase.propTypes = {
    singularName: PropTypes.string.isRequired,
    singularNameTitleCase: PropTypes.string.isRequired,
    pluralName: PropTypes.string.isRequired,
    pluralNameTitleCase: PropTypes.string.isRequired,
    listDefinition: PropTypes.instanceOf(ListDefinition).isRequired,
    CreateNewElement: PropTypes.func,
    fetchElements: PropTypes.func.isRequired,
    fetchAllElements: PropTypes.func,
    onCreateCallback: PropTypes.func,
    editEnabled: PropTypes.bool,
    onReload: PropTypes.func,
};

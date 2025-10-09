/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";

import {
    Box,
    BreadcrumbGroup,
    Button,
    Grid,
    SpaceBetween,
    TextContent,
    Alert,
    Icon,
} from "@cloudscape-design/components";
import { useParams } from "react-router";
import TableList from "../components/list/TableList";
import PropTypes from "prop-types";
import ListDefinition from "../components/list/list-definitions/types/ListDefinition";
import RelatedTableList from "../components/list/RelatedTableList";
import Synonyms from "../synonyms";

export default function ListPage(props) {
    const { databaseId } = useParams();
    const {
        singularNameTitleCase,
        pluralName,
        pluralNameTitleCase,
        listDefinition,
        CreateNewElement,
        fetchElements,
        fetchAllElements,
        onCreateCallback,
        isRelatedTable,
        editEnabled,
        hideDeleteButton = false,
    } = props;
    const [reload, setReload] = useState(true);
    const [loading, setLoading] = useState(true);
    const [allItems, setAllItems] = useState([]);
    const [error, setError] = useState(null);

    const [openNewElement, setOpenNewElement] = useState(false);

    useEffect(() => {
        const getData = async () => {
            setLoading(true);
            setError(null);
            try {
                let items;
                if (databaseId !== undefined) {
                    // This handles both specific database IDs and Global
                    items = await fetchElements({ databaseId: databaseId });
                } else {
                    // This is for the main pipelines page showing all pipelines
                    items = await fetchAllElements();
                }

                if (items !== false && Array.isArray(items)) {
                    setAllItems(
                        //@todo fix workflow delete return
                        items.filter((item) => item.databaseId.indexOf("#deleted") === -1)
                    );
                } else {
                    setError("Failed to load data. Please try refreshing.");
                }
            } catch (err) {
                console.error("Error loading data:", err);
                setError(err.message || "An error occurred while loading data. Please try refreshing.");
            } finally {
                setLoading(false);
                setReload(false);
            }
        };
        if (reload) {
            getData();
        }
    }, [reload, databaseId, fetchAllElements, fetchElements]);

    const handleOpenNewElement = () => {
        if (onCreateCallback) onCreateCallback();
        else if (CreateNewElement) setOpenNewElement(true);
    };

    const handleRefresh = () => {
        setReload(true);
    };

    return (
        <>
            <Box padding={{ top: databaseId ? "s" : "m", horizontal: "l" }}>
                {databaseId && (
                    <BreadcrumbGroup
                        items={[
                            { text: Synonyms.Databases, href: "#/databases/" },
                            {
                                text: databaseId,
                                href: `#/databases/${databaseId}/${pluralName}/`,
                            },
                            { text: pluralNameTitleCase },
                        ]}
                        ariaLabel="Breadcrumbs"
                    />
                )}
                <Grid gridDefinition={[{ colspan: { default: "12" } }]}>
                    <div>
                        <TextContent>
                            <h1>
                                {pluralNameTitleCase}
                                {databaseId && ` for ${databaseId}`}
                            </h1>
                        </TextContent>
                    </div>
                </Grid>
                <Grid gridDefinition={[{ colspan: { default: "12" } }]}>
                    {error && (
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
                    )}
                    {isRelatedTable && (
                        <RelatedTableList
                            allItems={allItems}
                            loading={loading}
                            listDefinition={listDefinition}
                            databaseId={databaseId}
                            setReload={setReload}
                        />
                    )}
                    <TableList
                        allItems={allItems}
                        loading={loading}
                        listDefinition={listDefinition}
                        databaseId={databaseId}
                        editEnabled={editEnabled}
                        setReload={setReload}
                        UpdateSelectedElement={CreateNewElement}
                        hideDeleteButton={hideDeleteButton}
                        createNewElement={
                            (CreateNewElement || onCreateCallback) && (
                                <div style={{ float: "right" }}>
                                    <SpaceBetween direction={"horizontal"} size={"m"}>
                                        <Button onClick={handleOpenNewElement} variant="primary">
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
                    databaseId={databaseId}
                />
            )}
        </>
    );
}

ListPage.propTypes = {
    singularName: PropTypes.string.isRequired,
    singularNameTitleCase: PropTypes.string.isRequired,
    pluralName: PropTypes.string.isRequired,
    pluralNameTitleCase: PropTypes.string.isRequired,
    listDefinition: PropTypes.instanceOf(ListDefinition).isRequired,
    CreateNewElement: PropTypes.func,
    fetchElements: PropTypes.func.isRequired,
    fetchAllElements: PropTypes.func,
    onCreateCallback: PropTypes.func,
    isRelatedTable: PropTypes.bool,
    editEnabled: PropTypes.bool,
    hideDeleteButton: PropTypes.bool,
};

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
import Synonyms from "../synonyms";
import { usePageTitleWhen } from "../hooks/usePageTitle";

/**
 * A list of one database's elements, or of every database's when the route names none. The routes
 * that render it (`/assets`, `/search/:databaseId/assets`, `/databases/:databaseId/assets`) render
 * the same page component, so in-app navigation between them keeps this instance mounted; the body
 * is keyed by the route's database so a change of database starts it afresh. It then loads that
 * database's list and drops the previous one's rows, selection, filter text and page, and an answer
 * still on its way for the previous database is never shown.
 */
export default function ListPage(props: any) {
    const { databaseId } = useParams();
    return <ListPageBody key={databaseId ?? ""} {...props} />;
}

function ListPageBody(props: any) {
    const { databaseId } = useParams();
    const {
        singularNameTitleCase,
        pluralNameTitleCase,
        listDefinition,
        CreateNewElement,
        fetchElements,
        fetchAllElements,
        onCreateCallback,
        editEnabled,
        hideDeleteButton = false,
        customFilterControls,
        customHeaderActions,
        onSelectionChange,
        // Extra arguments for fetchElements / fetchAllElements (e.g. showArchived); a change refetches.
        fetchOptions,
        // Whether rows from an archived partition (databaseId ending in #deleted) are kept.
        includeArchived = false,
        // Receives the reload trigger so a caller's own modals can refresh the list.
        onReloadReady,
        createDisabledReason,
        // For a host page that renders the breadcrumbs and the h1 itself (the search page's asset
        // list view): the list then starts with its table, whose own header names it, and the
        // host page keeps the document title.
        hidePageHeading = false,
    } = props;
    usePageTitleWhen(!hidePageHeading, databaseId || null, pluralNameTitleCase);
    const [reload, setReload] = useState(true);
    const [loading, setLoading] = useState(true);
    const [allItems, setAllItems] = useState<any[]>([]);
    const [error, setError] = useState<string | null>(null);

    const [openNewElement, setOpenNewElement] = useState(false);

    useEffect(() => {
        const getData = async () => {
            setLoading(true);
            setError(null);
            try {
                let items;
                if (databaseId !== undefined) {
                    // This handles both specific database IDs and Global
                    items = await fetchElements({
                        databaseId: databaseId,
                        ...(fetchOptions || {}),
                    });
                } else {
                    // This is for the main pipelines page showing all pipelines
                    items = await fetchAllElements(fetchOptions || {});
                }

                if (items !== false && Array.isArray(items)) {
                    setAllItems(
                        //@todo fix workflow delete return
                        includeArchived
                            ? items
                            : items.filter((item) => item.databaseId.indexOf("#deleted") === -1)
                    );
                } else if (typeof items === "string" && items.trim() !== "") {
                    // The service layer returns the API error message string on failure.
                    setError(items);
                } else {
                    setError("Failed to load data. Please try refreshing.");
                }
            } catch (err: any) {
                console.error("Error loading data:", err);
                setError(
                    err.message || "An error occurred while loading data. Please try refreshing."
                );
            } finally {
                setLoading(false);
                setReload(false);
            }
        };
        if (reload) {
            getData();
        }
    }, [reload, databaseId, fetchAllElements, fetchElements, fetchOptions, includeArchived]);

    // A fetch-option change (e.g. Show archived) refetches.
    useEffect(() => {
        setReload(true);
    }, [fetchOptions, includeArchived]);

    useEffect(() => {
        if (onReloadReady) onReloadReady(() => setReload(true));
    }, [onReloadReady]);

    const handleOpenNewElement = () => {
        if (onCreateCallback) onCreateCallback();
        else if (CreateNewElement) setOpenNewElement(true);
    };

    const handleRefresh = () => {
        setReload(true);
    };

    return (
        <>
            <Box padding={{ top: databaseId || hidePageHeading ? "s" : "m", horizontal: "l" }}>
                {databaseId && !hidePageHeading && (
                    <BreadcrumbGroup
                        items={[
                            { text: Synonyms.Databases, href: "#/databases/" },
                            {
                                text: databaseId,
                                href: `#/databases/${databaseId}/assets/`,
                            },
                            { text: pluralNameTitleCase } as any,
                        ]}
                        ariaLabel="Breadcrumbs"
                    />
                )}
                {!hidePageHeading && (
                    <Grid gridDefinition={[{ colspan: { default: "12" } as any }]}>
                        <div>
                            <TextContent>
                                <h1>
                                    {pluralNameTitleCase}
                                    {databaseId && ` for ${databaseId}`}
                                </h1>
                            </TextContent>
                        </div>
                    </Grid>
                )}
                {error && (
                    <Grid gridDefinition={[{ colspan: { default: "12" } as any }]}>
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
                <Grid gridDefinition={[{ colspan: { default: "12" } as any }]}>
                    <TableList
                        allItems={allItems}
                        loading={loading}
                        listDefinition={listDefinition}
                        databaseId={databaseId}
                        editEnabled={editEnabled}
                        setReload={setReload}
                        UpdateSelectedElement={CreateNewElement}
                        hideDeleteButton={hideDeleteButton}
                        customFilterControls={customFilterControls}
                        customHeaderActions={customHeaderActions}
                        onSelectionChange={onSelectionChange}
                        createNewElement={
                            (CreateNewElement || onCreateCallback) && (
                                <div style={{ float: "right" }}>
                                    <SpaceBetween direction={"horizontal"} size={"m"}>
                                        <Button
                                            onClick={handleOpenNewElement}
                                            variant="primary"
                                            disabled={!!createDisabledReason}
                                            disabledReason={createDisabledReason}
                                        >
                                            Create {singularNameTitleCase}
                                        </Button>
                                    </SpaceBetween>
                                </div>
                            )
                        }
                    />
                </Grid>
                <div style={{ paddingBottom: "20px" }} />
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
    editEnabled: PropTypes.bool,
    hideDeleteButton: PropTypes.bool,
    customFilterControls: PropTypes.element,
    customHeaderActions: PropTypes.node,
    onSelectionChange: PropTypes.func,
    fetchOptions: PropTypes.object,
    includeArchived: PropTypes.bool,
    onReloadReady: PropTypes.func,
    createDisabledReason: PropTypes.string,
    hidePageHeading: PropTypes.bool,
};

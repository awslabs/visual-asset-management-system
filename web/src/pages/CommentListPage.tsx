/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
} from "@cloudscape-design/components";
import { useParams } from "react-router";
import CommentTableList from "../components/list/CommentTableList";
import PropTypes, { object } from "prop-types";
import ListDefinition from "../components/list/list-definitions/types/ListDefinition";
import RelatedTableList from "../components/list/RelatedTableList";
import Synonyms from "../synonyms";

export default function CommentListPage(props: any) {
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
        onSelection,
        selectedItems,
    } = props;
    const [reload, setReload] = useState<boolean>(true);
    const [loading, setLoading] = useState<boolean>(true);
    const [allItems, setAllItems] = useState<Array<any>>([]);

    const [openNewElement, setOpenNewElement] = useState(false);

    useEffect(() => {
        const getData = async () => {
            setLoading(true);
            let items;
            if (databaseId) {
                items = await fetchElements({ databaseId: databaseId });
            } else {
                items = await fetchAllElements();
            }

            if (items !== false && Array.isArray(items)) {
                setLoading(false);
                setReload(false);
                setAllItems(
                    //@todo fix workflow delete return
                    items.filter((item) => item.databaseId.indexOf("#deleted") === -1)
                );
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
                        ]}
                        ariaLabel="Breadcrumbs"
                    />
                )}
                <Grid gridDefinition={[{ colspan: { default: 6 } }]}>
                    <div>
                        <TextContent>
                            <h1>
                                {pluralNameTitleCase}
                                {databaseId && ` for ${databaseId}`}
                            </h1>
                        </TextContent>
                    </div>
                </Grid>
                <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                    {isRelatedTable && (
                        <RelatedTableList
                            allItems={allItems}
                            loading={loading}
                            listDefinition={listDefinition}
                            databaseId={databaseId}
                            setReload={setReload}
                        />
                    )}
                    <CommentTableList
                        allItems={allItems}
                        loading={loading}
                        listDefinition={listDefinition}
                        databaseId={databaseId}
                        editEnabled={editEnabled}
                        setReload={setReload}
                        onSelection={onSelection}
                        selectedItems={selectedItems}
                        UpdateSelectedElement={CreateNewElement}
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

CommentListPage.propTypes = {
    singularName: PropTypes.string.isRequired,
    singularNameTitleCase: PropTypes.string.isRequired,
    pluralName: PropTypes.string.isRequired,
    pluralNameTitleCase: PropTypes.string.isRequired,
    listDefinition: PropTypes.instanceOf(ListDefinition).isRequired,
    CreateNewElement: PropTypes.func,
    fetchElements: PropTypes.func.isRequired,
    fetchAllElements: PropTypes.func,
    onCreateCallback: PropTypes.func,
    onSelection: PropTypes.func,
    selectedItems: PropTypes.arrayOf(object),
    isRelatedTable: PropTypes.bool,
    editEnabled: PropTypes.bool,
};

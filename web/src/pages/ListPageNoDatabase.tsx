/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Grid from "@cloudscape-design/components/grid";
import SpaceBetween from "@cloudscape-design/components/space-between";
import TextContent from "@cloudscape-design/components/text-content";
import TableList from "../components/list/TableList";
import PropTypes from "prop-types";
import ListDefinition from "../components/list/list-definitions/types/ListDefinition";
import RelatedTableList from "../components/list/RelatedTableList";

export default function ListPageNoDatabase(props: any) {
    const {
        singularNameTitleCase,
        pluralNameTitleCase,
        listDefinition,
        CreateNewElement,
        fetchElements,
        fetchAllElements,
        onCreateCallback,
        isRelatedTable,
        editEnabled,
    } = props;
    const [reload, setReload] = useState(true);
    const [loading, setLoading] = useState(true);
    const [allItems, setAllItems] = useState<Array<any>>([]);

    const [openNewElement, setOpenNewElement] = useState(false);

    useEffect(() => {
        const getData = async () => {
            setLoading(true);
            let items = await fetchAllElements();

            if (items !== false && Array.isArray(items)) {
                setLoading(false);
                setReload(false);
                setAllItems(items);
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
                <Grid gridDefinition={[{ colspan: 12 }]}>
                    {isRelatedTable && (
                        <RelatedTableList
                            allItems={allItems}
                            loading={loading}
                            listDefinition={listDefinition}
                            setReload={setReload}
                        />
                    )}
                    <TableList
                        allItems={allItems}
                        loading={loading}
                        listDefinition={listDefinition}
                        editEnabled={editEnabled}
                        setReload={setReload}
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
    isRelatedTable: PropTypes.bool,
    editEnabled: PropTypes.bool,
};

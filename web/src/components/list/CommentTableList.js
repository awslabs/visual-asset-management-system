/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from "react";
import PropTypes from "prop-types";
import { useCollection } from "@cloudscape-design/collection-hooks";
import {
    Button,
    Grid,
    Header,
    Pagination,
    Select,
    Table,
    TextFilter,
    Flashbar,
    SpaceBetween,
} from "@cloudscape-design/components";
import { EmptyState } from "../../common/common-components";
import ListDefinition from "./list-definitions/types/ListDefinition";

export default function CommentTableList(props) {
    //props
    const {
        allItems,
        loading,
        listDefinition,
        databaseId,
        setReload,
        createNewElement,
        UpdateSelectedElement,
        editEnabled,
        onSelection,
        selectedItems,
    } = props;
    const {
        columnDefinitions,
        visibleColumns,
        filterColumns,
        pluralName,
        pluralNameTitleCase,
        deleteFunction,
    } = listDefinition;
    const filteredVisibleColumns = visibleColumns.filter((columnName) => {
        if (!databaseId) return true;
        if (columnName === "databaseId") return false;
        return true;
    });
    const filteredFilterColumns = filterColumns.filter((filterColumn) => {
        if (!databaseId) return true;
        if (filterColumn.name === "databaseId") return false;
        return true;
    });
    //state
    const [editOpen, setEditOpen] = useState(false);

    const [activeFilters, setActiveFilters] = useState(
        filteredFilterColumns.reduce((acc, cur) => {
            acc[cur.name] = null;
            return acc;
        }, {})
    );
    const [deleting, setDeleting] = useState(false);
    const [deleteResult, setDeleteResult] = useState({
        result: "",
        items: [],
    });
    //private functions
    const getMatchesCountText = (items) => {
        return `Found ${items} ${pluralName}.`;
    };
    const highlightMatches = (text, match = "") => {
        let newText = text + "";
        if (match !== "") {
            match = match.split(" ").map((word) => word.toLowerCase());
            for (let i = 0; i < match.length; i++) {
                const regEx = new RegExp(match[i], "ig");
                newText = newText.replaceAll(regEx, ($replace) => `||${$replace}||`);
            }
            return newText.split("||").map((segment, i) => {
                if (match.includes(segment.toLowerCase())) {
                    return <strong key={i}>{segment}</strong>;
                }
                return <span key={i}>{segment}</span>;
            });
        }
        return newText;
    };

    //implementation per polaris docs example
    const { items, actions, filteredItemsCount, collectionProps, filterProps, paginationProps } =
        useCollection(allItems, {
            filtering: {
                empty: (
                    <EmptyState
                        title={`No ${pluralName}`}
                        subtitle={`No ${pluralName} to display.`}
                    />
                ),
                noMatch: (
                    <EmptyState
                        title="No matches"
                        subtitle="We can’t find a match."
                        action={
                            <Button onClick={() => actions.setFiltering("")}>Clear filter</Button>
                        }
                    />
                ),
                filteringFunction: (item, filteringText) => {
                    const filteringTextLowerCase = filteringText.toLowerCase();
                    if (filteringTextLowerCase == "") {
                        return true;
                    }

                    for (let i = 0; i < filteredFilterColumns.length; i++) {
                        const filterColumnName = filteredFilterColumns[i].name;
                        if (
                            activeFilters[filterColumnName] !== null &&
                            item[filterColumnName] !== activeFilters[filterColumnName]
                        ) {
                            return false;
                        }
                    }

                    for (let i = 0; i < filteredVisibleColumns.length; i++) {
                        const visibleColumnName = filteredVisibleColumns[i];
                        if (
                            item[visibleColumnName] !== undefined &&
                            item[visibleColumnName]
                                .toString()
                                .toLowerCase()
                                .indexOf(filteringTextLowerCase) !== -1
                        ) {
                            return true;
                        }
                    }
                    return false;
                },
            },
            pagination: { pageSize: 15 },
            sorting: {},

            selection: {},
        });

    const handleFilterSelected = (prop, value) => {
        const newActiveFilters = Object.assign({}, activeFilters);
        newActiveFilters[prop] = value;
        setActiveFilters(newActiveFilters);
    };

    const handleDeleteElements = async (selected) => {
        setDeleting(true);
        for (let i = 0; i < selected.length; i++) {
            const result = await deleteFunction(selected[i]);
            if (result !== false && Array.isArray(result)) {
                if (result[0] === false) {
                    setDeleteResult({
                        result: "Error",
                        items: [
                            {
                                header: "Failed to Delete",
                                type: "error",
                                content: result[1],
                                dismissible: true,
                                dismissLabel: "Dismiss message",
                                onDismiss: () => setDeleteResult([]),
                            },
                        ],
                    });
                } else {
                    setDeleteResult({
                        result: "Success",
                        items: [],
                    });
                }
            }
        }
        setDeleting(false);
        setReload(true);
    };

    return (
        <>
            <Flashbar items={deleteResult.items} />
            <Table
                {...collectionProps}
                header={
                    <Header
                        counter={
                            items.length !== allItems.length
                                ? `(${items.length}/${allItems.length})`
                                : `(${allItems.length})`
                        }
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                {editEnabled && (
                                    <Button
                                        disabled={
                                            deleting || collectionProps.selectedItems?.length !== 1
                                        }
                                        onClick={() => {
                                            console.log("Edit", collectionProps.selectedItems[0]);
                                            setEditOpen(true);
                                        }}
                                    >
                                        Edit
                                    </Button>
                                )}

                                <Button
                                    disabled={
                                        deleting || collectionProps.selectedItems.length === 0
                                    }
                                    onClick={() =>
                                        handleDeleteElements(collectionProps.selectedItems)
                                    }
                                >
                                    Delete Selected
                                </Button>
                                {createNewElement}
                            </SpaceBetween>
                        }
                    >
                        {pluralNameTitleCase}
                    </Header>
                }
                columnDefinitions={columnDefinitions.map(
                    ({ id, header, CellWrapper, sortingField }) => {
                        return {
                            id,
                            header,
                            cell: (e) => (
                                <CellWrapper item={e}>
                                    {highlightMatches(
                                        e[id],
                                        (() => {
                                            const textFilterCaptureElement =
                                                document.getElementById("textFilterCapture");
                                            const textFilterInputElement =
                                                textFilterCaptureElement.querySelectorAll(
                                                    ":scope input"
                                                )[0];
                                            return textFilterInputElement?.value;
                                        })()
                                    )}
                                </CellWrapper>
                            ),
                            sortingField,
                        };
                    }
                )}
                visibleColumns={filteredVisibleColumns}
                items={items}
                loading={loading}
                selectionType={"single"}
                //@todo add aria pagination label
                pagination={<Pagination {...paginationProps} />}
                selectedItems={selectedItems}
                onSelectionChange={({ detail }) => onSelection(detail.selectedItems)}
                filter={
                    <Grid
                        gridDefinition={[
                            { colspan: { default: "7" } },
                            { colspan: { default: "5" } },
                        ]}
                    >
                        <div id="textFilterCapture">
                            <TextFilter
                                id={"test"}
                                {...filterProps}
                                countText={getMatchesCountText(filteredItemsCount)}
                                filteringAriaLabel={`Filter ${pluralName}`}
                            />
                        </div>
                        <div style={{ float: "right" }}>
                            <Grid
                                gridDefinition={filteredFilterColumns.map((filterColumn, i) => {
                                    return {
                                        colspan: {
                                            default: String(
                                                Math.floor(12 / (filterColumn.length + 1))
                                            ),
                                        },
                                    };
                                })}
                            >
                                {filteredFilterColumns.map((filterColumn, i) => {
                                    const selectedValue = activeFilters[filterColumn.name];
                                    return (
                                        <Select
                                            key={i}
                                            selectedOption={
                                                !selectedValue
                                                    ? null
                                                    : {
                                                          label: selectedValue,
                                                          value: selectedValue,
                                                      }
                                            }
                                            onChange={({ detail }) => {
                                                handleFilterSelected(
                                                    filterColumn.name,
                                                    detail?.selectedOption?.value
                                                );
                                            }}
                                            options={[
                                                {
                                                    label: <em>all</em>,
                                                    value: null,
                                                },
                                            ].concat(
                                                [
                                                    ...new Set(
                                                        allItems.map(
                                                            (row) => row[filterColumn.name]
                                                        )
                                                    ),
                                                ].map((cellValue) => {
                                                    return { label: cellValue, value: cellValue };
                                                })
                                            )}
                                            placeholder={filterColumn.placeholder}
                                            selectedAriaLabel="Selected"
                                        />
                                    );
                                })}
                            </Grid>
                        </div>
                    </Grid>
                }
            />

            {UpdateSelectedElement && collectionProps.selectedItems.length === 1 && (
                <UpdateSelectedElement
                    open={editOpen}
                    setOpen={setEditOpen}
                    setReload={setReload}
                    initState={collectionProps.selectedItems[0]}
                />
            )}
        </>
    );
}

CommentTableList.propTypes = {
    allItems: PropTypes.array.isRequired,
    loading: PropTypes.bool.isRequired,
    setReload: PropTypes.func.isRequired,
    onSelection: PropTypes.func,
    selectedItems: PropTypes.array,

    listDefinition: PropTypes.instanceOf(ListDefinition).isRequired,
    databaseId: PropTypes.string,
    editEnabled: PropTypes.bool,
    UpdateSelectedElement: PropTypes.func,
    createNewElement: PropTypes.element,
};

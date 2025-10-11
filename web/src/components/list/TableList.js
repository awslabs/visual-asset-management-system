/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
    Modal,
    Box,
    Alert,
} from "@cloudscape-design/components";
import { EmptyState } from "../../common/common-components";
import ListDefinition from "./list-definitions/types/ListDefinition";

export default function TableList(props) {
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
        onReload,
        hideDeleteButton = false,
    } = props;
    const {
        columnDefinitions,
        visibleColumns,
        filterColumns,
        pluralName,
        pluralNameTitleCase,
        singularNameTitleCase,
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
    const [showDeleteModal, setShowDeleteModal] = useState(false);

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
                    for (let i = 0; i < filteredFilterColumns.length; i++) {
                        const filterColumnName = filteredFilterColumns[i].name;
                        if (activeFilters[filterColumnName] !== null) {
                            if (item[filterColumnName] !== activeFilters[filterColumnName])
                                return false;
                        }
                    }

                    const filteringTextLowerCase = filteringText.toLowerCase();
                    if (filteringTextLowerCase !== "") {
                        for (let i = 0; i < filteredVisibleColumns.length; i++) {
                            const visibleColumnName = filteredVisibleColumns[i];
                            if (
                                item[visibleColumnName] !== undefined &&
                                item[visibleColumnName]
                                    ?.toString()
                                    .toLowerCase()
                                    .indexOf(filteringTextLowerCase) !== -1
                            ) {
                                return true;
                            }
                        }
                        return false;
                    }
                    return true;
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
                                content: result[1] + ". " + result[2],
                                dismissible: true,
                                dismissLabel: "Dismiss message",
                                onDismiss: () =>
                                    setDeleteResult({
                                        result: "",
                                        items: [],
                                    }),
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
        if (onReload) {
            onReload();
        }
    };

    function DeleteModal({ selectedItems, onCancel, onOk }) {
        let length = selectedItems.length;
        let title = length > 1 ? pluralNameTitleCase : singularNameTitleCase;
        const shouldHideCancelButton = pluralName === "tag types";
        let itemNames = [];
        if (pluralName === "tag types") {
            for (let i = 0; i < length; i++) {
                itemNames[i] = selectedItems[i]?.tagTypeName || "unknown";
            }
        }
        if (pluralName === "tags") {
            for (let i = 0; i < length; i++) {
                itemNames[i] = selectedItems[i]?.tagName || "unknown";
            }
        }
        if (pluralName === "Subscriptions") {
            for (let i = 0; i < length; i++) {
                itemNames[i] = selectedItems[i]?.entityValue || "unknown";
            }
        }
        if (pluralName === "Roles") {
            for (let i = 0; i < length; i++) {
                itemNames[i] = selectedItems[i]?.roleName || "unknown";
            }
        }
        if (pluralName === "databases") {
            for (let i = 0; i < length; i++) {
                itemNames[i] = selectedItems[i]?.databaseId || "unknown";
            }
        }
        if (pluralName === "Users in Roles") {
            for (let i = 0; i < length; i++) {
                itemNames[i] = selectedItems[i]?.userId || "unknown";
            }
        }
        const showFeatureComingSoonModal = shouldHideCancelButton;
        return (
            <>
                <Modal
                    onDismiss={onCancel}
                    visible={showDeleteModal}
                    size="medium"
                    footer={
                        <Box float="right">
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button variant="link" onClick={onCancel}>
                                    No
                                </Button>
                                <Button variant="primary" onClick={onOk}>
                                    Yes
                                </Button>
                            </SpaceBetween>
                        </Box>
                    }
                    header={"Delete " + title}
                >
                    <div>
                        <p>
                            {`Do you want to delete ${title}?: '`}
                            <i>{itemNames.join(", ")}</i>
                            {`' ?`}
                        </p>
                    </div>
                </Modal>
            </>
        );
    }

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
                                            setEditOpen(true);
                                        }}
                                    >
                                        Edit
                                    </Button>
                                )}
                                {!hideDeleteButton && (
                                    <Button
                                        disabled={
                                            deleting || collectionProps.selectedItems.length === 0
                                        }
                                        onClick={() => {
                                            setShowDeleteModal(true);
                                        }}
                                    >
                                        Delete Selected
                                    </Button>
                                )}
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
                selectionType={"multi"}
                pagination={<Pagination {...paginationProps} />}
                filter={
                    <Grid
                        gridDefinition={[
                            { colspan: { default: "7" } },
                            { colspan: { default: "5" } },
                        ]}
                    >
                        <div
                            id="textFilterCapture"
                            style={{ display: "flex", alignItems: "center", gap: "8px" }}
                        >
                            <div style={{ flex: 1 }}>
                                <TextFilter
                                    id={"test"}
                                    {...filterProps}
                                    countText={getMatchesCountText(filteredItemsCount)}
                                    filteringAriaLabel={`Filter ${pluralName}`}
                                />
                            </div>
                            <Button
                                iconName="refresh"
                                variant="icon"
                                onClick={() => setReload(true)}
                                loading={loading}
                                ariaLabel="Refresh data"
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
                                    if (
                                        pluralName !== "tag types" &&
                                        pluralName !== "tags" &&
                                        pluralName !== "Subscriptions" &&
                                        pluralName !== "Roles" &&
                                        pluralName !== "User Roles"
                                    )
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
                                                        return {
                                                            label: cellValue,
                                                            value: cellValue,
                                                        };
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
            <DeleteModal
                selectedItems={collectionProps.selectedItems}
                onCancel={() => setShowDeleteModal(false)}
                onOk={() => {
                    handleDeleteElements(collectionProps.selectedItems);
                    setShowDeleteModal(false);
                }}
            />

            {UpdateSelectedElement && collectionProps.selectedItems.length === 1 && (
                <UpdateSelectedElement
                    open={editOpen}
                    setOpen={setEditOpen}
                    setReload={setReload}
                    initState={collectionProps.selectedItems[0]}
                    reloadChild={onReload}
                />
            )}
        </>
    );
}

TableList.propTypes = {
    allItems: PropTypes.array.isRequired,
    loading: PropTypes.bool.isRequired,
    setReload: PropTypes.func.isRequired,
    listDefinition: PropTypes.instanceOf(ListDefinition).isRequired,
    databaseId: PropTypes.string,
    editEnabled: PropTypes.bool,
    UpdateSelectedElement: PropTypes.func,
    createNewElement: PropTypes.element,
    onReload: PropTypes.func,
};

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

export default function CommentTableList(props: any) {
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
    const filteredVisibleColumns = visibleColumns.filter((columnName: any) => {
        if (!databaseId) return true;
        if (columnName === "databaseId") return false;
        return true;
    });
    const filteredFilterColumns = filterColumns.filter((filterColumn: any) => {
        if (!databaseId) return true;
        if (filterColumn.name === "databaseId") return false;
        return true;
    });
    //state
    const [editOpen, setEditOpen] = useState(false);

    const [activeFilters, setActiveFilters] = useState<any>(
        filteredFilterColumns.reduce((acc: any, cur: any) => {
            acc[cur.name] = null;
            return acc;
        }, {})
    );
    const [deleting, setDeleting] = useState(false);
    const [deleteResult, setDeleteResult] = useState<any>({
        result: "",
        items: [],
    });
    //private functions
    const getMatchesCountText = (items: any) => {
        return `Found ${items} ${pluralName}.`;
    };
    const highlightMatches = (text: any, match: any = ""): any => {
        let newText = text + "";
        if (match !== "") {
            match = match.split(" ").map((word: any) => word.toLowerCase());
            for (let i = 0; i < match.length; i++) {
                const regEx = new RegExp(match[i], "ig");
                newText = newText.replaceAll(regEx, ($replace: any) => `||${$replace}||`);
            }
            return newText.split("||").map((segment: any, i: number) => {
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
                filteringFunction: (item: any, filteringText: string) => {
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

    const handleFilterSelected = (prop: any, value: any) => {
        const newActiveFilters = Object.assign({}, activeFilters);
        newActiveFilters[prop] = value;
        setActiveFilters(newActiveFilters);
    };

    const handleDeleteElements = async (selected: any) => {
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
                                            deleting ||
                                            (collectionProps.selectedItems as any)?.length !== 1
                                        }
                                        onClick={() => {
                                            console.log(
                                                "Edit",
                                                (collectionProps.selectedItems as any)[0]
                                            );
                                            setEditOpen(true);
                                        }}
                                    >
                                        Edit
                                    </Button>
                                )}

                                <Button
                                    disabled={
                                        deleting ||
                                        (collectionProps.selectedItems as any).length === 0
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
                    ({ id, header, CellWrapper, sortingField }: any) => {
                        return {
                            id,
                            header,
                            cell: (e: any) => (
                                <CellWrapper item={e}>
                                    {highlightMatches(
                                        e[id],
                                        (() => {
                                            const textFilterCaptureElement =
                                                document.getElementById("textFilterCapture");
                                            const textFilterInputElement =
                                                textFilterCaptureElement!.querySelectorAll(
                                                    ":scope input"
                                                )[0] as HTMLInputElement;
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
                        gridDefinition={[{ colspan: { default: 7 } }, { colspan: { default: 5 } }]}
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
                                gridDefinition={filteredFilterColumns.map(
                                    (filterColumn: any, i: number) => {
                                        return {
                                            colspan: {
                                                default: Math.floor(12 / (filterColumn.length + 1)),
                                            },
                                        };
                                    }
                                )}
                            >
                                {filteredFilterColumns.map((filterColumn: any, i: number) => {
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
                                            options={
                                                (
                                                    [
                                                        {
                                                            label: <em>all</em>,
                                                            value: null,
                                                        },
                                                    ] as any
                                                ).concat(
                                                    [
                                                        ...new Set(
                                                            allItems.map(
                                                                (row: any) => row[filterColumn.name]
                                                            )
                                                        ),
                                                    ].map((cellValue: any) => {
                                                        return {
                                                            label: cellValue,
                                                            value: cellValue,
                                                        };
                                                    })
                                                ) as any
                                            }
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

            {UpdateSelectedElement && (collectionProps.selectedItems as any).length === 1 && (
                <UpdateSelectedElement
                    open={editOpen}
                    setOpen={setEditOpen}
                    setReload={setReload}
                    initState={(collectionProps.selectedItems as any)[0]}
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

    listDefinition: PropTypes.instanceOf(ListDefinition as any).isRequired,
    databaseId: PropTypes.string,
    editEnabled: PropTypes.bool,
    UpdateSelectedElement: PropTypes.func,
    createNewElement: PropTypes.element,
};

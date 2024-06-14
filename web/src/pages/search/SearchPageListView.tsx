import Table, { TableProps } from "@cloudscape-design/components/table";
import {
    CollectionPreferences,
    Header,
    Link,
    Pagination,
    Box,
    Button,
    SpaceBetween,
    Modal,
    Alert,
    Input,
    Grid,
    Select,
    FormField,
} from "@cloudscape-design/components";
import {
    changeFilter,
    changeRectype,
    deleteSelected,
    paginateSearch,
    search,
    sortSearch,
} from "./SearchPropertyFilter";
import { INITIAL_STATE, SearchPageViewProps } from "./SearchPage";
import Synonyms from "../../synonyms";
import { EmptyState } from "../../common/common-components";
import { useNavigate } from "react-router-dom";
import DatabaseSelector from "../../components/selectors/DatabaseSelector";
import { useEffect, useState } from "react";
import { API } from "aws-amplify";
import { fetchtagTypes } from "../../services/APIService";
var tagTypes: any;

function columnRender(e: any, name: string, value: any) {
    if (name === "str_databaseid") {
        return (
            <Box>
                <Link href={`#/databases/${e["str_databaseid"]}/assets/`}>{value}</Link>
            </Box>
        );
    }
    if (name === "str_assetname") {
        return (
            <Box>
                <Link href={`#/databases/${e["str_databaseid"]}/assets/${e["str_assetid"]}`}>
                    {value}
                </Link>
            </Box>
        );
    } else if (name === "list_tags" && Array.isArray(value)) {
        const tagsWithType = value.map((tag) => {
            if (tagTypes)
                for (const tagType of tagTypes) {
                    if (tagType.tags.includes(tag)) {
                        return `${tag} (${tagType.tagTypeName})`;
                    }
                }
            return tag;
        });

        return <Box>{tagsWithType.join(", ")}</Box>;
    } else if (
        name.indexOf("str") === 0 ||
        name.indexOf("date_") === 0 ||
        name.indexOf("num_") === 0
    ) {
        return <Box>{value}</Box>;
    }
}

function SearchPageListView({ state, dispatch }: SearchPageViewProps) {
    // identify all the names of columns from state.result.hits.hits
    // create a column definition for each column

    useEffect(() => {
        fetchtagTypes().then((res) => {
            tagTypes = res;
        });
    }, []);
    const navigate = useNavigate();

    if (!state?.initialResult) {
        return <div>Loading..</div>;
    }

    const { columnNames } = state;

    const columnDefinitions = columnNames?.map((name: string) => {
        if (name === "str_asset") {
            return {
                id: name,
                header: Synonyms.Asset,
                cell: (e: any) => columnRender(e, name, e[name]),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "str_databaseid") {
            return {
                id: name,
                header: Synonyms.Database,
                cell: (e: any) => columnRender(e, name, e[name]),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "str_assettype") {
            return {
                id: name,
                header: "Type",
                cell: (e: any) => columnRender(e, name, e[name]),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "list_tags") {
            return {
                id: name,
                header: "Tags",
                cell: (e: any) => columnRender(e, name, e[name]),
                sortingField: name,
                isRowHeader: false,
            };
        }
        return {
            id: name,
            header:
                name === "str_assetname"
                    ? Synonyms.Asset
                    : name
                          .split("_")
                          .slice(1)
                          .map((s: string) => s.charAt(0).toUpperCase() + s.slice(1))
                          .join(" "),
            cell: (e: any) => columnRender(e, name, e[name]),
            sortingField: name,
            isRowHeader: false,
        };
    });

    const currentPage = 1 + Math.ceil(state?.pagination?.from / state?.tablePreferences?.pageSize);
    const pageCount = Math.ceil(
        state?.result?.hits?.total?.value / state?.tablePreferences?.pageSize
    );

    if (!columnDefinitions) {
        return <div>Loading...</div>;
    }

    return (
        <>
            <SpaceBetween direction="vertical" size="l">
                <Table
                    empty={
                        <EmptyState
                            title="No matches"
                            subtitle="We can’t find a match."
                            action={
                                <Button
                                    onClick={() => {
                                        dispatch({ type: "query-criteria-cleared" });
                                        setTimeout(() => {
                                            search(INITIAL_STATE, { state, dispatch });
                                        }, 10);
                                    }}
                                >
                                    Clear filter
                                </Button>
                            }
                        />
                    }
                    columnDefinitions={columnDefinitions}
                    selectedItems={state?.selectedItems}
                    isItemDisabled={(item: any) => {
                        return state?.disableSelection || false;
                    }}
                    onSelectionChange={({ detail }) => {
                        if (detail.selectedItems) {
                            dispatch({
                                type: "set-selected-items",
                                selectedItems: detail.selectedItems,
                            });
                        }
                    }}
                    selectionType={state?.filters._rectype.value !== "asset" ? undefined : "multi"}
                    trackBy="_id"
                    visibleColumns={state?.tablePreferences?.visibleContent}
                    loading={state.loading}
                    loadingText="Loading"
                    items={state?.result?.hits?.hits?.map((hit: any) => ({
                        ...hit._source,
                        _id: hit._id,
                    }))}
                    sortingColumn={{
                        sortingField: state?.tableSort?.sortingField,
                    }}
                    sortingDescending={state?.tableSort?.sortingDescending}
                    onSortingChange={({ detail }) => {
                        console.log("sorting change", detail);
                        if (detail.sortingColumn.sortingField) {
                            sortSearch(
                                detail.sortingColumn.sortingField,
                                detail.isDescending || false,
                                {
                                    state,
                                    dispatch,
                                }
                            );
                        }
                    }}
                    pagination={
                        <Pagination
                            pagesCount={pageCount}
                            currentPageIndex={currentPage}
                            onChange={({ detail }) => {
                                console.log(
                                    "pagination",
                                    detail,
                                    state?.tablePreferences?.pageSize
                                );
                                paginateSearch(
                                    (detail.currentPageIndex - 1) *
                                        state?.tablePreferences?.pageSize,
                                    state?.tablePreferences?.pageSize,
                                    { state, dispatch }
                                );
                            }}
                        />
                    }
                    preferences={
                        <CollectionPreferences
                            onConfirm={({ detail }) => {
                                console.log("detail", detail);
                                dispatch({ type: "set-search-table-preferences", payload: detail });
                            }}
                            visibleContentPreference={{
                                title: "Columns",
                                options: [
                                    {
                                        label: "All columns",
                                        options: columnDefinitions
                                            .map(
                                                (
                                                    columnDefinition: TableProps.ColumnDefinition<string>
                                                ) => ({
                                                    id: columnDefinition.id,
                                                    label: columnDefinition.header,
                                                })
                                            )
                                            .map((x: any) => {
                                                if (
                                                    ["str_assetname", "str_key"].indexOf(x.id) >= 0
                                                ) {
                                                    x.alwaysVisible = true;
                                                    x.editable = false;
                                                }
                                                return x;
                                            })
                                            .sort((a: any, b: any) =>
                                                a.label.localeCompare(b.label)
                                            ),
                                    },
                                ],
                            }}
                            title="Preferences"
                            confirmLabel="Confirm"
                            cancelLabel="Cancel"
                            preferences={state.tablePreferences}
                            pageSizePreference={{
                                title: "Page size",
                                options: [
                                    { value: 10, label: "10 resources" },
                                    { value: 25, label: "25 resources" },
                                    { value: 50, label: "50 resources" },
                                    { value: 100, label: "100 resources" },
                                ],
                            }}
                        />
                    }
                    header={
                        <Header
                            children={Synonyms.Assets}
                            counter={
                                state?.result?.hits?.total?.value
                                    ? state?.result?.hits?.total?.value +
                                      (state?.result?.hits?.total?.relation === "gte" ? "+" : "")
                                    : ""
                            }
                            actions={
                                <SpaceBetween direction="horizontal" size="xs">
                                    <Button
                                        disabled={
                                            state?.selectedItems?.length === 0 ||
                                            state?.disableSelection
                                        }
                                        onClick={() => {
                                            // deleteSelected({ state, dispatch });
                                            dispatch({ type: "clicked-initial-delete" });
                                        }}
                                    >
                                        Delete Selected
                                    </Button>
                                    <Button
                                        onClick={(e) => {
                                            navigate("/upload");
                                        }}
                                        variant="primary"
                                    >
                                        Create {Synonyms.Asset}
                                    </Button>
                                </SpaceBetween>
                            }
                        />
                    }
                    filter={
                        false && (
                            <Grid
                                gridDefinition={[
                                    { colspan: { default: 7 } },
                                    { colspan: { default: 5 } },
                                ]}
                            >
                                <FormField label="Keywords">
                                    <Grid
                                        gridDefinition={[
                                            { colspan: { default: 9 } },
                                            { colspan: { default: 3 } },
                                        ]}
                                    >
                                        <Input
                                            placeholder="Search"
                                            type="search"
                                            onChange={(e) => {
                                                dispatch({
                                                    type: "query-updated",
                                                    query: e.detail.value,
                                                });
                                            }}
                                            onKeyDown={({ detail }) => {
                                                if (detail.key === "Enter") {
                                                    search({}, { state, dispatch });
                                                }
                                            }}
                                            value={state?.query}
                                        />
                                        <Button
                                            variant="primary"
                                            onClick={(e) => {
                                                search({}, { state, dispatch });
                                            }}
                                        >
                                            Search
                                        </Button>
                                    </Grid>
                                </FormField>
                                <SpaceBetween direction="horizontal" size="xs">
                                    <FormField label="Asset Type">
                                        <Select
                                            selectedOption={
                                                state?.filters?._rectype || {
                                                    label: Synonyms.Assets,
                                                    value: "asset",
                                                }
                                            }
                                            onChange={({ detail }) =>
                                                // changeRectype(e.detail.selectedOption, { state, dispatch })
                                                changeFilter("_rectype", detail.selectedOption, {
                                                    state,
                                                    dispatch,
                                                })
                                            }
                                            options={[
                                                { label: Synonyms.Assets, value: "asset" },
                                                { label: "Files", value: "s3object" },
                                            ]}
                                            placeholder="Asset Type"
                                        />
                                    </FormField>
                                    <FormField label="File Type">
                                        <Select
                                            selectedOption={state?.filters?.str_assettype}
                                            placeholder="File Type"
                                            options={[
                                                { label: "All", value: "all" },
                                                ...(state?.result?.aggregations?.str_assettype?.buckets.map(
                                                    (b: any) => {
                                                        return {
                                                            label: `${b.key} (${b.doc_count})`,
                                                            value: b.key,
                                                        };
                                                    }
                                                ) || []),
                                            ]}
                                            onChange={({ detail }) =>
                                                changeFilter(
                                                    "str_assettype",
                                                    detail.selectedOption,
                                                    {
                                                        state,
                                                        dispatch,
                                                    }
                                                )
                                            }
                                        />
                                    </FormField>
                                    <FormField label="Database">
                                        <Select
                                            selectedOption={state?.filters?.str_databaseid}
                                            placeholder="Database"
                                            options={[
                                                { label: "All", value: "all" },
                                                ...(state?.result?.aggregations?.str_databaseid?.buckets.map(
                                                    (b: any) => {
                                                        return {
                                                            label: `${b.key} (${b.doc_count})`,
                                                            value: b.key,
                                                        };
                                                    }
                                                ) || []),
                                            ]}
                                            onChange={({ detail }) =>
                                                changeFilter(
                                                    "str_databaseid",
                                                    detail.selectedOption,
                                                    {
                                                        state,
                                                        dispatch,
                                                    }
                                                )
                                            }
                                        />
                                    </FormField>
                                </SpaceBetween>
                            </Grid>
                        )
                    }
                />
            </SpaceBetween>
            <Modal
                visible={state?.showDeleteModal}
                onDismiss={() => dispatch({ type: "clicked-cancel-delete" })}
                header={`Delete ${
                    state?.selectedItems?.length > 1 ? Synonyms.Assets : Synonyms.Asset
                }`}
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="link"
                                onClick={() => dispatch({ type: "clicked-cancel-delete" })}
                            >
                                No
                            </Button>
                            <Button
                                variant="primary"
                                onClick={() => deleteSelected({ state, dispatch })}
                            >
                                Yes
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                <SpaceBetween direction="vertical" size="xs">
                    <Box variant="p">
                        Do you want to delete{" "}
                        {state?.selectedItems?.length > 1 ? (
                            <b>
                                {state?.selectedItems?.length} {Synonyms.Assets}
                            </b>
                        ) : (
                            <b>{state?.selectedItems?.[0]?.str_assetname}</b>
                        )}
                        ?
                    </Box>
                </SpaceBetween>
            </Modal>
        </>
    );
}

export default SearchPageListView;

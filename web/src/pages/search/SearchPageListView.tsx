import React, { Dispatch, ReducerAction } from "react";
import Table, { TableProps } from "@cloudscape-design/components/table";
import { CollectionPreferences, Header, Link, Pagination } from "@cloudscape-design/components";
import { paginateSearch, sortSearch } from "./SearchPropertyFilter";
import { SearchPageViewProps } from "./SearchPage";

function columnRender(e: any, name: string, value: any) {
    if (name.indexOf("str") === 0 || name.indexOf("date_") === 0) {
        return (
            <div>
                <Link href={`/databases/${e["str_databaseid"]}/assets/${e["str_assetid"]}`}>
                    {value}
                </Link>
            </div>
        );
    }
}

function SearchPageListView({ state, dispatch }: SearchPageViewProps) {
    // identify all the names of columns from state.result.hits.hits
    // create a column definition for each column

    if (!state?.result?.hits?.hits) {
        return <div>No results</div>;
    }
    if (state?.result?.hits?.total.value === 0) {
        return <div>No results</div>;
    }

    const columnNames = state?.result?.hits?.hits
        ?.map((hit: any) => Object.keys(hit?._source))
        ?.reduce((acc: { [key: string]: string }, val: string) => (acc[val] = val), {})
        ?.filter((name: string) => name.indexOf("_") > 0);

    const columnDefinitions = columnNames?.map((name: string) => ({
        id: name,
        header: name
            .split("_")
            .slice(1)
            .map((s: string) => s.charAt(0).toUpperCase() + s.slice(1))
            .join(" "),
        cell: (e: any) => columnRender(e, name, e[name]),
        sortingField: name,
        isRowHeader: true,
    }));
    console.log("names", columnNames, columnDefinitions);

    const currentPage = 1 + Math.ceil(state?.pagination?.from / state?.tablePreferences?.pageSize);
    const pageCount = state?.result?.hits?.total?.value / state?.tablePreferences?.pageSize;
    console.log("current page", currentPage, pageCount);

    return (
        <Table
            columnDefinitions={columnDefinitions}
            visibleColumns={
                state?.tablePreferences?.visibleContent ||
                columnDefinitions.slice(0, 4).map((c: any) => c.id)
            }
            loading={state.loading}
            items={state?.result?.hits?.hits?.map((hit: any) => hit._source)}
            sortingColumn={{
                sortingField: state?.tableSort?.sortingField,
            }}
            sortingDescending={state?.tableSort?.sortingDescending}
            onSortingChange={({ detail }) => {
                console.log("sorting change", detail);
                if (detail.sortingColumn.sortingField) {
                    sortSearch(detail.sortingColumn.sortingField, detail.isDescending || false, {
                        state,
                        dispatch,
                    });
                }
            }}
            pagination={
                <Pagination
                    pagesCount={pageCount}
                    currentPageIndex={currentPage}
                    onChange={({ detail }) => {
                        paginateSearch(
                            (detail.currentPageIndex - 1) * state?.tablePreferences?.pageSize,
                            state?.tablePreferences?.pageSize,
                            { state, dispatch }
                        );
                    }}
                    // onNextPageClick={(e) =>
                    //     paginateSearch(
                    //         state?.pagination?.from + state?.tablePreferences?.pageSize,
                    //         state?.tablePreferences?.pageSize,
                    //         { state, dispatch }
                    //     )
                    // }
                    // onPreviousPageClick={(e) =>
                    //     paginateSearch(
                    //         state?.pagination?.from - state?.tablePreferences?.pageSize,
                    //         state?.tablePreferences?.pageSize,
                    //         { state, dispatch }
                    //     )
                    // }
                />
            }
            header={
                <Header
                    children="Matches"
                    counter={
                        state?.result?.hits?.total?.value +
                        (state?.result?.hits?.total?.relation === "gte" ? "+" : "")
                    }
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
                                    .sort((a: any, b: any) => a.label.localeCompare(b.label)),
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
        />
    );
}

export default SearchPageListView;

import * as React from "react";
import PropertyFilter, { PropertyFilterProps } from "@cloudscape-design/components/property-filter";
import Synonyms from "../../synonyms";
import { Dispatch, ReducerAction, useEffect, useState } from "react";
import { API } from "aws-amplify";
import {
    PropertyFilterOperator,
    PropertyFilterProperty,
} from "@cloudscape-design/collection-hooks";
import { Select, SpaceBetween } from "@cloudscape-design/components";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import { deleteElement } from "../../services/APIService";
import { execPath } from "process";

interface SearchPropertyFilterProps {
    state: any;
    dispatch: Dispatch<ReducerAction<any>>;
}

function filterFilter(key: string, filters: { [key: string]: OptionDefinition }) {
    return filters[key] && filters[key].value !== "all";
}

async function search(overrides: any, { dispatch, state }: SearchPropertyFilterProps) {
    const filters: object[] = Object.keys(state?.filters)
        .filter((key: string) => filterFilter(key, state?.filters))
        .map((key) => {
            return {
                query_string: {
                    query: `(${key}:("${state?.filters[key].value}"))`,
                },
            };
        });
    if (state.databaseId) {
        filters.push({
            query_string: { query: `(str_databaseid:("${state?.databaseId}"))` },
        });
    }

    const body = {
        tokens: state.propertyFilter?.tokens || [],
        operation: (state.propertyFilter?.operation || "AND").toUpperCase(),
        sort: state.sort,
        from: state?.pagination?.from,
        size: state?.tablePreferences?.pageSize,
        query: state?.query,
        filters,
        ...overrides,
    };
    console.log("body to send", body);
    console.log("search function - state.tableSort:", state.tableSort);
    console.log("search function - overrides.tableSort:", overrides.tableSort);
    console.log(
        "search function - will dispatch tableSort:",
        overrides.tableSort || state.tableSort
    );

    dispatch({ type: "search-results-requested" });

    try {
        const result = await API.post("api", "search", {
            "Content-type": "application/json",
            body: body,
        });
        const tableSortToPass = overrides.tableSort || state.tableSort;
        console.log("search function - dispatching tableSort:", tableSortToPass);
        dispatch({
            type: "search-result-update",
            result,
            // Always pass tableSort through to preserve it
            tableSort: tableSortToPass,
        });
    } catch (e: any) {
        console.error("Search API error:", e);
        dispatch({
            type: "search-result-error",
            error: e,
        });
        // Show error toast notification
        if (typeof window !== "undefined") {
            // Extract error message from various possible locations
            let errorMessage = "An error occurred while searching";

            if (e?.response?.data?.message) {
                errorMessage = e.response.data.message;
            } else if (e?.response?.message) {
                errorMessage = e.response.message;
            } else if (e?.message) {
                errorMessage = e.message;
            } else if (typeof e?.response?.data === "string") {
                try {
                    const parsed = JSON.parse(e.response.data);
                    errorMessage = parsed.message || errorMessage;
                } catch {
                    errorMessage = e.response.data;
                }
            }

            // Dispatch a custom event that can be caught by a toast manager
            window.dispatchEvent(
                new CustomEvent("search-error", {
                    detail: {
                        title: "Search Failed",
                        message: errorMessage,
                        type: "error",
                    },
                })
            );
        }
    }
}

export async function sortSearch(
    sortingField: string,
    isDescending: boolean,
    { state, dispatch }: SearchPropertyFilterProps
) {
    let sortingFieldIndex = sortingField;
    if (sortingField.indexOf("str_") === 0) {
        sortingFieldIndex = sortingField + ".keyword";
    }

    // Backend expects sort format: [{ field: "fieldname", order: "asc|desc" }, "_score"]
    const sort = [
        {
            field: sortingFieldIndex,
            order: isDescending ? "desc" : "asc",
        },
        "_score",
    ];

    const tableSort = {
        sortingField,
        sortingDescending: isDescending,
    };

    dispatch({
        type: "query-sort",
        sort,
        tableSort,
    });

    // Pass tableSort through the search so it's preserved in the result
    const body = {
        sort,
        tableSort, // Include this so it can be preserved
    };
    search(body, { dispatch, state });
}

// The deleteSelected function has been replaced by the AssetDeleteModal component
// which handles both archive and permanent delete operations directly

export async function paginateSearch(
    from: number,
    size: number,
    { state, dispatch }: SearchPropertyFilterProps
) {
    dispatch({
        type: "query-paginate",
        pagination: {
            from,
            size,
        },
    });
    const body = {
        from,
        size,
    };
    search(body, { dispatch, state });
}

export async function changeFilter(
    field: string,
    value: OptionDefinition,
    { state, dispatch }: SearchPropertyFilterProps
) {
    dispatch({ type: "query-filter-updated", filters: { [field]: value } });
    const filters = {
        ...state.filters,
        [field]: {
            label: field,
            value: value.value,
        },
    };
    const body = {
        filters: Object.keys(filters)
            .filter((key) => filterFilter(key, filters))
            .map((key) => {
                return {
                    query_string: {
                        query: `(${key}:("${filters[key].value}"))`,
                    },
                };
            }),
    };
    search(body, { dispatch, state });
}

export async function changeRectype(
    value: OptionDefinition,
    { state, dispatch }: SearchPropertyFilterProps
) {
    dispatch({
        type: "set-rectype",
        rectype: value,
    });
    const body = {
        filters: [{ query_string: { query: `(_rectype:(${value.value}))` } }],
    };
    search(body, { dispatch, state });
}

async function runSearch(
    detail: PropertyFilterProps.Query,
    { state, dispatch }: SearchPropertyFilterProps
) {
    dispatch({
        type: "property-filter-query-updated",
        tokens: detail.tokens,
        operation: detail.operation,
        sort: ["_score"],
    });
    const body = {
        tokens: detail.tokens,
        operation: detail.operation,
        sort: ["_score"],
        from: 0,
        size: state?.tablePreferences?.pageSize,
        filters: [{ query_string: { query: `(_rectype:(${state?.rectype.value}))` } }],
    };
    search(body, { dispatch, state });
}

function SearchPropertyFilter({ state, dispatch }: SearchPropertyFilterProps) {
    const [properties, setProperties] = useState<PropertyFilterProperty[]>([]);

    useEffect(() => {
        API.get("api", "search", {}).then((response) => {
            const prefixes = "str bool date".split(" ");
            const result = Object.keys(response?.mappings?.properties || {})
                .filter((x) => {
                    if (x.indexOf("_") === 0) return false;
                    const segments = x.split("_");
                    if (segments.length > 0 && prefixes.find((y) => y === segments[0])) {
                        return true;
                    }
                    return false;
                })
                .map((key) => {
                    let operators: PropertyFilterOperator[] = ["=", "!="];
                    if (key.indexOf("date_") === 0) {
                        operators = ["=", "!=", ">", "<", ">=", "<="];
                    }
                    const property = response?.mappings?.properties[key];
                    const result: PropertyFilterProperty = {
                        key,
                        propertyLabel: key
                            .split("_")
                            .slice(1)
                            .map((x) => x.charAt(0).toUpperCase() + x.slice(1))
                            .join(" "),
                        groupValuesLabel: property.type,
                        operators: operators,
                        defaultOperator: "=",
                    };
                    return result;
                });
            setProperties(result);
        });
    }, []);

    return (
        <SpaceBetween direction="horizontal" size="l">
            <PropertyFilter
                disabled={state.loading}
                onChange={({ detail }) => runSearch(detail, { state, dispatch })}
                query={state.propertyFilter}
                filteringProperties={properties}
                i18nStrings={{
                    filteringAriaLabel: `Find ${Synonyms.Asset}`,
                    filteringPlaceholder: `Find ${Synonyms.Asset}`,
                    operationAndText: "AND",
                    operationOrText: "OR",
                    cancelActionText: "Cancel",
                    applyActionText: "Search",
                    clearFiltersText: "Clear",
                }}
                expandToViewport
            />
            <Select
                selectedOption={state.rectype}
                onChange={(e) => changeRectype(e.detail.selectedOption, { state, dispatch })}
                options={[
                    { label: Synonyms.Assets, value: "asset" },
                    { label: "Files", value: "file" },
                ]}
            />
        </SpaceBetween>
    );
}

export default SearchPropertyFilter;
export { search };

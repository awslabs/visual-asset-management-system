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
            query_string: { query: `(str_databaseid:(${state?.databaseId}))` },
        });
    }

    const body = {
        tokens: state.propertyFilter.tokens,
        operation: state.propertyFilter.operation,
        sort: state.sort,
        from: state?.pagination?.from,
        size: state?.tablePreferences?.pageSize,
        query: state?.query,
        filters,
        ...overrides,
    };
    console.log("body to send", body);

    dispatch({ type: "search-results-requested" });

    try {
        const result = await API.post("api", "search", {
            "Content-type": "application/json",
            body: body,
        });
        dispatch({
            type: "search-result-update",
            result,
        });
    } catch (e) {
        dispatch({
            type: "search-result-error",
            error: e,
        });
    }
}

export async function sortSearch(
    sortingField: string,
    isDescending: boolean,
    { state, dispatch }: SearchPropertyFilterProps
) {
    let sortingFieldIndex = sortingField;
    if (sortingField.indexOf("str_") === 0) {
        sortingFieldIndex = sortingField + ".raw";
    }
    const sort = [
        {
            [sortingFieldIndex]: {
                missing: "_last",
                order: isDescending ? "desc" : "asc",
            },
        },
        "_score",
    ];
    dispatch({
        type: "query-sort",
        sort,
        tableSort: {
            sortingField,
            sortingDescending: isDescending,
        },
    });
    const body = {
        sort,
    };
    search(body, { dispatch, state });
}

export async function deleteSelected({ state, dispatch }: SearchPropertyFilterProps) {
    dispatch({ type: "set-delete-in-progress" });

    setTimeout(async () => {
        await state?.selectedItems.forEach(async (item: any) => {
            const [status, resp] = await deleteElement({
                elementId: "assetId",
                deleteRoute: "database/{databaseId}/assets/{assetId}",
                item: {
                    databaseId: item.str_databaseid,
                    assetId: item.str_assetid,
                },
            });
        });
        setTimeout(async () => {
            await search({}, { state, dispatch });
            dispatch({ type: "end-delete-in-progress" });
        }, 5000);
    });
}

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
                    { label: "Files", value: "s3object" },
                ]}
            />
        </SpaceBetween>
    );
}

export default SearchPropertyFilter;
export { search };

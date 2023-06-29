import * as React from "react";
import PropertyFilter, { PropertyFilterProps } from "@cloudscape-design/components/property-filter";
import Synonyms from "../../synonyms";
import { Dispatch, ReducerAction, useEffect, useState } from "react";
import { API } from "aws-amplify";
import { PropertyFilterOperator, PropertyFilterProperty } from "@cloudscape-design/collection-hooks";

interface SearchPropertyFilterProps {
    state: any;
    dispatch: Dispatch<ReducerAction<any>>;
}

async function search(body: any, { dispatch }: SearchPropertyFilterProps) {
    console.log("body to send", body);
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
        }
    });
    const body = {
        tokens: state.query.tokens,
        operation: state.query.operation,
        sort,
        size: state?.tablePreferences?.pageSize
    };
    search(body, { dispatch, state });
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
        tokens: state.query.tokens,
        operation: state.query.operation,
        sort: state.sort,
        from,
        size,
    };
    search(body, { dispatch, state });
}

async function runSearch(
    detail: PropertyFilterProps.Query,
    { state, dispatch }: SearchPropertyFilterProps
) {
    dispatch({
        type: "query-update",
        tokens: detail.tokens,
        operation: detail.operation,
        sort: ["_score"],
    });
    const body = {
        tokens: detail.tokens,
        operation: detail.operation,
        sort: ["_score"],
        from: 0,
        size: state?.tablePreferences?.pageSize
    }
    search(body, { dispatch, state });
}

function SearchPropertyFilter({ state, dispatch }: SearchPropertyFilterProps) {
    const [properties, setProperties] = useState<PropertyFilterProperty[]>([]);

    useEffect(() => {
        API.get("api", "search", {}).then((response) => {
            console.log("propss", response);
            const prefixes = "str bool date".split(" ");
            // assets1236.mappings.properties
            const result = Object.keys(response?.assets1236?.mappings?.properties || {})
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
                    const property = response?.assets1236?.mappings?.properties[key];
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
        <PropertyFilter
            disabled={state.loading}
            onChange={({ detail }) => runSearch(detail, { state, dispatch })}
            query={state.query}
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
    );
}

export default SearchPropertyFilter;

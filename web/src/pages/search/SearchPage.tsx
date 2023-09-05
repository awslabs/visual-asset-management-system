/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { Dispatch, ReducerAction, useEffect, useReducer, useState } from "react";
import SearchPropertyFilter, { changeFilter, search } from "./SearchPropertyFilter";
import Container from "@cloudscape-design/components/container";
import {
    Alert,
    BreadcrumbGroup,
    Button,
    ColumnLayout,
    Flashbar,
    FormField,
    Grid,
    Header,
    Input,
    Select,
    SpaceBetween,
    TextContent,
} from "@cloudscape-design/components";
import Synonyms from "../../synonyms";
import SearchPageSegmentedControl from "./SearchPageSegmentedControl";
import SearchPageMapView from "./SearchPageMapView";
import SearchPageListView from "./SearchPageListView";
import Box from "@cloudscape-design/components/box";
import { useParams } from "react-router";

export interface SearchPageViewProps {
    state: any;
    dispatch: Dispatch<ReducerAction<any>>;
}

interface SearchPageProps {}

const getMinMaxLatLongBounds = (result: any) => {
    let minLat = -90;
    let maxLat = 90;
    let minLong = -180;
    let maxLong = 180;
    result?.hits?.hits
        ?.filter((hit: any) => hit?._source?.gp_location?.lat && hit?._source?.gp_location?.lon)
        .forEach((hit: any) => {
            console.log("hit", hit?._source?.gp_location?.lat, hit?._source?.gp_location?.lon);
            minLat = Math.max(minLat, hit?._source?.gp_location?.lat);
            maxLat = Math.min(maxLat, hit?._source?.gp_location?.lat);
            minLong = Math.max(minLong, hit?._source?.gp_location?.lon);
            maxLong = Math.min(maxLong, hit?._source?.gp_location?.lon);
        });
    const r = {
        minLat,
        maxLat,
        minLong,
        maxLong,
    };
    console.log("bounds result", r);
    return r;
};

function searchReducer(state: any, action: any) {
    console.log("searchReducer", action);
    switch (action.type) {
        case "search-results-requested":
            return {
                ...state,
                loading: true,
                initialResult: true,
            };

        case "query-sort":
            return {
                ...state,
                sort: action.sort,
                pagination: {
                    from: 0,
                },
                tableSort: action.tableSort,
            };

        case "query-paginate":
            return {
                ...state,
                pagination: {
                    from: action.pagination.from,
                    size: action.pagination.size,
                },
                error: undefined,
                loading: true,
            };

        case "query-criteria-cleared":
            return {
                ...state,
                ...INITIAL_STATE,
            };

        case "property-filter-query-updated":
            return {
                ...state,
                propertyFilter: { tokens: action.tokens, operation: action.operation },
                error: undefined,
                loading: true,
                sort: action.sort,
                tableSort: {},
                pagination: {
                    from: 0,
                },
            };

        case "query-updated":
            return {
                ...state,
                query: action.query,
            };

        case "query-filter-updated":
            return {
                ...state,
                filters: {
                    ...state.filters,
                    ...action.filters,
                },
                loading: true,
            };

        case "search-result-update":
            if (state.map) {
                try {
                    console.log("fit map");
                    const bounds = getMinMaxLatLongBounds(action.result);
                    state.map.target.fitBounds([
                        [bounds.minLong, bounds.minLat],
                        [bounds.maxLong, bounds.maxLat],
                    ]);
                } catch (e) {
                    console.log("error fitting map", e);
                }
            }

            const columnNames =
                action?.result?.hits?.hits.length > 0
                    ? action?.result?.hits?.hits
                          ?.map((hit: any) => Object.keys(hit?._source))
                          ?.reduce(
                              (acc: { [key: string]: string }, val: string) => (acc[val] = val),
                              {}
                          )
                          ?.filter((name: string) => name.indexOf("_") > 0)
                    : [];

            const reqCols = ["str_assetname"];
            if (columnNames.indexOf("str_key") > -1) {
                reqCols.push("str_key");
            }

            const ret = {
                ...state,
                initialResult: true,
                loading: false,
                result: action.result,
                columnNames: Array.from(new Set([...reqCols, ...columnNames]).values()),
                selectedItems: [],
            };

            if (!ret?.tablePreferences?.visibleContent) {
                ret.tablePreferences = {
                    ...state.tablePreferences,
                    visibleContent: [
                        "str_key",
                        "str_assetname",
                        "str_description",
                        "str_databaseid",
                        "str_assettype",
                    ],
                };
            }

            return ret;
        case "search-result-error":
            return {
                ...state,
                loading: false,
                error: action.error,
            };
        case "set-search-table-preferences":
            action.payload.visibleContent = Array.from(
                new Set(["str_assetname", "str_key", ...action.payload.visibleContent]).values()
            );
            return {
                ...state,
                tablePreferences: action.payload,
            };

        case "set-selected-items":
            return {
                ...state,
                selectedItems: action.selectedItems,
            };

        case "set-popup-info":
            console.log(action.payload);
            return {
                ...state,
                popupInfo: action.payload,
            };
        case "set-rectype":
            return {
                ...state,
                rectype: action.rectype,
                loading: true,
                result: undefined,
            };

        case "set-view":
            return {
                ...state,
                view: action.view,
            };

        case "set-map":
            return {
                ...state,
                map: action.map,
            };

        case "set-delete-in-progress":
            return {
                ...state,
                disableSelection: true,
                showDeleteModal: false,
                notifications: [
                    {
                        type: "info",
                        dismissible: false,
                        loading: true,
                        dismissLabel: "Dismiss message",
                        content: `Deleting ${state.selectedItems.length} ${Synonyms.Assets}`,
                    },
                ],
            };

        case "end-delete-in-progress":
            return {
                ...state,
                disableSelection: false,
                showDeleteModal: false,
                notifications: [],
            };

        case "clicked-initial-delete":
            return {
                ...state,
                disableSelection: true,
                showDeleteModal: true,
            };
        case "clicked-cancel-delete":
            return {
                ...state,
                disableSelection: false,
                showDeleteModal: false,
            };

        default:
            return state;
    }
}

export const INITIAL_STATE = {
    initialResult: false,
    propertyFilter: { tokens: [], operation: "AND" },
    loading: false,
    tablePreferences: {
        pageSize: 100,
    },
    pagination: {
        from: 0,
    },
    rectype: {
        value: "asset",
        label: Synonyms.Assets,
    },
    filters: {
        _rectype: {
            label: Synonyms.Assets,
            value: "asset",
        },
    },
    query: "",
    disableSelection: false,
    view: "listview",
    columnNames: [],
    columnDefinitions: [],
    notifications: [],
};

function SearchPage(props: SearchPageProps) {
    const [useMapView] = useState(true);
    const { databaseId } = useParams();
    const [state, dispatch] = useReducer(searchReducer, { ...INITIAL_STATE, databaseId });

    useEffect(() => {
        if (!state.initialResult) {
            search({}, { dispatch, state });
        }
    }, [state]);

    return (
        <>
            <Box padding={{ top: databaseId ? "s" : "m", horizontal: "l" }}>
                {databaseId && (
                    <BreadcrumbGroup
                        items={[
                            { text: Synonyms.Databases, href: "/databases/" },
                            {
                                text: databaseId,
                                href: `/databases/${databaseId}/assets/`,
                            },
                            { text: Synonyms.Assets, href: "" },
                        ]}
                        ariaLabel="Breadcrumbs"
                    />
                )}
                <Grid gridDefinition={[{ colspan: { default: 6 } }]}>
                    <div>
                        <TextContent>
                            <h1>
                                {Synonyms.Assets}
                                {databaseId && ` for ${databaseId}`}
                            </h1>
                        </TextContent>
                    </div>
                </Grid>
                <SpaceBetween direction="vertical" size="l">
                    {state.error && (
                        <Alert
                            statusIconAriaLabel="Error"
                            type="error"
                            header={state.error.message}
                        >
                            {state.error.stack}
                        </Alert>
                    )}
                    {state?.notifications.length > 0 && <Flashbar items={state.notifications} />}

                    <Grid
                        gridDefinition={[{ colspan: { default: 7 } }, { colspan: { default: 5 } }]}
                    >
                        <FormField label="Keywords">
                            <Grid
                                gridDefinition={[
                                    { colspan: { default: 12 } },
                                    { colspan: { default: 12 } },
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
                                        changeFilter("str_assettype", detail.selectedOption, {
                                            state,
                                            dispatch,
                                        })
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
                                        changeFilter("str_databaseid", detail.selectedOption, {
                                            state,
                                            dispatch,
                                        })
                                    }
                                />
                            </FormField>
                            <FormField label="Search">
                                <Button
                                    variant="primary"
                                    onClick={(e) => {
                                        search({}, { state, dispatch });
                                    }}
                                >
                                    Search
                                </Button>
                            </FormField>
                        </SpaceBetween>
                    </Grid>

                    {useMapView && (
                        <SearchPageSegmentedControl
                            selectedId={state.view}
                            onchange={(selectedId: string) =>
                                dispatch({ type: "set-view", view: selectedId })
                            }
                        />
                    )}
                    {state.view === "mapview" ? (
                        <SearchPageMapView state={state} dispatch={dispatch} />
                    ) : (
                        <SearchPageListView state={state} dispatch={dispatch} />
                    )}
                </SpaceBetween>
            </Box>
        </>
    );
}

export default SearchPage;

/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { Dispatch, ReducerAction, useEffect, useReducer, useState } from "react";
import { Cache } from "aws-amplify";
import { useNavigate } from "react-router-dom";
import SearchPropertyFilter, { changeFilter, search } from "./SearchPropertyFilter";
import {
    Alert,
    BreadcrumbGroup,
    Button,
    Flashbar,
    FormField,
    Grid,
    Input,
    Select,
    Multiselect,
    SpaceBetween,
    TextContent,
} from "@cloudscape-design/components";
import Synonyms from "../../synonyms";
import { featuresEnabled } from "../../common/constants/featuresEnabled";
import SearchPageSegmentedControl from "./SearchPageSegmentedControl";
import SearchPageMapView from "./SearchPageMapView";
import SearchPageListView from "./SearchPageListView";
import Box from "@cloudscape-design/components/box";
import { useParams } from "react-router";
import OptionDefinition from "../../components/createupdate/form-definitions/types/OptionDefinition";
import { fetchTags } from "../../services/APIService";
import ListPage from "../ListPage";
import { fetchAllAssets, fetchDatabaseAssets } from "../../services/APIService";
import { AssetListDefinition } from "../../components/list/list-definitions/AssetListDefinition";
import { fetchAllDatabases } from "../../services/APIService";

export interface SearchPageViewProps {
    state: any;
    dispatch: Dispatch<ReducerAction<any>>;
}

interface SearchPageProps {}

let databases: any;

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
                        "list_tags",
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

        case "delete-item-failed":
            return {
                ...state,
                loading: false,
                error: action.error,
                notifications: [
                    {
                        type: "error",
                        header: "Failed to Delete",
                        content: action.payload.response,
                    },
                ],
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
var tags: any[] = [];
function SearchPage(props: SearchPageProps) {
    const config = Cache.getItem("config");
    const navigate = useNavigate();
    const [useNoOpenSearch] = useState(
        config.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH)
    );

    //Mapview should be used when location services are enabled and opensearch is used
    const [useMapView] = useState(
        config.featuresEnabled?.includes(featuresEnabled.LOCATIONSERVICES) && !useNoOpenSearch
    );

    const { databaseId } = useParams();

    useEffect(() => {
        fetchAllDatabases().then((res) => {
            databases = res;
        });
    }, []);

    const [state, dispatch] = useReducer(searchReducer, { ...INITIAL_STATE, databaseId });

    useEffect(() => {
        fetchTags().then((res) => {
            tags = [];
            if (res && Array.isArray(res)) {
                Object.values(res).map((x: any) => {
                    tags.push({ label: `${x.tagName} (${x.tagTypeName})`, value: x.tagName });
                });
            }
            return tags;
        });
        if (!state.initialResult && !useNoOpenSearch) {
            search({}, { dispatch, state });
        }
    }, [state]);
    const [selectedTags, setSelectedTags] = useState<OptionDefinition[]>([]);

    return (
        <>
            <Box padding={{ top: databaseId ? "s" : "m", horizontal: "l" }}>
                {databaseId && (
                    <BreadcrumbGroup
                        items={[
                            { text: Synonyms.Databases, href: "#/databases/" },
                            {
                                text: databaseId,
                                href: `#/databases/${databaseId}/assets/`,
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
                        ></Alert>
                    )}
                    {state?.notifications.length > 0 && <Flashbar items={state.notifications} />}

                    {!useNoOpenSearch && (
                        <Grid
                            gridDefinition={[
                                { colspan: { default: 6 } },
                                { colspan: { default: 6 } },
                            ]}
                        >
                            <FormField label="Keywords">
                                <Grid
                                    gridDefinition={[
                                        { colspan: { default: 10 } },
                                        { colspan: { default: 2 } },
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
                            <Box>
                                <Grid
                                    gridDefinition={[
                                        { colspan: { default: 9 } },
                                        { colspan: { default: 3 } },
                                    ]}
                                >
                                    <FormField label="Filter Types">
                                        <Grid
                                            gridDefinition={[
                                                { colspan: { default: 3 } },
                                                { colspan: { default: 3 } },
                                                { colspan: { default: 3 } },
                                                { colspan: { default: 3 } },
                                            ]}
                                        >
                                            <Select
                                                selectedOption={
                                                    state?.filters?._rectype || {
                                                        label: Synonyms.Assets,
                                                        value: "asset",
                                                    }
                                                }
                                                onChange={({ detail }) =>
                                                    // changeRectype(e.detail.selectedOption, { state, dispatch })
                                                    changeFilter(
                                                        "_rectype",
                                                        detail.selectedOption,
                                                        {
                                                            state,
                                                            dispatch,
                                                        }
                                                    )
                                                }
                                                options={[
                                                    { label: Synonyms.Assets, value: "asset" },
                                                    { label: "Files", value: "s3object" },
                                                ]}
                                                placeholder="Asset Type"
                                            />
                                            <Select
                                                selectedOption={state?.filters?.str_databaseid}
                                                placeholder="Database"
                                                options={[
                                                    { label: "All", value: "all" },
                                                    //List every database from "databases" variable and then map to result aggregation to display (doc_count) next to each
                                                    //We do this because opensearch has a max items it will return in a query which may not be everything across aggregated databases
                                                    //Without this, you wouldn't be able to search on other databases not listed due to trimmed results. 
                                                    ...(databases?.map((b: any) => {
                                                        var count = 0
                                                        //Map through result aggregation to find doc_count for each database
                                                        state?.result?.aggregations?.str_databaseid?.buckets.map(
                                                            (c: any) => {
                                                                if (c.key === b.databaseId) {
                                                                    count = c.doc_count
                                                                }
                                                            }
                                                        )
    
                                                        return {
                                                            label: `${b.databaseId} (Results: ${count} / Total: ${b.assetCount})`,
                                                            value: b.databaseId,
                                                        };
    
                                                    }) || []),
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
                                            <Select
                                                selectedOption={state?.filters?.list_tags}
                                                placeholder="Tags"
                                                options={[
                                                    { label: "All", value: "all" },
                                                    ...(state?.result?.aggregations?.list_tags?.buckets.flatMap(
                                                        (tag: any) =>
                                                            tag.key
                                                                .split(",")
                                                                .map((value: string) => ({
                                                                    label: `${value} (${tag.doc_count})`,
                                                                    value: value,
                                                                }))
                                                    ) || []),
                                                ]}
                                                onChange={({ detail }) =>
                                                    changeFilter(
                                                        "list_tags",
                                                        detail.selectedOption,
                                                        {
                                                            state,
                                                            dispatch,
                                                        }
                                                    )
                                                }
                                            />
                                        </Grid>
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
                                </Grid>
                            </Box>
                        </Grid>
                    )}

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
                    ) : !useNoOpenSearch ? (
                        <SearchPageListView state={state} dispatch={dispatch} />
                    ) : (
                        <ListPage
                            singularName={Synonyms.Asset}
                            singularNameTitleCase={Synonyms.Asset}
                            pluralName={Synonyms.assets}
                            pluralNameTitleCase={Synonyms.Assets}
                            onCreateCallback={() => {
                                if (databaseId) {
                                    navigate(`/upload/${databaseId}`);
                                } else {
                                    navigate("/upload");
                                }
                            }}
                            listDefinition={AssetListDefinition}
                            fetchAllElements={fetchAllAssets}
                            fetchElements={fetchDatabaseAssets}
                        />
                    )}
                </SpaceBetween>
            </Box>
        </>
    );
}

export default SearchPage;

import React, { Dispatch, ReducerAction, useReducer, useState } from "react";
import SearchPropertyFilter from "./SearchPropertyFilter";
import Container from "@cloudscape-design/components/container";
import { Alert, ColumnLayout, Grid, Header, SpaceBetween } from "@cloudscape-design/components";
import Synonyms from "../../synonyms";
import SearchPageSegmentedControl from "./SearchPageSegmentedControl";
import SearchPageMapView from "./SearchPageMapView";
import SearchPageListView from "./SearchPageListView";
import Box from "@cloudscape-design/components/box";

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

        case "query-update":
            return {
                ...state,
                query: { tokens: action.tokens, operation: action.operation },
                error: undefined,
                loading: true,
                sort: action.sort,
                tableSort: {},
                pagination: {
                    from: 0,
                },
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

            return {
                ...state,
                loading: false,
                result: action.result,
            };
        case "search-result-error":
            return {
                ...state,
                loading: false,
                error: action.error,
            };
        case "set-search-table-preferences":
            return {
                ...state,
                tablePreferences: action.payload,
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

        default:
            return state;
    }
}

function SearchPage(props: SearchPageProps) {
    const [useMapView, setUseMapView] = useState(false);
    const [state, dispatch] = useReducer(searchReducer, {
        query: { tokens: [], operation: "AND" },
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
        view: "listview",
    });

    return (
        <Container header={<Header variant="h2">Search {Synonyms.Assets}</Header>}>
            <SpaceBetween direction="vertical" size="l">
                {state.error && (
                    <Alert statusIconAriaLabel="Error" type="error" header={state.error.message}>
                        {state.error.stack}
                    </Alert>
                )}
                <ColumnLayout columns={2}>
                    <SearchPropertyFilter state={state} dispatch={dispatch} />
                    {useMapView && (
                        <div style={{ display: "flex", justifyContent: "flex-end" }}>
                            <SearchPageSegmentedControl
                                selectedId={state.view}
                                onchange={(selectedId: string) =>
                                    dispatch({ type: "set-view", view: selectedId })
                                }
                            />
                        </div>
                    )}
                </ColumnLayout>
                <Grid>
                    {state.view === "mapview" ? (
                        <SearchPageMapView state={state} dispatch={dispatch} />
                    ) : (
                        <Box>
                            <SearchPageListView state={state} dispatch={dispatch} />
                        </Box>
                    )}
                </Grid>
            </SpaceBetween>
        </Container>
    );
}

export default SearchPage;

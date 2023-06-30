import React, {Dispatch, ReducerAction, useReducer, useState} from "react";
import SearchPropertyFilter from "./SearchPropertyFilter";
import Container from "@cloudscape-design/components/container";
import {
    Alert,
    ColumnLayout,
    Grid,
    Header,
    SpaceBetween,
} from "@cloudscape-design/components";
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
                query: { tokens: action.tokens, operation: action.operation, },
                error: undefined,
                loading: true,
                sort: action.sort,
                tableSort: {},
                pagination: {
                    from: 0,
                }
            };
        case "search-result-update":
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
            console.log(action.payload)
            return {
                ...state,
                popupInfo: action.payload,
            };
        default:
            return state;
    }
}

function SearchPage(props: SearchPageProps) {
    const [viewSelected, setViewSelected] = useState<String>("mapview");

    const [state, dispatch] = useReducer(searchReducer, {
        query: { tokens: [], operation: "AND" },
        loading: false,
        tablePreferences: {
            pageSize: 100,
        },
        pagination: {
            from: 0,
        }
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
                    <div style={{ display: "flex", justifyContent: "flex-end" }}>
                        <SearchPageSegmentedControl
                            onchange={(selectedId: string) => setViewSelected(selectedId)}
                        />
                    </div>
                </ColumnLayout>
                <Grid>
                    {viewSelected === "mapview" ? (
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

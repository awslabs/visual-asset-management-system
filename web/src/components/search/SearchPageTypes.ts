/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Dispatch, ReducerAction } from "react";
import Synonyms from "../../synonyms";

/**
 * Props interface for search page view components
 */
export interface SearchPageViewProps {
    state: any;
    dispatch: Dispatch<ReducerAction<any>>;
}

/**
 * Initial state for search functionality
 * Used by components that need to reset to default search state
 */
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
    showPreviewThumbnails: false,
    tableSort: {}, // Initialize as empty object - no sorting by default
    selectedItems: [], // Initialize as empty array
};

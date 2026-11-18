/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Dispatch, ReducerAction } from "react";
import Synonyms from "../../synonyms";

/**
 * Why the asset list's Create, Delete Selected and Unarchive Selected actions are unavailable to the
 * signed-in role (Cloudscape `disabledReason`), from the API's route listing. An action with no
 * reason is enabled; the API still authorizes each request.
 */
export interface AssetActionDisabledReasons {
    create?: string;
    delete?: string;
    unarchive?: string;
}

/**
 * Elements in the search page's title row that a view renders its own title-row content into with
 * `createPortal`: `info` beside the h1 (the result count) and `actions` before the view switch
 * (Clear all filters). Each is null until the row has mounted.
 */
export interface SearchTitleSlots {
    info: HTMLElement | null;
    actions: HTMLElement | null;
}

/**
 * Props interface for search page view components
 */
export interface SearchPageViewProps {
    state: any;
    dispatch: Dispatch<ReducerAction<any>>;
    onShowToast?: (title: string, message: string) => void;
    onCreateAsset?: () => void;
    assetActionDisabledReasons?: AssetActionDisabledReasons;
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

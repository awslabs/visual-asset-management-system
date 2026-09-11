/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export const appLayoutLabels = {
    navigation: "Side navigation",
    navigationToggle: "Open side navigation",
    navigationClose: "Close side navigation",
    notifications: "Notifications",
    tools: "Help panel",
    toolsToggle: "Open help panel",
    toolsClose: "Close help panel",
};

export const paginationLabels = {
    nextPageLabel: "Next page",
    previousPageLabel: "Previous page",
    pageLabel: (pageNumber: any) => `Page ${pageNumber} of all pages`,
};

export const externalLinkProps = {
    external: true,
    externalIconAriaLabel: "Opens in a new tab",
};

export const distributionSelectionLabels = {
    itemSelectionLabel: (data: any, row: any) => `select ${row.id}`,
    allItemsSelectionLabel: () => "select all",
    selectionGroupLabel: "Distribution selection",
};

export const originsSelectionLabels = {
    itemSelectionLabel: (data: any, row: any) => `select ${row.name}`,
    allItemsSelectionLabel: () => "select all",
    selectionGroupLabel: "Origins selection",
};

export const behaviorsSelectionLabels = {
    itemSelectionLabel: (data: any, row: any) =>
        `select path ${row.pathPattern} from origin ${row.origin}`,
    allItemsSelectionLabel: () => "select all",
    selectionGroupLabel: "Behaviors selection",
};

export const logsSelectionLabels = {
    itemSelectionLabel: (data: any, row: any) => `select ${row.name}`,
    allItemsSelectionLabel: () => "select all",
    selectionGroupLabel: "Logs selection",
};

const headerLabel = (title: any, sorted: any, descending: any) => {
    return `${title}, ${
        sorted ? `sorted ${descending ? "descending" : "ascending"}` : "not sorted"
    }.`;
};

export const addColumnSortLabels = (columns: any) =>
    columns.map((col: any) => ({
        ariaLabel: col.sortingField
            ? (sortState: any) => headerLabel(col.header, sortState.sorted, sortState.descending)
            : undefined,
        ...col,
    }));

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Column sorting, and paging in the browser, for a Cloudscape table, through
 * `@cloudscape-design/collection-hooks` as `TableList` uses it. A sortable column carries a
 * `sortingField` (its key, which the table matches the sorted column by) and a `sortingComparator`
 * built here, so a date sorts by its value rather than its display string and a status by the
 * label it shows.
 */

import { useCollection } from "@cloudscape-design/collection-hooks";
import type { PaginationProps } from "@cloudscape-design/components/pagination";
import type { TableProps } from "@cloudscape-design/components/table";

/** Rows per page of a list paged in the browser: the page size `TableList` uses. */
export const BROWSER_PAGE_SIZE = 15;

/**
 * Said in the header of a listing the server pages, while it spans more than the page shown: a
 * column header orders the rows of that page only.
 */
export const PAGE_SORTING_NOTE = "Column sorting applies to the page shown.";

type Comparator<T> = (a: T, b: T) => number;

const isMissing = (value: unknown): boolean =>
    value === undefined || value === null || value === "" || Number.isNaN(value);

/** A row without a value sorts after the rows with one (before them in descending order). */
const compareMissing = (left: unknown, right: unknown): number | null => {
    const leftMissing = isMissing(left);
    const rightMissing = isMissing(right);
    if (leftMissing && rightMissing) return 0;
    if (leftMissing) return 1;
    if (rightMissing) return -1;
    return null;
};

/**
 * Orders rows by text, ignoring case and accents, with the numbers in it compared by value
 * ("version 2" before "version 10").
 */
export const byText =
    <T>(value: (item: T) => string | null | undefined): Comparator<T> =>
    (a, b) => {
        const left = value(a);
        const right = value(b);
        return (
            compareMissing(left, right) ??
            String(left).localeCompare(String(right), undefined, {
                numeric: true,
                sensitivity: "base",
            })
        );
    };

/** Orders rows by a timestamp's value, earliest first; a value that is not a date has none. */
export const byDate =
    <T>(value: (item: T) => string | number | null | undefined): Comparator<T> =>
    (a, b) => {
        const raw = [value(a), value(b)];
        const [left, right] = raw.map((entry) =>
            isMissing(entry) ? NaN : new Date(entry as string | number).getTime()
        );
        return compareMissing(left, right) ?? left - right;
    };

/** Orders rows by a number, smallest first. */
export const byNumber =
    <T>(value: (item: T) => number | null | undefined): Comparator<T> =>
    (a, b) => {
        const left = value(a);
        const right = value(b);
        return compareMissing(left, right) ?? (left as number) - (right as number);
    };

/** The Table props that wire its sortable headers to the sorted items. */
export type TableSortingProps<T> = Pick<
    TableProps<T>,
    "sortingColumn" | "sortingDescending" | "onSortingChange"
>;

/**
 * The rows sorted by the column header the reader chose, for a listing whose rows are the page
 * the server returned. The order holds while the reader pages or the rows are read again.
 */
export const useSortedItems = <T>(
    items: readonly T[]
): { items: readonly T[]; sortingProps: TableSortingProps<T> } => {
    const { items: sorted, collectionProps } = useCollection(items, { sorting: {} });
    return {
        items: sorted,
        sortingProps: {
            sortingColumn: collectionProps.sortingColumn,
            sortingDescending: collectionProps.sortingDescending,
            onSortingChange: collectionProps.onSortingChange,
        },
    };
};

/**
 * A whole list sorted and paged in the browser: the rows of the current page, with the Table's
 * sorting props and the Pagination's props. Sorting returns to the first page; call `toFirstPage`
 * when the list is filtered.
 */
export const useBrowserPagedItems = <T>(
    items: readonly T[],
    pageSize: number = BROWSER_PAGE_SIZE
): {
    items: readonly T[];
    sortingProps: TableSortingProps<T>;
    paginationProps: Pick<PaginationProps, "currentPageIndex" | "pagesCount" | "onChange">;
    toFirstPage: () => void;
} => {
    const {
        items: pageItems,
        collectionProps,
        paginationProps,
        actions,
    } = useCollection(items, { sorting: {}, pagination: { pageSize } });
    return {
        items: pageItems,
        sortingProps: {
            sortingColumn: collectionProps.sortingColumn,
            sortingDescending: collectionProps.sortingDescending,
            onSortingChange: collectionProps.onSortingChange,
        },
        paginationProps: {
            currentPageIndex: paginationProps.currentPageIndex,
            // An empty list still shows one page.
            pagesCount: Math.max(1, paginationProps.pagesCount),
            onChange: paginationProps.onChange,
        },
        toFirstPage: () => actions.setCurrentPage(1),
    };
};

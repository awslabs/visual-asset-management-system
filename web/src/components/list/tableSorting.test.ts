/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The comparators the list tables sort with, and the browser paging of a whole list: a date sorts
 * by its value, text ignores case and compares the numbers in it by value, a row without a value
 * sorts after the rows with one, and a sort or a filter covers the whole list before it is paged.
 */

import { act, renderHook } from "@testing-library/react";
import {
    BROWSER_PAGE_SIZE,
    byDate,
    byNumber,
    byText,
    useBrowserPagedItems,
    useSortedItems,
} from "./tableSorting";

interface Row {
    id: string;
    name?: string | null;
    at?: string | null;
    count?: number | null;
}

const ids = (rows: readonly Row[]) => rows.map((row) => row.id);

describe("comparators", () => {
    it("orders dates by their value, not by how they are displayed", () => {
        const rows: Row[] = [
            { id: "oct", at: "2026-10-01T00:00:00Z" },
            { id: "feb", at: "2026-02-01T00:00:00Z" },
            { id: "sep", at: "2026-09-15T00:00:00Z" },
        ];
        // Displayed as 10/1/2026, 2/1/2026, 9/15/2026, whose text order is oct, feb, sep.
        expect(ids([...rows].sort(byDate((row) => row.at)))).toEqual(["feb", "sep", "oct"]);
    });

    it("sorts a row without a value after the others, a date that does not parse included", () => {
        const rows: Row[] = [
            { id: "none", at: null },
            { id: "bad", at: "not a date" },
            { id: "feb", at: "2026-02-01T00:00:00Z" },
        ];
        expect(ids([...rows].sort(byDate((row) => row.at)))).toEqual(["feb", "none", "bad"]);
    });

    it("orders text ignoring case, with the numbers in it by value", () => {
        const rows: Row[] = [
            { id: "b", name: "bravo" },
            { id: "a", name: "Alpha" },
            { id: "v10", name: "version 10" },
            { id: "v2", name: "version 2" },
            { id: "empty", name: "" },
        ];
        expect(ids([...rows].sort(byText((row) => row.name)))).toEqual([
            "a",
            "b",
            "v2",
            "v10",
            "empty",
        ]);
    });

    it("orders numbers by value", () => {
        const rows: Row[] = [
            { id: "ten", count: 10 },
            { id: "none", count: null },
            { id: "two", count: 2 },
        ];
        expect(ids([...rows].sort(byNumber((row) => row.count)))).toEqual(["two", "ten", "none"]);
    });
});

const nameColumn = { sortingField: "name", sortingComparator: byText((row: Row) => row.name) };

describe("useSortedItems", () => {
    it("keeps the rows' order until a column is sorted, then sorts both ways", () => {
        const rows: Row[] = [
            { id: "b", name: "bravo" },
            { id: "a", name: "Alpha" },
            { id: "c", name: "Charlie" },
        ];
        const { result } = renderHook(() => useSortedItems(rows));
        expect(ids(result.current.items)).toEqual(["b", "a", "c"]);

        act(() =>
            result.current.sortingProps.onSortingChange!(
                new CustomEvent("sort", {
                    detail: { sortingColumn: nameColumn, isDescending: false },
                }) as any
            )
        );
        expect(ids(result.current.items)).toEqual(["a", "b", "c"]);
        expect(result.current.sortingProps.sortingColumn).toBe(nameColumn);

        act(() =>
            result.current.sortingProps.onSortingChange!(
                new CustomEvent("sort", {
                    detail: { sortingColumn: nameColumn, isDescending: true },
                }) as any
            )
        );
        expect(ids(result.current.items)).toEqual(["c", "b", "a"]);
        expect(result.current.sortingProps.sortingDescending).toBe(true);
    });

    it("clearSorting returns the rows to their own order and leaves no column sorted", () => {
        const rows: Row[] = [
            { id: "b", name: "bravo" },
            { id: "a", name: "Alpha" },
            { id: "c", name: "Charlie" },
        ];
        const { result } = renderHook(() => useSortedItems(rows));
        act(() =>
            result.current.sortingProps.onSortingChange!(
                new CustomEvent("sort", {
                    detail: { sortingColumn: nameColumn, isDescending: true },
                }) as any
            )
        );
        expect(ids(result.current.items)).toEqual(["c", "b", "a"]);

        act(() => result.current.clearSorting());
        expect(ids(result.current.items)).toEqual(["b", "a", "c"]);
        expect(result.current.sortingProps.sortingColumn).toBeUndefined();
    });
});

describe("useBrowserPagedItems", () => {
    const many: Row[] = Array.from({ length: BROWSER_PAGE_SIZE + 5 }, (_, index) => ({
        id: `row-${String(index + 1).padStart(2, "0")}`,
        name: `row ${index + 1}`,
    }));

    it("pages the list at the TableList page size", () => {
        expect(BROWSER_PAGE_SIZE).toBe(15);
        const { result } = renderHook(() => useBrowserPagedItems(many));
        expect(result.current.items).toHaveLength(BROWSER_PAGE_SIZE);
        expect(result.current.paginationProps.pagesCount).toBe(2);
        act(() =>
            result.current.paginationProps.onChange!(
                new CustomEvent("page", { detail: { currentPageIndex: 2 } }) as any
            )
        );
        expect(ids(result.current.items)).toEqual(ids(many.slice(BROWSER_PAGE_SIZE)));
    });

    it("sorts the whole list, then shows its first page", () => {
        const { result } = renderHook(() => useBrowserPagedItems(many));
        act(() =>
            result.current.paginationProps.onChange!(
                new CustomEvent("page", { detail: { currentPageIndex: 2 } }) as any
            )
        );
        act(() =>
            result.current.sortingProps.onSortingChange!(
                new CustomEvent("sort", {
                    detail: { sortingColumn: nameColumn, isDescending: true },
                }) as any
            )
        );
        expect(result.current.paginationProps.currentPageIndex).toBe(1);
        // The last row of the list, which was on page 2, now leads page 1.
        expect(result.current.items[0].id).toBe("row-20");
    });

    it("returns to the first page when asked, as a filter change does", () => {
        const { result } = renderHook(() => useBrowserPagedItems(many));
        act(() =>
            result.current.paginationProps.onChange!(
                new CustomEvent("page", { detail: { currentPageIndex: 2 } }) as any
            )
        );
        act(() => result.current.toFirstPage());
        expect(result.current.paginationProps.currentPageIndex).toBe(1);
    });

    it("shows one page for an empty list", () => {
        const { result } = renderHook(() => useBrowserPagedItems<Row>([]));
        expect(result.current.items).toEqual([]);
        expect(result.current.paginationProps.pagesCount).toBe(1);
    });
});

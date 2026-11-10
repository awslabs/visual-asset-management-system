/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Clear all filters has something to clear only when the user changed the search from the page's
 * defaults (#1076): a query, a filter or a metadata filter row. The record type is the page's mode,
 * a database route's locked database is the page's scope, and the result explanation is a display
 * preference, so none of them counts.
 */
import { renderHook, act } from "@testing-library/react";
import { useSearchState } from "./useSearchState";

const assetType = { label: "glb", value: "glb", values: ["glb"] };

describe("useSearchState hasActiveFilters (#1076)", () => {
    it("is false on a fresh load, which holds only the record type", () => {
        const { result } = renderHook(() => useSearchState());
        expect(result.current.filters._rectype).toBeDefined();
        expect(result.current.hasActiveFilters()).toBe(false);
    });

    it("is false after a record type change: Clear all filters keeps the record type", () => {
        const { result } = renderHook(() => useSearchState());
        act(() => result.current.updateFilter("_rectype", { label: "Files", value: "file" }));
        expect(result.current.hasActiveFilters()).toBe(false);
    });

    it("is true after a filter, and false again after Clear all filters", () => {
        const { result } = renderHook(() => useSearchState());
        act(() => result.current.updateFilter("str_assettype", assetType));
        expect(result.current.hasActiveFilters()).toBe(true);

        act(() => result.current.clearSearch());
        expect(result.current.hasActiveFilters()).toBe(false);
        expect(Object.keys(result.current.filters)).toEqual(["_rectype"]);
    });

    it("counts a query and a metadata filter row", () => {
        const { result } = renderHook(() => useSearchState());
        act(() => result.current.setQuery("  truck "));
        expect(result.current.hasActiveFilters()).toBe(true);
        act(() => result.current.setQuery("   "));
        expect(result.current.hasActiveFilters()).toBe(false);

        act(() =>
            result.current.addMetadataFilter({ key: "", value: "", operator: "=", type: "string" })
        );
        expect(result.current.hasActiveFilters()).toBe(true);
    });

    it("does not count a database route's locked database, and Clear all filters keeps it", () => {
        const { result } = renderHook(() => useSearchState(undefined, "db1"));
        expect(result.current.filters.str_databaseid?.value).toBe("db1");
        expect(result.current.hasActiveFilters({ databaseLocked: true })).toBe(false);

        act(() => result.current.updateFilter("str_assettype", assetType));
        expect(result.current.hasActiveFilters({ databaseLocked: true })).toBe(true);

        act(() => result.current.clearSearch(["str_databaseid"]));
        expect(result.current.hasActiveFilters({ databaseLocked: true })).toBe(false);
        expect(result.current.filters.str_databaseid).toEqual({ label: "db1", value: "db1" });
        expect(result.current.filters.str_assettype).toBeUndefined();
    });

    it("counts a database the user chose on the all-databases page", () => {
        const { result } = renderHook(() => useSearchState());
        act(() =>
            result.current.updateFilter("str_databaseid", {
                label: "db1",
                value: "db1",
                values: ["db1"],
            })
        );
        expect(result.current.hasActiveFilters({ databaseLocked: false })).toBe(true);
    });

    it("counts a search option only once the user clears it from its default", () => {
        const { result } = renderHook(() => useSearchState());
        act(() => result.current.updateFilter("includeMetadataInKeywordSearch", false));
        expect(result.current.hasActiveFilters()).toBe(true);
        act(() => result.current.updateFilter("includeMetadataInKeywordSearch", true));
        expect(result.current.hasActiveFilters()).toBe(false);

        act(() => result.current.updateFilter("includeSegments", false));
        expect(result.current.hasActiveFilters()).toBe(true);
        act(() => result.current.updateFilter("includeSegments", true));
        expect(result.current.hasActiveFilters()).toBe(false);
    });

    it("does not count the result explanation preference", () => {
        const { result } = renderHook(() => useSearchState());
        act(() => result.current.updateFilter("showResultExplanation", true));
        expect(result.current.hasActiveFilters()).toBe(false);
    });

    it("counts a date or size filter the user turned on before a value is entered", () => {
        const { result } = renderHook(() => useSearchState());
        act(() =>
            result.current.updateFilter("date_lastmodified_filter", { operator: ">", value: "" })
        );
        expect(result.current.hasActiveFilters()).toBe(true);
        act(() => result.current.updateFilter("date_lastmodified_filter", null));
        expect(result.current.hasActiveFilters()).toBe(false);
    });

    it("counts a relationship filter set to False, and not a select left at All", () => {
        const { result } = renderHook(() => useSearchState());
        act(() => result.current.updateFilter("bool_has_asset_children", { value: false }));
        expect(result.current.hasActiveFilters()).toBe(true);
        act(() => result.current.updateFilter("bool_has_asset_children", null));
        act(() => result.current.updateFilter("str_assettype", { label: "All", value: "all" }));
        expect(result.current.hasActiveFilters()).toBe(false);
    });
});

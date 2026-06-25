/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { renderHook, act } from "@testing-library/react-hooks";
import { useSearchState } from "./useSearchState";

const fileA = { filename: "a.glb", key: "x/a.glb", isDirectory: false, assetId: "x", databaseId: "d" };
const fileB = { filename: "b.png", key: "x/b.png", isDirectory: false, assetId: "x", databaseId: "d" };

describe("useSearchState viewer-selection slice", () => {
    it("starts not in viewer-select mode with an empty selection", () => {
        const { result } = renderHook(() => useSearchState());
        expect(result.current.viewerSelectMode).toBe(false);
        expect(result.current.viewerSelection).toEqual([]);
    });

    it("enters/exits mode and exit clears the selection", () => {
        const { result } = renderHook(() => useSearchState());
        act(() => result.current.enterViewerSelectMode());
        expect(result.current.viewerSelectMode).toBe(true);
        act(() => result.current.addToViewerSelection([fileA as any]));
        expect(result.current.viewerSelection).toHaveLength(1);
        act(() => result.current.exitViewerSelectMode());
        expect(result.current.viewerSelectMode).toBe(false);
        expect(result.current.viewerSelection).toEqual([]);
    });

    it("dedups additions by key", () => {
        const { result } = renderHook(() => useSearchState());
        act(() => result.current.addToViewerSelection([fileA as any, fileB as any]));
        act(() => result.current.addToViewerSelection([fileA as any])); // duplicate
        expect(result.current.viewerSelection.map((f) => f.key)).toEqual(["x/a.glb", "x/b.png"]);
    });

    it("PERSISTS the viewer selection across a new search result (setResult)", () => {
        const { result } = renderHook(() => useSearchState());
        act(() => result.current.addToViewerSelection([fileA as any]));
        act(() => result.current.setResult({ hits: { hits: [], total: { value: 0 } } } as any));
        // selectedItems is cleared by SET_RESULT, but viewerSelection must survive:
        expect(result.current.selectedItems).toEqual([]);
        expect(result.current.viewerSelection.map((f) => f.key)).toEqual(["x/a.glb"]);
    });

    it("clearViewerSelection empties the running set but stays in mode", () => {
        const { result } = renderHook(() => useSearchState());
        act(() => result.current.enterViewerSelectMode());
        act(() => result.current.addToViewerSelection([fileA as any]));
        act(() => result.current.clearViewerSelection());
        expect(result.current.viewerSelection).toEqual([]);
        expect(result.current.viewerSelectMode).toBe(true);
    });
});

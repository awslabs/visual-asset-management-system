/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { fileManagerReducer } from "./FileManagerReducer";
import type { FileManagerState, FileTree } from "../types/FileManagerTypes";

function node(partial: Partial<FileTree>): FileTree {
    return {
        name: "",
        displayName: "",
        relativePath: "/",
        keyPrefix: "",
        level: 0,
        expanded: true,
        subTree: [],
        isFolder: false,
        ...partial,
    } as FileTree;
}

/** A listing whose root is still the placeholder name, with one file selected. */
function stateWithSelection(): { state: FileManagerState; file: FileTree } {
    const file = node({
        name: "p1.png",
        displayName: "p1.png",
        relativePath: "/img/p1.png",
        keyPrefix: "asset/img/p1.png",
        level: 2,
        expanded: false,
    });
    const img = node({
        name: "img",
        displayName: "img",
        relativePath: "/img/",
        keyPrefix: "asset/img/",
        level: 1,
        isFolder: true,
        subTree: [file],
    });
    const root = node({
        name: "Loading...",
        displayName: "Loading...",
        isFolder: true,
        subTree: [img],
    });
    const state = {
        fileTree: root,
        unfilteredFileTree: root,
        flattenedItems: [root, img, file],
        selectedItem: file,
        selectedItems: [file],
        selectedItemPath: file.relativePath,
        selectedItemPaths: [file.relativePath],
        multiSelectMode: false,
        lastSelectedIndex: 2,
        loading: false,
        error: null,
    } as unknown as FileManagerState;
    return { state, file };
}

describe("fileManagerReducer SET_TREE_NAME", () => {
    it("renames the root of both trees without touching the selection", () => {
        const { state, file } = stateWithSelection();

        const next = fileManagerReducer(state, { type: "SET_TREE_NAME", payload: "mx-asset-a" });

        expect(next.fileTree.name).toBe("mx-asset-a");
        expect(next.fileTree.displayName).toBe("mx-asset-a");
        expect(next.unfilteredFileTree.name).toBe("mx-asset-a");
        // The selection is the same object, not a re-resolved copy, so the details toolbar keeps
        // its component instance (and whatever menu or dialog is open from it).
        expect(next.selectedItem).toBe(file);
        expect(next.selectedItems).toEqual([file]);
        expect(next.selectedItemPath).toBe("/img/p1.png");
        expect(next.multiSelectMode).toBe(false);
        // The flattened rows follow the renamed root.
        expect(next.flattenedItems[0].name).toBe("mx-asset-a");
        expect(next.flattenedItems.map((n) => n.relativePath)).toEqual([
            "/",
            "/img/",
            "/img/p1.png",
        ]);
    });

    it("is a no-op when the root already carries the name", () => {
        const { state } = stateWithSelection();
        const named = fileManagerReducer(state, { type: "SET_TREE_NAME", payload: "mx-asset-a" });

        const again = fileManagerReducer(named, { type: "SET_TREE_NAME", payload: "mx-asset-a" });

        expect(again.fileTree).toBe(named.fileTree);
        expect(again.selectedItem).toBe(named.selectedItem);
    });

    it("contrast: FETCH_SUCCESS is the refresh path and still resets the selection", () => {
        const { state } = stateWithSelection();

        const next = fileManagerReducer(state, { type: "FETCH_SUCCESS", payload: state.fileTree });

        expect(next.selectedItem).toBeNull();
        expect(next.selectedItems).toEqual([]);
    });
});

describe("fileManagerReducer selection across a page merge (#1125 D32)", () => {
    // A tree still loading merges each page as it arrives (MERGE_FILES), which re-resolves the
    // selection by `selectedItemPaths`. A Ctrl- or Shift-click selection must be in those paths, or
    // the next page drops every item but the one first clicked.
    const merge = (state: FileManagerState) =>
        fileManagerReducer(state, {
            type: "MERGE_FILES",
            payload: { files: [], loadingPhase: "basic-loading" },
        } as any);

    it("keeps a Ctrl-click addition when the next page merges", () => {
        const { state } = stateWithSelection();
        const [root, , file] = state.flattenedItems;
        let next = fileManagerReducer(state, {
            type: "SELECT_ITEM",
            payload: { item: root, ctrlKey: false, shiftKey: false },
        } as any);
        next = fileManagerReducer(next, {
            type: "SELECT_ITEM",
            payload: { item: file, ctrlKey: true, shiftKey: false },
        } as any);
        expect(next.selectedItemPaths).toEqual(["/", "/img/p1.png"]);

        const merged = merge(next);

        expect(merged.selectedItems.map((i) => i.relativePath)).toEqual(["/", "/img/p1.png"]);
    });

    it("keeps a Shift-click range when the next page merges", () => {
        const { state } = stateWithSelection();
        const [root, , file] = state.flattenedItems;
        let next = fileManagerReducer(state, {
            type: "SELECT_ITEM",
            payload: { item: root, ctrlKey: false, shiftKey: false },
        } as any);
        next = fileManagerReducer(next, {
            type: "SELECT_ITEM",
            payload: { item: file, ctrlKey: false, shiftKey: true },
        } as any);

        const merged = merge(next);

        expect(merged.selectedItems.map((i) => i.relativePath)).toEqual([
            "/",
            "/img/",
            "/img/p1.png",
        ]);
    });

    it("a Ctrl-click removal stays removed when the next page merges", () => {
        const { state } = stateWithSelection();
        const [root, , file] = state.flattenedItems;
        let next = fileManagerReducer(state, {
            type: "SELECT_ITEM",
            payload: { item: root, ctrlKey: false, shiftKey: false },
        } as any);
        next = fileManagerReducer(next, {
            type: "SELECT_ITEM",
            payload: { item: file, ctrlKey: true, shiftKey: false },
        } as any);
        next = fileManagerReducer(next, {
            type: "SELECT_ITEM",
            payload: { item: root, ctrlKey: true, shiftKey: false },
        } as any);

        const merged = merge(next);

        expect(merged.selectedItems.map((i) => i.relativePath)).toEqual(["/img/p1.png"]);
    });
});

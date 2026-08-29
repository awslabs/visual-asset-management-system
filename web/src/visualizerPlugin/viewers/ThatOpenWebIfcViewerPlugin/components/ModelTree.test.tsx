/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A production IFC model routinely puts tens of thousands of elements in a single
 * category, so expanding one must not commit a row per element, and computing a
 * row's selected state must not walk the category on every render. The rows are
 * also the viewer's primary navigation, so they have to be reachable by keyboard
 * and have names that are not an emoji.
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import ModelTree from "./ModelTree";
import { SpatialNode } from "../types";

const HUGE = 50000;

const instance = {
    fragments: { core: { update: jest.fn().mockResolvedValue(undefined) } },
    model: {
        setVisible: jest.fn().mockResolvedValue(undefined),
        resetVisible: jest.fn().mockResolvedValue(undefined),
    },
} as any;

const hugeTree = (): SpatialNode => ({
    localId: null,
    name: "Categories",
    visible: true,
    children: [
        {
            localId: null,
            name: "Wall",
            visible: true,
            children: Array.from({ length: HUGE }, (_, index) => ({
                localId: index + 1,
                name: `#${index + 1}`,
                children: [],
                visible: true,
            })),
        },
    ],
});

const renderTree = (onSelect = jest.fn(), selected: number[] = []) => {
    const utils = render(
        <ModelTree
            instance={instance}
            tree={hugeTree()}
            selectedLocalIds={selected}
            onSelectLocalIds={onSelect}
        />
    );
    return { ...utils, onSelect };
};

const rows = () => screen.getAllByRole("treeitem");

describe("ModelTree", () => {
    beforeEach(() => jest.clearAllMocks());

    it("renders one row per category while collapsed", () => {
        renderTree();
        expect(rows()).toHaveLength(1);
        expect(screen.getByRole("tree")).toBeInTheDocument();
    });

    it("bounds the rows committed when a 50,000-element category is expanded", () => {
        renderTree();
        const group = rows()[0];

        fireEvent.keyDown(group, { key: "ArrowRight" });

        const committed = rows().length;
        // Expanded (more than the single category row) but nowhere near one row
        // per element — the un-windowed tree committed HUGE + 1 here.
        expect(committed).toBeGreaterThan(1);
        expect(committed).toBeLessThan(300);
        expect(committed).toBeLessThan(HUGE);
        expect(screen.getByRole("button", { name: /show 200 more of/i })).toBeInTheDocument();
    });

    it("reveals another page on demand", () => {
        renderTree();
        fireEvent.keyDown(rows()[0], { key: "ArrowRight" });
        const firstPage = rows().length;

        fireEvent.click(screen.getByRole("button", { name: /show 200 more of/i }));

        expect(rows().length).toBe(firstPage + 200);
    });

    it("is reachable by keyboard: the tree owns a single tab stop", () => {
        renderTree();
        expect(rows()[0]).toHaveAttribute("tabindex", "0");

        fireEvent.keyDown(rows()[0], { key: "ArrowRight" });

        // Every other row is removed from the tab order (roving tabindex), so Tab
        // does not have to walk 200 rows to leave the tree.
        const tabbable = rows().filter((row) => row.getAttribute("tabindex") === "0");
        expect(tabbable).toHaveLength(1);
        expect(rows()[0]).toHaveAttribute("aria-expanded", "true");
    });

    it("selects a category's elements from the keyboard", () => {
        const { onSelect } = renderTree();

        fireEvent.keyDown(rows()[0], { key: "Enter" });

        expect(onSelect).toHaveBeenCalledTimes(1);
        const ids = onSelect.mock.calls[0][0];
        expect(ids).toHaveLength(HUGE);
        expect(ids[0]).toBe(1);
    });

    it("names the per-row actions instead of leaving an emoji as the name", () => {
        renderTree();
        expect(screen.getByRole("button", { name: /isolate wall/i })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /^hide wall$/i })).toBeInTheDocument();
        // Positive control: the emoji is hidden from the accessible name, so a
        // lookup by it must not resolve.
        expect(screen.queryByRole("button", { name: "🎯" })).toBeNull();
        expect(screen.queryByRole("button", { name: "👁" })).toBeNull();
    });

    it("marks a category selected without walking it per rendered row", () => {
        // The group row highlights because one of its children is selected. This
        // is the value that used to cost a full recursive walk per row.
        renderTree(jest.fn(), [42]);
        expect(rows()[0]).toHaveAttribute("aria-selected", "true");
    });

    it("does not mark a category whose elements are not selected", () => {
        // Positive control for the assertion above.
        renderTree(jest.fn(), [HUGE + 1]);
        expect(rows()[0]).toHaveAttribute("aria-selected", "false");
    });
});

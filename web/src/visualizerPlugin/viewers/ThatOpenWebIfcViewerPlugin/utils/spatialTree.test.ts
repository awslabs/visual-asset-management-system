/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

// Pure-function tests (no DOM) — run on the lightweight `node` jest environment
// instead of jsdom, keeping them self-contained with no core test dependency.

import { buildTreeFromGroups, flattenModelIdMap, prettifyCategory } from "./spatialTree";

describe("buildTreeFromGroups", () => {
    it("builds parent nodes with element children from a grouping map", () => {
        const groups = {
            "Level 1": [10, 11],
            "Level 2": [20],
        };
        const tree = buildTreeFromGroups("Storeys", groups);
        expect(tree.name).toBe("Storeys");
        expect(tree.children).toHaveLength(2);
        const level1 = tree.children.find((c) => c.name === "Level 1");
        expect(level1).toBeDefined();
        expect(level1?.children.map((c) => c.localId)).toEqual([10, 11]);
        expect(level1?.visible).toBe(true);
    });

    it("produces an empty root when given no groups", () => {
        const tree = buildTreeFromGroups("Storeys", {});
        expect(tree.children).toHaveLength(0);
    });

    it("sets localId null on group nodes and the root", () => {
        const tree = buildTreeFromGroups("Categories", { IfcWall: [1] });
        expect(tree.localId).toBeNull();
        expect(tree.children[0].localId).toBeNull();
        expect(tree.children[0].children[0].localId).toBe(1);
    });
});

describe("flattenModelIdMap", () => {
    it("collapses a single model's Set of local ids into an array", () => {
        const map = { "model-a": new Set([3, 1, 2]) };
        expect(flattenModelIdMap(map).sort((a, b) => a - b)).toEqual([1, 2, 3]);
    });

    it("merges and de-duplicates local ids across multiple models", () => {
        const map = {
            "model-a": new Set([1, 2]),
            "model-b": new Set([2, 3]),
        };
        expect(flattenModelIdMap(map).sort((a, b) => a - b)).toEqual([1, 2, 3]);
    });

    it("returns an empty array for an empty or nullish map", () => {
        expect(flattenModelIdMap({})).toEqual([]);
        // @ts-expect-error — exercising the defensive nullish guard at runtime
        expect(flattenModelIdMap(undefined)).toEqual([]);
    });
});

describe("prettifyCategory", () => {
    it("splits all-caps IFC names using the word-stem dictionary", () => {
        expect(prettifyCategory("IFCWALLSTANDARDCASE")).toBe("Wall Standard Case");
        expect(prettifyCategory("IFCWALL")).toBe("Wall");
        expect(prettifyCategory("IFCBUILDINGELEMENTPROXY")).toBe("Building Element Proxy");
    });

    it("splits camelCase IFC names at case boundaries", () => {
        expect(prettifyCategory("IfcWallStandardCase")).toBe("Wall Standard Case");
        expect(prettifyCategory("IfcBuildingElementProxy")).toBe("Building Element Proxy");
    });

    it("handles names without an IFC prefix", () => {
        expect(prettifyCategory("Door")).toBe("Door");
    });

    it("falls back to a single title-cased word for unknown all-caps stems", () => {
        expect(prettifyCategory("IFCFOOBAR")).toBe("Foobar");
    });
});

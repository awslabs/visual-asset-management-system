/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { SpatialNode } from "../types";

// Builds the Model Tree shown in the panel.
//
// Pure layer (unit tested): buildTreeFromGroups() turns a {groupName: localId[]}
// map into a SpatialNode tree; flattenModelIdMap() collapses a That Open
// ModelIdMap (Record<modelId, Set<localId>>) into a flat localId[];
// prettifyCategory() turns "IFCWALLSTANDARDCASE" into "Wall standard case".
//
// Engine layer (verified in-app): buildSpatialTree() reads the loaded Fragments
// model's categories directly (model.getCategories + getItemsOfCategories) and
// delegates to the pure layer. (@thatopen/components 3.4.x dropped the older
// IfcRelationsIndexer/Classifier grouping API.)

/**
 * Converts a grouping map ({ groupLabel: localIds[] }) into a SpatialNode tree
 * rooted at a synthetic node labeled `rootName`.
 */
export function buildTreeFromGroups(
    rootName: string,
    groups: Record<string, number[]>
): SpatialNode {
    const children: SpatialNode[] = Object.keys(groups).map((groupName) => ({
        localId: null,
        name: groupName,
        children: groups[groupName].map((localId) => ({
            localId,
            name: `#${localId}`,
            children: [],
            visible: true,
        })),
        visible: true,
    }));

    return {
        localId: null,
        name: rootName,
        children,
        visible: true,
    };
}

/**
 * Collapses a That Open `ModelIdMap` (`Record<modelId, Set<localId>>`) into a
 * flat, de-duplicated list of local ids across all models. Exported for unit
 * testing; the runtime shape is plain Sets keyed by model id.
 */
export function flattenModelIdMap(modelIdMap: Record<string, Set<number>>): number[] {
    const ids = new Set<number>();
    for (const key of Object.keys(modelIdMap || {})) {
        const set = modelIdMap[key];
        if (set && typeof (set as any).forEach === "function") {
            set.forEach((id: number) => ids.add(id));
        }
    }
    return Array.from(ids);
}

/**
 * Turns an IFC category name into a friendlier label:
 *   - "IfcWallStandardCase" → "Wall Standard Case" (camelCase: split on boundaries)
 *   - "IFCWALLSTANDARDCASE"  → "Wall Standard Case" (all-caps: greedy dictionary split)
 *   - "Door"                 → "Door"
 *
 * web-ifc category names can arrive either camelCased (from the model) or fully
 * upper-cased (from the IFC schema). camelCase splits cleanly on case
 * transitions. All-caps names have no case boundaries, so we segment them with a
 * small dictionary of common IFC word stems, falling back to a single
 * title-cased word when nothing matches (still readable).
 */
export function prettifyCategory(category: string): string {
    const trimmed = category.replace(/^IFC/i, "");
    if (!trimmed) return category;

    const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();

    // Mixed-case input: split on lower→upper and acronym→Word boundaries.
    if (/[a-z]/.test(trimmed)) {
        const spaced = trimmed
            .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
            .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2");
        return spaced
            .split(" ")
            .map((w) => titleCase(w))
            .join(" ");
    }

    // All-caps input: greedily peel known IFC word stems from the front.
    const STEMS = [
        "BUILDING",
        "ELEMENT",
        "STANDARD",
        "CASE",
        "PROXY",
        "COMPONENT",
        "COVERING",
        "CURTAIN",
        "DISTRIBUTION",
        "FURNISHING",
        "MEMBER",
        "RAILING",
        "FLOW",
        "SEGMENT",
        "FITTING",
        "TERMINAL",
        "STRUCTURAL",
        "SYSTEM",
        "SPACE",
        "ASSEMBLY",
        "PLATE",
        "WALL",
        "SLAB",
        "BEAM",
        "COLUMN",
        "DOOR",
        "WINDOW",
        "ROOF",
        "STAIR",
        "RAMP",
        "PIPE",
        "DUCT",
        "FOOTING",
        "PILE",
        "TYPE",
        "ANNOTATION",
        "OPENING",
        "SITE",
        "STOREY",
    ];
    const words: string[] = [];
    let rest = trimmed;
    while (rest.length > 0) {
        const match = STEMS.find((stem) => rest.startsWith(stem));
        if (match) {
            words.push(titleCase(match));
            rest = rest.slice(match.length);
        } else {
            // No known stem at the front — take the remainder as one word.
            words.push(titleCase(rest));
            break;
        }
    }
    return words.join(" ");
}

/**
 * Builds the Model Tree by grouping a loaded model's elements by IFC category,
 * read directly from the Fragments model (the same source the 3D view uses).
 *
 * `@thatopen/components` 3.4.x no longer ships `IfcRelationsIndexer`/Classifier
 * spatial grouping in the shape earlier versions did, so we use the
 * FragmentsModel API instead: `getCategories()` lists every category present,
 * and `getItemsOfCategories([/^IFCWALL$/, ...])` returns `{ category: localId[] }`.
 * This is robust and matches what the renderer actually contains.
 *
 * @param bundle window.ThatOpenWebIfcBundle (unused now, kept for signature parity)
 * @param components OBC.Components instance (unused now, kept for signature parity)
 * @param model the loaded Fragments model
 */
export async function buildSpatialTree(
    _bundle: any,
    _components: any,
    model: any
): Promise<SpatialNode> {
    const groups: Record<string, number[]> = {};

    try {
        const categories: string[] = await model.getCategories();
        if (Array.isArray(categories) && categories.length > 0) {
            // Build one exact-match RegExp per category and fetch their items in
            // a single call: returns { [category]: number[] }.
            const regexes = categories.map(
                (c) => new RegExp(`^${c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`)
            );
            const byCategory: Record<string, number[]> = await model.getItemsOfCategories(regexes);
            for (const category of Object.keys(byCategory)) {
                const ids = byCategory[category];
                if (Array.isArray(ids) && ids.length > 0) {
                    groups[prettifyCategory(category)] = ids;
                }
            }
        }
    } catch (err) {
        console.warn("ThatOpenWebIfc: failed to read model categories:", err);
    }

    return buildTreeFromGroups("Categories", groups);
}

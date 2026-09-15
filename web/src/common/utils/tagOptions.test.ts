/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { buildTagOptionGroups, isGlobalScope, scopeLabel, GLOBAL_SCOPE } from "./tagOptions";

describe("scope helpers", () => {
    it("treats an absent or sentinel databaseId as global", () => {
        expect(isGlobalScope(undefined)).toBe(true);
        expect(isGlobalScope(null)).toBe(true);
        expect(isGlobalScope("")).toBe(true);
        expect(isGlobalScope(GLOBAL_SCOPE)).toBe(true);
        expect(isGlobalScope("factory-db")).toBe(false);
    });

    it("writes the shared scope with the sentinel's own capitalization", () => {
        // The rest of the site writes it GLOBAL; a lower-case rendering would read as a database name.
        expect(scopeLabel(undefined)).toBe("GLOBAL");
        expect(scopeLabel("GLOBAL")).toBe("GLOBAL");
        expect(scopeLabel("factory-db")).toBe("factory-db");
    });
});

describe("buildTagOptionGroups", () => {
    const tagTypes = [
        { tagTypeName: "Lifecycle", required: "False" }, // global (no databaseId)
        { tagTypeName: "Line", required: "True", databaseId: "factory-db" },
    ];
    const tags = [
        { tagName: "zeta", tagTypeName: "Lifecycle" },
        { tagName: "alpha", tagTypeName: "Lifecycle" },
        { tagName: "press", tagTypeName: "Line", databaseId: "factory-db" },
        { tagName: "assembly", tagTypeName: "Line", databaseId: "factory-db" },
    ];

    it("labels every group and option with its scope", () => {
        const groups = buildTagOptionGroups(tags, tagTypes);

        expect(groups.map((g) => g.label)).toEqual([
            "Lifecycle (GLOBAL)",
            "Line (factory-db) [required]",
        ]);
        expect(groups[1].options.map((o) => o.label)).toEqual([
            "assembly (factory-db)",
            "press (factory-db)",
        ]);
    });

    it("orders global before database-scoped, then alphabetically", () => {
        const mixed = [
            { tagName: "beta", tagTypeName: "Shared" },
            { tagName: "alpha", tagTypeName: "Shared", databaseId: "factory-db" },
            { tagName: "gamma", tagTypeName: "Shared" },
        ];
        const groups = buildTagOptionGroups(mixed, [{ tagTypeName: "Shared", required: "False" }]);

        // One name used in two scopes is TWO groups, GLOBAL first — the scopes are separate
        // vocabularies and merging them would hide one behind the other.
        expect(groups.map((g) => g.label)).toEqual(["Shared (GLOBAL)", "Shared (factory-db)"]);
        expect(groups[0].options.map((o) => o.value)).toEqual(["beta", "gamma"]);
        expect(groups[1].options.map((o) => o.value)).toEqual(["alpha"]);
    });

    it("keeps the bare tag name as the option value", () => {
        // The asset stores bare names; decorating the value would change what is submitted.
        const groups = buildTagOptionGroups(tags, tagTypes);
        const values = groups.flatMap((g) => g.options.map((o) => o.value));
        expect(values).toEqual(expect.arrayContaining(["alpha", "zeta", "assembly", "press"]));
        expect(values.some((v) => v.includes("("))).toBe(false);
    });

    it("marks a required tag type and leaves an optional one unmarked", () => {
        const groups = buildTagOptionGroups(tags, tagTypes);
        expect(groups[0].label).not.toContain("[required]");
        expect(groups[1].label).toContain("[required]");
    });

    it("groups tags whose type is unknown under Uncategorized as global", () => {
        const groups = buildTagOptionGroups([{ tagName: "loose" }], []);
        expect(groups[0].label).toBe("Uncategorized (GLOBAL)");
    });

    it("returns nothing for an empty tag list", () => {
        expect(buildTagOptionGroups([], tagTypes)).toEqual([]);
        expect(buildTagOptionGroups(undefined as any, undefined as any)).toEqual([]);
    });

    it("labels a group with its tags' scope, not a stale cached tag-type record", () => {
        // The cached tag-type list predates per-database namespacing and often has no databaseId, which
        // made a database-scoped tag type render as GLOBAL on the asset forms. A tag's type must live in
        // the tag's own scope, so the tags decide.
        const groups = buildTagOptionGroups(
            [{ tagName: "local", tagTypeName: "Line", databaseId: "factory-db" }],
            [{ tagTypeName: "Line", required: "False" }] // cached record carries no scope
        );

        expect(groups[0].label).toBe("Line (factory-db)");
        expect(groups[0].options[0].label).toBe("local (factory-db)");
    });

    it("keeps a name that exists in both scopes as two separate groups", () => {
        // Creating a GLOBAL entry over a name a database already uses is allowed (with a warning), so
        // the picker legitimately holds both. Each keeps its own required flag and its own tags.
        const groups = buildTagOptionGroups(
            [
                { tagName: "shared", tagTypeName: "Line" },
                { tagName: "local", tagTypeName: "Line", databaseId: "factory-db" },
            ],
            [
                { tagTypeName: "Line", required: "False", databaseId: "GLOBAL" },
                { tagTypeName: "Line", required: "True", databaseId: "factory-db" },
            ]
        );

        expect(groups.map((g) => g.label)).toEqual([
            "Line (GLOBAL)",
            "Line (factory-db) [required]",
        ]);
        expect(groups[0].options.map((o) => o.label)).toEqual(["shared (GLOBAL)"]);
        expect(groups[1].options.map((o) => o.label)).toEqual(["local (factory-db)"]);
    });

    it("uses the tag type's recorded scope when its tags carry none", () => {
        // The cached tag-type list predates namespacing, so a scoped type can arrive with no
        // databaseId on its tags; the type's own record is then the only scope information.
        const groups = buildTagOptionGroups(
            [{ tagName: "shared", tagTypeName: "Legacy" }],
            [{ tagTypeName: "Legacy", required: "False", databaseId: "GLOBAL" }]
        );

        expect(groups[0].label).toBe("Legacy (GLOBAL)");
    });
});

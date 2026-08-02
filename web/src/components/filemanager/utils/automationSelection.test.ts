/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { deriveAutomationInputFiles, automationDisabledReason } from "./automationSelection";

const base = {
    databaseId: "db1",
    assetId: "a1",
    isMultiSelect: false,
    selectedItems: [],
    selectedItem: null as any,
    isFolder: false,
};

describe("deriveAutomationInputFiles", () => {
    it("maps a whole-asset selection to the bare asset root", () => {
        // '/' is what the backend's wholeAssetAllowed gate matches on.
        const out = deriveAutomationInputFiles({
            ...base,
            isFolder: true,
            selectedItem: { relativePath: "/" },
        });
        expect(out).toEqual([{ databaseId: "db1", assetId: "a1", relativeFileKey: "/" }]);
    });

    it("keeps a folder's trailing slash", () => {
        // Losing it would turn a folder selection into a file selection, which the folderAllowed gate
        // and the extension filters treat completely differently.
        const out = deriveAutomationInputFiles({
            ...base,
            isFolder: true,
            selectedItem: { relativePath: "models" },
        });
        expect(out[0].relativeFileKey).toBe("/models/");
    });

    it("does not collapse a folder key that already has a trailing slash", () => {
        const out = deriveAutomationInputFiles({
            ...base,
            isFolder: true,
            selectedItem: { relativePath: "/models/" },
        });
        expect(out[0].relativeFileKey).toBe("/models/");
    });

    it("carries no version for a folder", () => {
        // The workflow expands the folder at launch; the files inside have their own versions.
        const out = deriveAutomationInputFiles({
            ...base,
            isFolder: true,
            selectedItem: { relativePath: "/models/", versionId: "v9" },
        });
        expect(out[0].versionId).toBeUndefined();
    });

    it("maps a single file with its version pinned", () => {
        const out = deriveAutomationInputFiles({
            ...base,
            selectedItem: { relativePath: "/pump.glb", versionId: "v3" },
        });
        expect(out).toEqual([
            { databaseId: "db1", assetId: "a1", relativeFileKey: "/pump.glb", versionId: "v3" },
        ]);
    });

    it("normalizes a path with no leading slash", () => {
        const out = deriveAutomationInputFiles({
            ...base,
            selectedItem: { relativePath: "pump.glb" },
        });
        expect(out[0].relativeFileKey).toBe("/pump.glb");
    });

    it("maps every file in a multi-selection", () => {
        const out = deriveAutomationInputFiles({
            ...base,
            isMultiSelect: true,
            selectedItems: [
                { relativePath: "/a.glb", versionId: "v1" },
                { relativePath: "/b.glb", versionId: "v2" },
            ],
        });
        expect(out.map((f) => f.relativeFileKey)).toEqual(["/a.glb", "/b.glb"]);
        expect(out.map((f) => f.versionId)).toEqual(["v1", "v2"]);
    });

    it("does NOT pin a file version while browsing a specific asset version", () => {
        // The asset version already determines which file version applies; pinning again would
        // conflict with it. Same rule the download and view paths follow.
        const out = deriveAutomationInputFiles({
            ...base,
            assetVersionId: "av7",
            selectedItem: { relativePath: "/pump.glb", versionId: "v3" },
        });
        expect(out[0].versionId).toBeUndefined();
    });

    it("drops archived and deleted entries from a multi-selection", () => {
        const out = deriveAutomationInputFiles({
            ...base,
            isMultiSelect: true,
            selectedItems: [
                { relativePath: "/a.glb" },
                { relativePath: "/gone.glb", isArchived: true },
                { relativePath: "/nuked.glb", isPermanentlyDeleted: true },
            ],
        });
        expect(out.map((f) => f.relativeFileKey)).toEqual(["/a.glb"]);
    });

    it("yields nothing for an archived single selection", () => {
        // Launching a workflow against an object with no live version would fail at read time.
        const out = deriveAutomationInputFiles({
            ...base,
            selectedItem: { relativePath: "/gone.glb", isArchived: true },
        });
        expect(out).toEqual([]);
    });

    it("yields nothing when nothing is selected", () => {
        expect(deriveAutomationInputFiles(base)).toEqual([]);
    });

    it("yields nothing without a database or asset in scope", () => {
        expect(
            deriveAutomationInputFiles({
                ...base,
                databaseId: undefined,
                selectedItem: { relativePath: "/a.glb" },
            })
        ).toEqual([]);
    });
});

describe("automationDisabledReason", () => {
    const reason = (input: any) =>
        automationDisabledReason(input, deriveAutomationInputFiles(input));

    it("asks for a selection when there is none", () => {
        expect(reason(base)).toMatch(/Select a file, folder, or asset/);
    });

    it("is undefined for a usable single file", () => {
        expect(reason({ ...base, selectedItem: { relativePath: "/a.glb" } })).toBeUndefined();
    });

    it("is undefined for a usable folder", () => {
        expect(
            reason({ ...base, isFolder: true, selectedItem: { relativePath: "/models/" } })
        ).toBeUndefined();
    });

    it("explains an archived selection rather than silently greying out", () => {
        expect(
            reason({ ...base, selectedItem: { relativePath: "/g.glb", isArchived: true } })
        ).toMatch(/archived/i);
    });

    it("distinguishes a deleted selection from an archived one", () => {
        expect(
            reason({
                ...base,
                selectedItem: { relativePath: "/g.glb", isPermanentlyDeleted: true },
            })
        ).toMatch(/deleted/i);
    });

    it("says how many multi-selected items were dropped", () => {
        expect(
            reason({
                ...base,
                isMultiSelect: true,
                selectedItems: [
                    { relativePath: "/a.glb" },
                    { relativePath: "/g.glb", isArchived: true },
                ],
            })
        ).toMatch(/1 selected item cannot be processed/);
    });

    it("pluralizes the dropped count", () => {
        expect(
            reason({
                ...base,
                isMultiSelect: true,
                selectedItems: [
                    { relativePath: "/a.glb" },
                    { relativePath: "/g.glb", isArchived: true },
                    { relativePath: "/h.glb", isArchived: true },
                ],
            })
        ).toMatch(/2 selected items cannot be processed/);
    });

    it("is undefined when every multi-selected item is usable", () => {
        expect(
            reason({
                ...base,
                isMultiSelect: true,
                selectedItems: [{ relativePath: "/a.glb" }, { relativePath: "/b.glb" }],
            })
        ).toBeUndefined();
    });

    it("blocks a multi-selection where nothing is usable", () => {
        expect(
            reason({
                ...base,
                isMultiSelect: true,
                selectedItems: [
                    { relativePath: "/a.glb", isArchived: true },
                    { relativePath: "/b.glb", isArchived: true },
                ],
            })
        ).toMatch(/cannot be processed/);
    });
});

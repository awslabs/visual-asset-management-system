/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import InputFileSelector from "./InputFileSelector";

jest.mock("../api/queries", () => ({
    useAssetSearch: jest.fn(),
    useAssetFileSearch: jest.fn(),
    useFileVersions: jest.fn(),
}));

const queries = () => require("../api/queries");

/** The shape the file-search hook returns. */
const filePage = (paths: string[], total?: number, listFallback = false) => ({
    data: {
        items: paths.map((p) => ({
            fileName: p.split("/").pop(),
            key: p,
            relativePath: p,
            isFolder: false,
        })),
        total: total ?? paths.length,
        listFallback,
    },
    isFetching: false,
});

const value = { databaseId: "db1", assetId: "a1", relativeFileKey: "" };

/** Open the File picker and return the option labels it offers. */
async function fileOptions() {
    await userEvent.click(screen.getByLabelText("File"));
    const list = await screen.findByRole("listbox");
    return Array.from(list.querySelectorAll('[role="option"]')).map((o) => o.textContent || "");
}

describe("InputFileSelector file filtering", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        queries().useAssetSearch.mockReturnValue({
            data: { items: [{ databaseId: "db1", assetId: "a1", assetName: "Pump" }], total: 1 },
            isFetching: false,
        });
        queries().useFileVersions.mockReturnValue({ data: [], isFetching: false });
    });

    it("offers only the files the workflow's filters admit", async () => {
        // A file the workflow rejects must not be selectable: offering it would let the user pick it
        // and then fail validation on the next step for a reason the picker already knew.
        queries().useAssetFileSearch.mockReturnValue(
            filePage(["/pump.glb", "/notes.txt", "/valve.glb"])
        );
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={value}
                onChange={jest.fn()}
                showVersion={false}
                allowWholeAsset={false}
                inputFileFilters={{ allow: ["*.glb"] }}
            />
        );
        const options = await fileOptions();
        expect(options).toEqual(expect.arrayContaining(["/pump.glb", "/valve.glb"]));
        expect(options).not.toContain("/notes.txt");
    });

    it("applies the exclude list as well as the allow list", async () => {
        queries().useAssetFileSearch.mockReturnValue(
            filePage(["/pump.glb", "/pump.glb.previewFile.gif"])
        );
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={value}
                onChange={jest.fn()}
                showVersion={false}
                allowWholeAsset={false}
                inputFileFilters={{ allow: [], exclude: ["*.previewFile.*"] }}
            />
        );
        const options = await fileOptions();
        expect(options).toContain("/pump.glb");
        expect(options).not.toContain("/pump.glb.previewFile.gif");
    });

    it("says how many files the filters hid, so an absent file is explained", async () => {
        queries().useAssetFileSearch.mockReturnValue(
            filePage(["/pump.glb", "/notes.txt", "/readme.md"])
        );
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={value}
                onChange={jest.fn()}
                showVersion={false}
                allowWholeAsset={false}
                inputFileFilters={{ allow: ["*.glb"] }}
            />
        );
        await userEvent.click(screen.getByLabelText("File"));
        await waitFor(() => {
            expect(
                screen.getByText(/2 files hidden by the workflow's input-file filters/)
            ).toBeInTheDocument();
        });
    });

    it("offers every file when the workflow declares no filters", async () => {
        queries().useAssetFileSearch.mockReturnValue(filePage(["/pump.glb", "/notes.txt"]));
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={value}
                onChange={jest.fn()}
                showVersion={false}
                allowWholeAsset={false}
            />
        );
        const options = await fileOptions();
        expect(options).toEqual(expect.arrayContaining(["/pump.glb", "/notes.txt"]));
    });

    it("passes the typed term to the search hook rather than filtering in memory", async () => {
        queries().useAssetFileSearch.mockReturnValue(filePage(["/pump.glb"]));
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={value}
                onChange={jest.fn()}
                showVersion={false}
                allowWholeAsset={false}
            />
        );
        await userEvent.click(screen.getByLabelText("File"));
        // The dropdown's own search box, not the trigger button. Its presence is what makes this a
        // server-resolved picker: with onQueryChange set, SearchableSelect skips local filtering.
        await userEvent.type(
            await screen.findByPlaceholderText(/Type to search, Enter to refresh/),
            "pump"
        );
        await waitFor(() => {
            const terms = queries().useAssetFileSearch.mock.calls.map((c: any[]) => c[0]);
            expect(terms).toContain("pump");
        });
    });
});

/**
 * The version selector picks an S3 OBJECT version of the selected file.
 *
 * `versionId` travels to `head_object(VersionId=...)` in executeWorkflow, so it must be an S3 version
 * of that exact key. The list was previously fed by the asset-version API keyed on (databaseId,
 * assetId) only, which meant every file row in an asset offered the same — wrong — options, and a
 * chosen id failed the pre-launch existence check.
 */
describe("InputFileSelector file version selector", () => {
    const S3_VERSIONS = [
        {
            versionId: "v-newest",
            relativeKey: "/pump.glb",
            isLatest: true,
            lastModified: "2026-07-30",
        },
        {
            versionId: "v-older",
            relativeKey: "/pump.glb",
            isLatest: false,
            lastModified: "2026-07-01",
        },
    ];

    beforeEach(() => {
        jest.clearAllMocks();
        queries().useAssetSearch.mockReturnValue({
            data: { items: [{ databaseId: "db1", assetId: "a1", assetName: "Pump" }], total: 1 },
            isFetching: false,
        });
        queries().useAssetFileSearch.mockReturnValue(filePage(["/pump.glb", "/valve.glb"]));
        queries().useFileVersions.mockReturnValue({ data: S3_VERSIONS, isFetching: false });
    });

    const renderFor = (relativeFileKey: string, onChange = jest.fn()) => {
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={{ databaseId: "db1", assetId: "a1", relativeFileKey }}
                onChange={onChange}
            />
        );
        return onChange;
    };

    it("queries versions for the SELECTED FILE, not just the asset", () => {
        // The load-bearing assertion: the file key is part of the query. Without it two rows over the
        // same asset would share one version list.
        renderFor("/pump.glb");
        expect(queries().useFileVersions).toHaveBeenCalledWith("db1", "a1", "/pump.glb");
    });

    it("defaults to Latest rather than pinning a version", () => {
        renderFor("/pump.glb");
        // Empty value = send no versionId, so the run reads whatever is current AT LAUNCH instead of
        // whatever was current when the form was filled in.
        expect((screen.getByLabelText("File version") as HTMLSelectElement).value).toBe("");
        expect(screen.getByRole("option", { name: "Latest" })).toBeInTheDocument();
    });

    it("offers every S3 version of the file and reports the chosen id", async () => {
        const onChange = renderFor("/pump.glb");
        await userEvent.selectOptions(screen.getByLabelText("File version"), "v-older");
        expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ versionId: "v-older" }));
    });

    it("labels the newest version as Current so 'Latest' and it are not read as different files", () => {
        renderFor("/pump.glb");
        const labels = Array.from(
            screen.getByLabelText("File version").querySelectorAll("option")
        ).map((o) => o.textContent || "");
        expect(labels.some((l) => l.startsWith("Current") && l.includes("v-newest"))).toBe(true);
        expect(labels.some((l) => l.startsWith("v-older"))).toBe(true);
    });

    it("selecting a different file clears the pinned version", async () => {
        // A version id belongs to one key. Carrying it across a file change would send another file's
        // version and fail the launch existence check.
        const onChange = jest.fn();
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={{
                    databaseId: "db1",
                    assetId: "a1",
                    relativeFileKey: "/pump.glb",
                    versionId: "v-older",
                }}
                onChange={onChange}
            />
        );
        await userEvent.click(screen.getByLabelText("File"));
        await userEvent.click(await screen.findByRole("option", { name: "/valve.glb" }));
        expect(onChange).toHaveBeenCalledWith(
            expect.objectContaining({ relativeFileKey: "/valve.glb", versionId: undefined })
        );
    });

    it("offers no version selector for a whole-asset selection", () => {
        // '/' spans every file in the asset, so there is no single object version to pin.
        renderFor("/");
        expect(screen.queryByLabelText("File version")).not.toBeInTheDocument();
    });

    it("offers no version selector for a folder selection", () => {
        renderFor("/scans/");
        expect(screen.queryByLabelText("File version")).not.toBeInTheDocument();
    });

    it("still offers Latest when the file has no retrievable version history", () => {
        // Versioning disabled, or the lookup failed: the selector must not vanish, because 'Latest'
        // (send nothing) is still a valid and correct choice.
        queries().useFileVersions.mockReturnValue({ data: [], isFetching: false });
        renderFor("/pump.glb");
        expect(screen.getByLabelText("File version")).toBeInTheDocument();
        expect(screen.getByRole("option", { name: "Latest" })).toBeInTheDocument();
    });

    it("is suppressed entirely when the caller turns versions off", () => {
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={{ databaseId: "db1", assetId: "a1", relativeFileKey: "/pump.glb" }}
                onChange={jest.fn()}
                showVersion={false}
            />
        );
        expect(screen.queryByLabelText("File version")).not.toBeInTheDocument();
    });
});

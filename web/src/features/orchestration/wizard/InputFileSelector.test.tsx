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
                screen.getByText(/2 files hidden by the input-file filters/)
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
 * A picker never offers an entity from a scope other than the one selected.
 *
 * Both pickers hold the previous page on screen while the next one loads, so the list does not flash
 * empty on every keystroke. Across a DATABASE or ASSET change that held page describes entities that
 * are not in the new scope, and a pick from it emits a (database, asset, file) triple that does not
 * exist — accepted by the wizard's validation (a key is present) and failed at launch with a 404.
 */
describe("InputFileSelector stale-scope pages", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        queries().useFileVersions.mockReturnValue({ data: [], isFetching: false });
    });

    /** A page the query is serving from the PREVIOUS key while the new one loads. */
    const heldPage = (page: any) => ({ ...page, isPlaceholderData: true });

    it("offers no file while the previous asset's page is still being held", async () => {
        queries().useAssetSearch.mockReturnValue({
            data: { items: [{ assetId: "a2", assetName: "Valve" }], total: 1 },
            isFetching: false,
        });
        queries().useAssetFileSearch.mockReturnValue(filePage(["/a1-only.glb"]));
        const { rerender } = render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={{ databaseId: "db1", assetId: "a1", relativeFileKey: "" }}
                onChange={jest.fn()}
                showVersion={false}
            />
        );
        expect(await fileOptions()).toContain("/a1-only.glb");

        // Asset changes; the hook is still serving a1's page. The dropdown is already open, so the
        // options are read in place rather than toggled shut and reopened.
        queries().useAssetFileSearch.mockReturnValue(heldPage(filePage(["/a1-only.glb"])));
        rerender(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={{ databaseId: "db1", assetId: "a2", relativeFileKey: "" }}
                onChange={jest.fn()}
                showVersion={false}
            />
        );
        await waitFor(() =>
            expect(screen.queryByRole("option", { name: "/a1-only.glb" })).not.toBeInTheDocument()
        );
    });

    it("offers a2's files once its own page arrives", async () => {
        queries().useAssetSearch.mockReturnValue({
            data: { items: [{ assetId: "a2", assetName: "Valve" }], total: 1 },
            isFetching: false,
        });
        queries().useAssetFileSearch.mockReturnValue(filePage(["/a2-only.glb"]));
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={{ databaseId: "db1", assetId: "a2", relativeFileKey: "" }}
                onChange={jest.fn()}
                showVersion={false}
            />
        );
        expect(await fileOptions()).toContain("/a2-only.glb");
    });

    it("offers no asset while the previous database's page is still being held", async () => {
        queries().useAssetFileSearch.mockReturnValue(filePage([]));
        queries().useAssetSearch.mockReturnValue({
            data: { items: [{ assetId: "db1-asset", assetName: "Pump A" }], total: 1 },
            isFetching: false,
        });
        const { rerender } = render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }, { databaseId: "db2" }]}
                value={{ databaseId: "db1", assetId: "", relativeFileKey: "" }}
                onChange={jest.fn()}
                showVersion={false}
            />
        );
        await userEvent.click(screen.getByLabelText("Asset"));
        expect(await screen.findByRole("option", { name: /Pump A/ })).toBeInTheDocument();

        queries().useAssetSearch.mockReturnValue({
            data: { items: [{ assetId: "db1-asset", assetName: "Pump A" }], total: 1 },
            isFetching: false,
            isPlaceholderData: true,
        });
        rerender(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }, { databaseId: "db2" }]}
                value={{ databaseId: "db2", assetId: "", relativeFileKey: "" }}
                onChange={jest.fn()}
                showVersion={false}
            />
        );
        await waitFor(() =>
            expect(screen.queryByRole("option", { name: /Pump A/ })).not.toBeInTheDocument()
        );
    });
});

/**
 * Container selections are offered only where the resolved scope grants them.
 *
 * An omitted scope key is not a grant, so both container options default OFF rather than on: a caller
 * that has not resolved the chain must not have a whole-asset option appear by omission.
 */
describe("InputFileSelector container selections", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        queries().useAssetSearch.mockReturnValue({
            data: { items: [{ assetId: "a1", assetName: "Pump" }], total: 1 },
            isFetching: false,
        });
        queries().useFileVersions.mockReturnValue({ data: [], isFetching: false });
        queries().useAssetFileSearch.mockReturnValue(
            filePage(["/models/pump.glb", "/models/parts/bolt.glb", "/top.glb"])
        );
    });

    const renderWith = (props: Record<string, any> = {}) =>
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={value}
                onChange={jest.fn()}
                showVersion={false}
                {...props}
            />
        );

    it("offers no whole-asset option unless the caller grants it", async () => {
        renderWith();
        expect(await fileOptions()).not.toContain("Whole asset (all files)");
    });

    /** The option labels that are folder keys — a trailing '/' is what makes a key a folder. */
    const folderLabels = (options: string[]) =>
        options.filter((o) => o.startsWith("/") && o.split("Folder")[0].endsWith("/"));

    it("offers no folder option unless the caller grants it", async () => {
        renderWith({ allowWholeAsset: true });
        const options = await fileOptions();
        expect(options).toContain("Whole asset (all files)");
        expect(folderLabels(options)).toEqual([]);
    });

    it("offers every folder the asset's files sit in when folders are allowed", async () => {
        // Folders are derived from the file paths: both file-resolution paths drop folder entries, so
        // the paths are the only record of the structure.
        renderWith({ allowFolder: true });
        const folders = folderLabels(await fileOptions());
        expect(folders.some((o) => o.startsWith("/models/Folder"))).toBe(true);
        expect(folders.some((o) => o.startsWith("/models/parts/Folder"))).toBe(true);
    });

    it("reports a chosen folder as the trailing-slash key", async () => {
        const onChange = jest.fn();
        renderWith({ allowFolder: true, onChange });
        await userEvent.click(screen.getByLabelText("File"));
        const option = (await screen.findAllByRole("option")).find((o) =>
            (o.textContent || "").startsWith("/models/parts/")
        ) as HTMLElement;
        await userEvent.click(option);
        expect(onChange).toHaveBeenCalledWith(
            expect.objectContaining({ relativeFileKey: "/models/parts/" })
        );
    });

    it("hides a folder the resolved filters exclude", async () => {
        renderWith({ allowFolder: true, inputFileFilters: { allow: ["/models/*"] } });
        const options = await fileOptions();
        expect(options.some((o) => o.startsWith("/models/"))).toBe(true);
        expect(options).not.toContain("/top.glb");
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

    it("fetches no version history until the selector is reached when the caller defers it", async () => {
        // A launch can carry hundreds of rows, each with its own version request. Deferred rows cost
        // nothing until someone actually opens one.
        const onChange = jest.fn();
        render(
            <InputFileSelector
                databaseOptions={[{ databaseId: "db1" }]}
                lockedDatabaseId="db1"
                value={{ databaseId: "db1", assetId: "a1", relativeFileKey: "/pump.glb" }}
                onChange={onChange}
                deferVersions
            />
        );
        // The hook is still called (hooks are unconditional) but with no database, which is what
        // disables the query.
        expect(queries().useFileVersions).toHaveBeenCalledWith(undefined, "a1", "/pump.glb");
        expect(queries().useFileVersions).not.toHaveBeenCalledWith("db1", "a1", "/pump.glb");

        await userEvent.click(screen.getByLabelText("File version"));
        await waitFor(() =>
            expect(queries().useFileVersions).toHaveBeenCalledWith("db1", "a1", "/pump.glb")
        );
    });

    it("fetches version history on mount when the caller does not defer", () => {
        renderFor("/pump.glb");
        expect(queries().useFileVersions).toHaveBeenCalledWith("db1", "a1", "/pump.glb");
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

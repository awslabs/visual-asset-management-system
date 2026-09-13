/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The bulk picker: an asset's files as a paged, check-to-select list, plus a paste box. What it
 * emits is judged here — the order, the deduplication against the selection, the cap, and the
 * compatibility gate — with the listing hook stubbed page by page.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BulkFilePicker, { folderKeyFor } from "./BulkFilePicker";
import { inputFileKey } from "./selectedInputFiles";
import type { ResolvedRestrictions } from "./resolveRestrictions";
import type { ExecuteInputFile } from "../types";

jest.mock("../api/queries", () => ({
    useAssetSearch: jest.fn(),
    useAssetFilePages: jest.fn(),
}));

const queries = () => require("../api/queries");

const restrictions = (overrides: Partial<ResolvedRestrictions> = {}): ResolvedRestrictions => ({
    allow: [],
    exclude: [],
    source: "none",
    metadataInputs: [],
    metadataInputKeys: [],
    metadataGatedOff: [],
    arity: "multi",
    outputType: "asset",
    templatesResolved: true,
    wholeAssetAllowed: false,
    folderAllowed: false,
    ...overrides,
});

const item = (key: string) => ({ fileName: key.slice(1), key, relativePath: key, isFolder: false });

/**
 * A listing of several pages. `fetchNextPage` reveals the next one and resolves with the data the
 * component reads, the way the real infinite query does; the hook then returns the revealed pages on
 * the next render.
 */
function pagedListing(pages: string[][]) {
    const state = { loaded: 1, calls: [] as any[] };
    const data = () => ({
        pages: pages.slice(0, state.loaded).map((keys, i) => ({
            items: keys.map(item),
            nextToken: i < pages.length - 1 ? `t${i + 1}` : undefined,
        })),
    });
    const fetchNextPage = jest.fn(async () => {
        if (state.loaded < pages.length) state.loaded += 1;
        return { data: data(), hasNextPage: state.loaded < pages.length };
    });
    queries().useAssetFilePages.mockImplementation((...args: any[]) => {
        state.calls.push(args);
        return {
            data: data(),
            isLoading: false,
            isError: false,
            error: null,
            hasNextPage: state.loaded < pages.length,
            isFetchingNextPage: false,
            fetchNextPage,
        };
    });
    return { fetchNextPage, state };
}

const renderPicker = (props: Partial<React.ComponentProps<typeof BulkFilePicker>> = {}) => {
    const onAdd = jest.fn();
    const onClose = jest.fn();
    render(
        <BulkFilePicker
            onClose={onClose}
            onAdd={onAdd}
            databaseOptions={[{ databaseId: "db1" }]}
            initialDatabaseId="db1"
            initialAssetId="a1"
            restrictions={restrictions()}
            selectedKeys={new Set()}
            existingCount={0}
            {...props}
        />
    );
    return { onAdd, onClose };
};

const added = (onAdd: jest.Mock): string[] =>
    (onAdd.mock.calls[0][0] as ExecuteInputFile[]).map((f) => f.relativeFileKey);

const checkbox = (key: string) => screen.getByLabelText(key) as HTMLInputElement;

beforeEach(() => {
    jest.clearAllMocks();
    queries().useAssetSearch.mockReturnValue({
        data: { items: [{ databaseId: "db1", assetId: "a1", assetName: "Pump" }], total: 1 },
        isFetching: false,
    });
});

describe("BulkFilePicker browsing", () => {
    it("lists the asset's files with a checkbox and a compatibility badge each", () => {
        pagedListing([["/a.glb", "/b.txt"]]);
        renderPicker({ restrictions: restrictions({ allow: ["*.glb"] }) });
        expect(screen.getByRole("dialog", { name: "Add input files" })).toBeInTheDocument();
        expect(checkbox("/a.glb")).toBeEnabled();
        // A file the chain rejects is shown, badged, and not selectable.
        expect(checkbox("/b.txt")).toBeDisabled();
        const rows = screen.getAllByTestId("picker-file-row").map((r) => r.textContent || "");
        expect(rows[0]).toContain("Compatible");
        expect(rows[1]).toContain("Not compatible");
    });

    it("selects all shown, skipping incompatible and already-selected files, and adds them in listing order", async () => {
        pagedListing([["/c.glb", "/a.glb", "/b.txt", "/d.glb"]]);
        const already = new Set([
            inputFileKey({ databaseId: "db1", assetId: "a1", relativeFileKey: "/a.glb" }),
        ]);
        const { onAdd, onClose } = renderPicker({
            restrictions: restrictions({ allow: ["*.glb"] }),
            selectedKeys: already,
        });
        expect(screen.getByText("Already selected")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Select all shown" }));
        expect(screen.getByText("2 files selected")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Add 2 files" }));
        expect(added(onAdd)).toEqual(["/c.glb", "/d.glb"]);
        expect(onAdd.mock.calls[0][0][0]).toEqual({
            databaseId: "db1",
            assetId: "a1",
            relativeFileKey: "/c.glb",
        });
        expect(onClose).toHaveBeenCalled();
    });

    it("narrows Select all shown to the filter", async () => {
        pagedListing([["/scan-1.e57", "/scan-2.e57", "/photo.jpg"]]);
        const { onAdd } = renderPicker();
        await userEvent.type(screen.getByLabelText("Filter files"), "scan");
        expect(screen.getAllByTestId("picker-file-row")).toHaveLength(2);
        await userEvent.click(screen.getByRole("button", { name: "Select all shown" }));
        await userEvent.click(screen.getByRole("button", { name: "Add 2 files" }));
        expect(added(onAdd)).toEqual(["/scan-1.e57", "/scan-2.e57"]);
    });

    it("toggles one file and disables Add with nothing checked", async () => {
        pagedListing([["/a.glb", "/b.glb"]]);
        renderPicker();
        expect(screen.getByRole("button", { name: "Add 0 files" })).toBeDisabled();
        await userEvent.click(checkbox("/b.glb"));
        expect(screen.getByRole("button", { name: "Add 1 file" })).toBeEnabled();
        await userEvent.click(checkbox("/b.glb"));
        expect(screen.getByRole("button", { name: "Add 0 files" })).toBeDisabled();
    });

    it("walks every page for Select all matching and loads more on demand", async () => {
        const { fetchNextPage } = pagedListing([
            ["/p1.glb", "/p2.glb"],
            ["/p3.glb", "/p4.glb"],
            ["/p5.glb"],
        ]);
        const { onAdd } = renderPicker();
        expect(screen.getByText("2 files loaded · more available")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Select all matching" }));
        await waitFor(() => expect(screen.getByText("5 files selected")).toBeInTheDocument());
        expect(fetchNextPage).toHaveBeenCalledTimes(2);
        expect(screen.getByText("5 files loaded")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Add 5 files" }));
        expect(added(onAdd)).toEqual(["/p1.glb", "/p2.glb", "/p3.glb", "/p4.glb", "/p5.glb"]);
    });

    it("Load more reveals the next page without selecting", async () => {
        const { fetchNextPage } = pagedListing([["/p1.glb"], ["/p2.glb"]]);
        renderPicker();
        await userEvent.click(screen.getByRole("button", { name: "Load more" }));
        expect(fetchNextPage).toHaveBeenCalledTimes(1);
        // The stub cannot re-render the component the way the live query does; typing in the
        // filter does, and the revealed page is then listed.
        await userEvent.type(screen.getByLabelText("Filter files"), "p");
        expect(screen.getByLabelText("/p2.glb")).toBeInTheDocument();
        expect(screen.getByText("0 files selected")).toBeInTheDocument();
    });

    it("stops Select all matching at what the run can still take", async () => {
        const { fetchNextPage } = pagedListing([
            ["/p1.glb", "/p2.glb"],
            ["/p3.glb", "/p4.glb"],
            ["/p5.glb"],
        ]);
        // 997 already selected: three more reach the cap, so the third page is never fetched.
        renderPicker({ existingCount: 997 });
        await userEvent.click(screen.getByRole("button", { name: "Select all matching" }));
        await waitFor(() => expect(screen.getByText("3 files selected")).toBeInTheDocument());
        expect(fetchNextPage).toHaveBeenCalledTimes(1);
        expect(screen.getByText("Stopped at the 1000-file limit.")).toBeInTheDocument();
    });

    it("walks past a page whose files are all already selected", async () => {
        // Select all shown, Add, reopen, Select all matching: the first page is then entirely in
        // the selection and only the next page holds anything to add.
        const first = Array.from({ length: 500 }, (_, i) => `/f${i}.glb`);
        const second = Array.from({ length: 300 }, (_, i) => `/g${i}.glb`);
        const { fetchNextPage } = pagedListing([first, second]);
        const already = new Set(
            first.map((key) =>
                inputFileKey({ databaseId: "db1", assetId: "a1", relativeFileKey: key })
            )
        );
        renderPicker({ selectedKeys: already, existingCount: 500 });
        await userEvent.click(screen.getByRole("button", { name: "Select all matching" }));
        await waitFor(() => expect(screen.getByText("300 files selected")).toBeInTheDocument());
        expect(fetchNextPage).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("button", { name: "Add 300 files" })).toBeEnabled();
        expect(screen.queryByText(/Stopped at/)).not.toBeInTheDocument();
    });

    it("walks past a page whose files the chain rejects, counting only what the run could take", async () => {
        const { fetchNextPage } = pagedListing([
            ["/n1.txt", "/n2.txt", "/n3.txt"],
            ["/p1.glb", "/p2.glb"],
        ]);
        renderPicker({ restrictions: restrictions({ allow: ["*.glb"] }), existingCount: 997 });
        await userEvent.click(screen.getByRole("button", { name: "Select all matching" }));
        await waitFor(() => expect(screen.getByText("2 files selected")).toBeInTheDocument());
        expect(fetchNextPage).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("button", { name: "Add 2 files" })).toBeEnabled();
    });

    it("says so when every matching file is already selected or not compatible", async () => {
        pagedListing([["/a.glb", "/b.txt"]]);
        const already = new Set([
            inputFileKey({ databaseId: "db1", assetId: "a1", relativeFileKey: "/a.glb" }),
        ]);
        renderPicker({ restrictions: restrictions({ allow: ["*.glb"] }), selectedKeys: already });
        await userEvent.click(screen.getByRole("button", { name: "Select all matching" }));
        await waitFor(() =>
            expect(
                screen.getByText("Every matching file is already selected or not compatible.")
            ).toBeInTheDocument()
        );
        expect(screen.getByText("0 files selected")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Add 0 files" })).toBeDisabled();
    });

    it("refuses to add when the selection is already at the cap", () => {
        pagedListing([["/p1.glb"]]);
        renderPicker({ existingCount: 1000 });
        expect(screen.getByText(/already holds 1000 files/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Add 0 files" })).toBeDisabled();
    });

    it("scopes the listing to the folder prefix on Enter and offers the folder itself when allowed", async () => {
        const { state } = pagedListing([["/docs/d1.txt"]]);
        const { onAdd, onClose } = renderPicker({
            restrictions: restrictions({ folderAllowed: true }),
        });
        const prefix = screen.getByLabelText("Folder prefix");
        await userEvent.type(prefix, "docs{enter}");
        await waitFor(() =>
            expect(state.calls[state.calls.length - 1]).toEqual(["db1", "a1", "/docs/"])
        );
        await userEvent.click(screen.getByRole("button", { name: "Add folder /docs/" }));
        expect(added(onAdd)).toEqual(["/docs/"]);
        expect(onClose).toHaveBeenCalled();
    });

    it("offers the whole asset only when the resolved scope allows it", async () => {
        pagedListing([["/a.glb"]]);
        const { onAdd } = renderPicker({ restrictions: restrictions({ wholeAssetAllowed: true }) });
        await userEvent.click(screen.getByRole("button", { name: "Add whole asset" }));
        expect(added(onAdd)).toEqual(["/"]);
    });

    it("hides the container actions when the scope does not grant them", () => {
        pagedListing([["/a.glb"]]);
        renderPicker();
        expect(screen.queryByRole("button", { name: "Add whole asset" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Add folder/ })).not.toBeInTheDocument();
    });

    it("asks for an asset before listing", () => {
        pagedListing([[]]);
        renderPicker({ initialAssetId: "" });
        expect(screen.getByText(/Choose a database and an asset/)).toBeInTheDocument();
        expect(screen.getByLabelText("Folder prefix")).toBeDisabled();
    });
});

describe("BulkFilePicker paste keys", () => {
    beforeEach(() => pagedListing([[]]));

    it("adds one normalized key per line for the chosen asset, once each", async () => {
        const { onAdd, onClose } = renderPicker();
        await userEvent.click(screen.getByRole("tab", { name: "Paste keys" }));
        await userEvent.type(
            screen.getByLabelText("Relative file keys"),
            "scans/1.e57{enter}/scans/2.e57{enter}{enter}scans/1.e57"
        );
        expect(screen.getByText("2 keys")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Add 2 keys" }));
        expect(added(onAdd)).toEqual(["/scans/1.e57", "/scans/2.e57"]);
        expect(onClose).toHaveBeenCalled();
    });

    it("counts and leaves out keys the chain rejects or the selection already holds", async () => {
        const already = new Set([
            inputFileKey({ databaseId: "db1", assetId: "a1", relativeFileKey: "/a.glb" }),
        ]);
        const { onAdd } = renderPicker({
            restrictions: restrictions({ allow: ["*.glb"] }),
            selectedKeys: already,
        });
        await userEvent.click(screen.getByRole("tab", { name: "Paste keys" }));
        await userEvent.type(
            screen.getByLabelText("Relative file keys"),
            "/a.glb{enter}/b.glb{enter}/c.txt"
        );
        expect(
            screen.getByText("3 keys · 1 not compatible · 1 already selected")
        ).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Add 1 key" }));
        expect(added(onAdd)).toEqual(["/b.glb"]);
    });

    it("treats a trailing slash as a folder and a bare slash as the whole asset", async () => {
        const { onAdd } = renderPicker({
            restrictions: restrictions({ folderAllowed: true, wholeAssetAllowed: true }),
        });
        await userEvent.click(screen.getByRole("tab", { name: "Paste keys" }));
        await userEvent.type(screen.getByLabelText("Relative file keys"), "docs/{enter}/");
        await userEvent.click(screen.getByRole("button", { name: "Add 2 keys" }));
        expect(added(onAdd)).toEqual(["/docs/", "/"]);
    });
});

describe("folderKeyFor", () => {
    it("normalizes a typed prefix to a trailing-slash key", () => {
        expect(folderKeyFor("docs")).toBe("/docs/");
        expect(folderKeyFor("/docs/sub/")).toBe("/docs/sub/");
        expect(folderKeyFor("  ")).toBe("");
        expect(folderKeyFor("/")).toBe("");
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useMemo } from "react";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation, useNavigate } from "react-router-dom";
import { EnhancedFileManager } from "./EnhancedFileManager";
import { AssetDetailContext } from "../../context/AssetDetailContext";

jest.mock("../../services/APIService", () => ({
    fetchAssetS3FilesStreaming: jest.fn(),
    fetchFileInfo: jest.fn(),
    downloadAsset: jest.fn(),
}));

// The details panel and preview modal pull in many heavy dependencies that
// are irrelevant to selection behavior.
jest.mock("./components/FileDetailsPanel", () => ({
    FileDetailsPanel: () => null,
}));
jest.mock("./modals/AssetPreviewModal", () => ({
    __esModule: true,
    default: () => null,
}));

const { fetchAssetS3FilesStreaming } = require("../../services/APIService");

const testFiles = [
    {
        fileName: "a.txt",
        key: "asset1/a.txt",
        relativePath: "/a.txt",
        isFolder: false,
        size: 10,
        dateCreatedCurrentVersion: "2026-01-01T00:00:00Z",
        versionId: "v1",
        isArchived: false,
        primaryType: null,
    },
    {
        fileName: "b.txt",
        key: "asset1/b.txt",
        relativePath: "/b.txt",
        isFolder: false,
        size: 20,
        dateCreatedCurrentVersion: "2026-01-01T00:00:00Z",
        versionId: "v1",
        isArchived: false,
        primaryType: null,
    },
    {
        fileName: "c.txt",
        key: "asset1/c.txt",
        relativePath: "/c.txt",
        isFolder: false,
        size: 30,
        dateCreatedCurrentVersion: "2026-01-01T00:00:00Z",
        versionId: "v1",
        isArchived: false,
        primaryType: null,
    },
];

// Mirrors ViewAsset's `?filePath=` URL synchronization so the test exercises
// the same selection -> URL -> filePathToNavigate feedback loop that exists
// in the real asset detail page.
function Harness() {
    const location = useLocation();
    const navigate = useNavigate();

    const filePathToNavigate = useMemo(() => {
        const params = new URLSearchParams(location.search);
        const fromQuery = params.get("filePath");
        if (fromQuery) return fromQuery;
        return (location.state as any)?.filePathToNavigate ?? undefined;
    }, [location.search, location.state]);

    const handleSelectedPathChange = useCallback(
        (path: string | null) => {
            const params = new URLSearchParams(location.search);
            const current = params.get("filePath");
            if (path === null) {
                if (current === null) return;
                params.delete("filePath");
            } else {
                if (current === path) return;
                params.set("filePath", path);
            }
            const search = params.toString();
            navigate(
                { search: search ? `?${search}` : "" },
                { replace: true, state: location.state }
            );
        },
        [location.search, location.state, navigate]
    );

    return (
        <AssetDetailContext.Provider value={{ state: {} as any, dispatch: jest.fn() }}>
            <div data-testid="search-probe">{location.search}</div>
            <EnhancedFileManager
                assetName="TestAsset"
                filePathToNavigate={filePathToNavigate}
                onSelectedPathChange={handleSelectedPathChange}
            />
        </AssetDetailContext.Provider>
    );
}

function renderFileManager() {
    return render(
        <MemoryRouter initialEntries={["/databases/db1/assets/asset1"]}>
            <Routes>
                <Route path="/databases/:databaseId/assets/:assetId" element={<Harness />} />
            </Routes>
        </MemoryRouter>
    );
}

async function flushEffects() {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
    });
}

function getFilePathParam() {
    const search = screen.getByTestId("search-probe").textContent || "";
    return new URLSearchParams(search).get("filePath");
}

function getMultiSelectedNames(container: HTMLElement): string[] {
    return Array.from(container.querySelectorAll(".tree-item-content.multi-selected")).map(
        (el) => el.querySelector(".tree-item-name")?.textContent?.trim() || ""
    );
}

describe("EnhancedFileManager multi-selection with URL filePath sync", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (fetchAssetS3FilesStreaming as jest.Mock).mockImplementation(async function* () {
            yield {
                success: true,
                items: testFiles,
                nextToken: null,
                error: null,
                pageNumber: 1,
                isLastPage: true,
            };
        });
    });

    it("auto-selects the root asset node on load when no filePath is provided", async () => {
        const { container } = renderFileManager();

        await waitFor(() => expect(screen.getByText("a.txt")).toBeInTheDocument());
        await flushEffects();

        // The root (top) node is selected by default (its name renders with a
        // folder-count suffix, e.g. "TestAsset(3)").
        await waitFor(() => {
            const rootRow = container.querySelector(".tree-item-content.selected");
            expect(rootRow?.querySelector(".tree-item-name")?.textContent).toContain("TestAsset");
        });
        // ...and the synthetic root path never lands in the URL as a deep-link.
        expect(getFilePathParam()).toBeNull();
    });

    it("keeps multiple files selected on ctrl+click and clears the filePath param", async () => {
        const { container } = renderFileManager();

        await waitFor(() => expect(screen.getByText("a.txt")).toBeInTheDocument());

        fireEvent.click(screen.getByText("a.txt"));
        await flushEffects();
        expect(getFilePathParam()).toBe("/a.txt");

        fireEvent.click(screen.getByText("b.txt"), { ctrlKey: true });
        await flushEffects();

        // Both files must remain selected after the URL-sync effects settle
        expect(getMultiSelectedNames(container)).toEqual(
            expect.arrayContaining(["a.txt", "b.txt"])
        );
        // Multi-selection cannot be represented in the URL - param is cleared
        expect(getFilePathParam()).toBeNull();
    });

    it("keeps a shift+click range selected", async () => {
        const { container } = renderFileManager();

        await waitFor(() => expect(screen.getByText("a.txt")).toBeInTheDocument());

        fireEvent.click(screen.getByText("a.txt"));
        await flushEffects();

        fireEvent.click(screen.getByText("c.txt"), { shiftKey: true });
        await flushEffects();

        expect(getMultiSelectedNames(container)).toEqual(
            expect.arrayContaining(["a.txt", "b.txt", "c.txt"])
        );
        expect(getFilePathParam()).toBeNull();
    });

    it("restores the filePath param when returning to a single selection", async () => {
        const { container } = renderFileManager();

        await waitFor(() => expect(screen.getByText("a.txt")).toBeInTheDocument());

        fireEvent.click(screen.getByText("a.txt"));
        await flushEffects();
        fireEvent.click(screen.getByText("b.txt"), { ctrlKey: true });
        await flushEffects();
        expect(getFilePathParam()).toBeNull();

        fireEvent.click(screen.getByText("c.txt"));
        await flushEffects();

        expect(getFilePathParam()).toBe("/c.txt");
        expect(getMultiSelectedNames(container)).toEqual([]);
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The file page looks a file up once when it is opened from a link that names no version.
 *
 * The page picks the latest version and writes it into the URL as `?version=`. That rewrite changes
 * the URL the direct-path load depends on, so the load must recognise that the file it already holds
 * answers the rewritten URL; otherwise it looks the same file up again, shows its spinner a second
 * time, and remounts the file versions table, which loads its versions again.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router";

const mockMissing = new Set<string>();

jest.mock("../../services/APIService", () => ({
    fetchAsset: jest.fn(
        async ({ databaseId, assetId }: { databaseId: string; assetId: string }) => ({
            assetId,
            databaseId,
            assetName: `Asset ${assetId}`,
            isDistributable: true,
        })
    ),
    fetchFileInfo: jest.fn(
        async ({ assetId, fileKey }: { databaseId: string; assetId: string; fileKey: string }) => {
            const rel = fileKey.replace(new RegExp(`^/?${assetId}/`), "").replace(/^\//, "");
            if (mockMissing.has(rel)) {
                return [false, "The file you're looking for doesn't exist or has been moved."];
            }
            return [
                true,
                {
                    key: `${assetId}/${rel}`,
                    fileName: rel.split("/").pop(),
                    versions: [
                        { versionId: `v-${rel}`, isLatest: true },
                        { versionId: "v-old", isLatest: false },
                    ],
                },
            ];
        }
    ),
}));
jest.mock("../../services/FileOperationsService", () => ({ archiveFile: jest.fn() }));
jest.mock("../metadata/FileMetadata", () => ({ __esModule: true, default: () => null }));

// Records each time the versions table is mounted and each time it would load its versions: the
// real table loads on mount and whenever its file changes.
const mockTableMounts: string[] = [];
const mockTableLoads: string[] = [];
jest.mock("../filemanager/components/FileVersionsTable", () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const ReactInMock = require("react");
    return {
        FileVersionsTable: (props: { databaseId: string; assetId: string; filePath: string }) => {
            ReactInMock.useEffect(() => {
                mockTableMounts.push(props.filePath);
                // Mount-only: counts table instances
                // eslint-disable-next-line react-hooks/exhaustive-deps
            }, []);
            ReactInMock.useEffect(() => {
                mockTableLoads.push(props.filePath);
            }, [props.databaseId, props.assetId, props.filePath]);
            return null;
        },
    };
});
// The registry pulls in Vite's import.meta.glob, which jest cannot load; only types are used here.
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({}));
jest.mock("../../visualizerPlugin/core/viewableExtensions", () => ({
    availableModesForFiles: () => ({ visualize: true, compare: false }),
}));
jest.mock("../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => true,
}));
jest.mock("../../visualizerPlugin/components/DynamicViewer", () => ({
    __esModule: true,
    default: (props: { files: { key: string; versionId?: string }[] }) => (
        <div data-testid="viewer">{`${props.files[0]?.key}@${props.files[0]?.versionId}`}</div>
    ),
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const ViewFile = require("./ViewFile").default;
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { fetchFileInfo } = require("../../services/APIService");

let go: ReturnType<typeof useNavigate> = () => undefined;
let currentUrl = "";
let spinnerShows = 0;
const Probe = () => {
    const navigate = useNavigate();
    const location = useLocation();
    go = navigate;
    currentUrl = location.pathname + location.search;
    return null;
};

const FILE_ROUTE = "/databases/db1/assets/assetA/file";

const settle = () =>
    act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 100));
    });
const viewer = () => screen.queryByTestId("viewer")?.textContent;
const lookedUp = () =>
    fetchFileInfo.mock.calls.map(([args]: [{ fileKey: string }]) =>
        args.fileKey.replace(/^\/?assetA\//, "").replace(/^\//, "")
    );

// Counts each separate appearance of the loading spinner.
let spinnerObserver: MutationObserver | null = null;
const watchSpinner = () => {
    let shown = false;
    spinnerObserver = new MutationObserver(() => {
        const now = document.body.textContent?.includes("Loading file...") ?? false;
        if (now && !shown) {
            spinnerShows += 1;
        }
        shown = now;
    });
    spinnerObserver.observe(document.body, { childList: true, subtree: true, characterData: true });
};

const openAt = async (url: string) => {
    watchSpinner();
    render(
        <MemoryRouter initialEntries={[url]}>
            <Probe />
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId/file/*"
                    element={<ViewFile />}
                />
            </Routes>
        </MemoryRouter>
    );
    await waitFor(() => expect(viewer()).toBeTruthy());
    await settle();
};
const move = async (to: string | number) => {
    await act(async () => (typeof to === "number" ? go(to) : go(to)));
    await settle();
};
const resetCounts = () => {
    fetchFileInfo.mockClear();
    mockTableMounts.length = 0;
    mockTableLoads.length = 0;
    spinnerShows = 0;
};

beforeEach(() => {
    jest.clearAllMocks();
    mockMissing.clear();
    mockTableMounts.length = 0;
    mockTableLoads.length = 0;
    spinnerShows = 0;
    currentUrl = "";
});
afterEach(() => {
    spinnerObserver?.disconnect();
    spinnerObserver = null;
});

describe("ViewFile link without a version", () => {
    it("looks the file up once and writes its latest version into the URL", async () => {
        await openAt(`${FILE_ROUTE}/a.glb`);

        expect(viewer()).toBe("assetA/a.glb@v-a.glb");
        expect(currentUrl).toBe(`${FILE_ROUTE}/a.glb?version=v-a.glb`);
        expect(lookedUp()).toEqual(["a.glb"]);
    });

    it("shows the loading spinner once", async () => {
        await openAt(`${FILE_ROUTE}/a.glb`);

        expect(spinnerShows).toBe(1);
    });

    it("mounts the file versions table once, so it loads its versions once", async () => {
        await openAt(`${FILE_ROUTE}/a.glb`);

        expect(mockTableMounts).toEqual(["assetA/a.glb"]);
        expect(mockTableLoads).toEqual(["assetA/a.glb"]);
    });

    it("looks the file up once when the link names a version", async () => {
        await openAt(`${FILE_ROUTE}/a.glb?version=v-old`);

        expect(viewer()).toBe("assetA/a.glb@v-old");
        expect(currentUrl).toBe(`${FILE_ROUTE}/a.glb?version=v-old`);
        expect(lookedUp()).toEqual(["a.glb"]);
        expect(mockTableMounts).toEqual(["assetA/a.glb"]);
    });

    it("looks another file of the same asset up once when its link names no version", async () => {
        await openAt(`${FILE_ROUTE}/a.glb`);
        resetCounts();

        await move(`${FILE_ROUTE}/b.glb`);

        expect(viewer()).toBe("assetA/b.glb@v-b.glb");
        expect(currentUrl).toBe(`${FILE_ROUTE}/b.glb?version=v-b.glb`);
        expect(lookedUp()).toEqual(["b.glb"]);
        expect(mockTableMounts).toEqual(["assetA/b.glb"]);
    });

    it("still looks the file up when only the version in the URL changes", async () => {
        await openAt(`${FILE_ROUTE}/a.glb`);
        resetCounts();

        await move(`${FILE_ROUTE}/a.glb?version=v-old`);

        expect(viewer()).toBe("assetA/a.glb@v-old");
        expect(currentUrl).toBe(`${FILE_ROUTE}/a.glb?version=v-old`);
        expect(lookedUp()).toEqual(["a.glb"]);
    });

    it("shows the first file again after Back from a file that was not found", async () => {
        await openAt(`${FILE_ROUTE}/a.glb`);
        mockMissing.add("c.glb");

        await move(`${FILE_ROUTE}/c.glb`);
        expect(screen.getByText("File Not Found")).toBeTruthy();
        expect(currentUrl).toBe(`${FILE_ROUTE}/c.glb`);

        await move(-1);

        expect(screen.queryByText("File Not Found")).toBeNull();
        expect(viewer()).toBe("assetA/a.glb@v-a.glb");
        expect(currentUrl).toBe(`${FILE_ROUTE}/a.glb?version=v-a.glb`);
    });
});

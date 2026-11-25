/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The file page opened from the File Manager before the file's version has loaded.
 *
 * The navigation state then names the file but no version, so the page looks the version up. It
 * asks once per file open: a failed lookup is not retried in a loop, the URL gaining the version does
 * not send the page to its direct-path load for the same file, and an answer that arrives after the
 * page has moved to another file does not replace that file (issue #519).
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router";

type Gate = { promise: Promise<void>; open: () => void };
const mockGates: Record<string, Gate> = {};
const gate = (file: string): Gate => {
    let open: () => void = () => undefined;
    const promise = new Promise<void>((resolve) => {
        open = resolve;
    });
    mockGates[file] = { promise, open };
    return mockGates[file];
};
// Files whose lookup fails. The answer is a failure tuple, as fetchFileInfo returns for a rejected
// request, and it takes a few milliseconds, as a request over the network does.
const mockFailing = new Set<string>();
// A runaway loop of lookups stops answering after this many calls, so a failing run ends.
const mockCallCap = 20;
let mockCallCount = 0;

jest.mock("../../services/APIService", () => ({
    fetchAssetWithStatus: jest.fn(
        async ({ databaseId, assetId }: { databaseId: string; assetId: string }) => [
            true,
            {
                assetId,
                databaseId,
                assetName: `Asset ${assetId}`,
                isDistributable: true,
            },
        ]
    ),
    fetchFileInfo: jest.fn(
        async ({ assetId, fileKey }: { databaseId: string; assetId: string; fileKey: string }) => {
            mockCallCount += 1;
            if (mockCallCount > mockCallCap) {
                return new Promise(() => undefined);
            }
            const rel = fileKey.replace(new RegExp(`^/?${assetId}/`), "").replace(/^\//, "");
            if (mockGates[rel]) {
                await mockGates[rel].promise;
            }
            if (mockFailing.has(rel)) {
                await new Promise((resolve) => setTimeout(resolve, 20));
                return [false, "Error fetching file info", 500];
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
jest.mock("../filemanager/components/FileVersionsTable", () => ({
    FileVersionsTable: () => null,
}));
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
    default: (props: { files: { key: string; versionId?: string }[]; assetId: string }) => (
        <div data-testid="viewer">{`${props.assetId}:${props.files[0]?.key}@${props.files[0]?.versionId}`}</div>
    ),
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const ViewFile = require("./ViewFile").default;
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { fetchFileInfo } = require("../../services/APIService");

let go: ReturnType<typeof useNavigate> = () => undefined;
let visited: string[] = [];
const Probe = () => {
    const navigate = useNavigate();
    const location = useLocation();
    go = navigate;
    const url = location.pathname + location.search;
    if (visited[visited.length - 1] !== url) {
        visited.push(url);
    }
    return null;
};

const FILE_ROUTE = "/databases/db1/assets/assetA/file";

/** The File Manager's View File before the file's version loaded: the state names no version. */
const fromFileManager = (file: string) => ({
    pathname: `${FILE_ROUTE}/${file}`,
    state: { filename: file, key: `assetA/${file}`, isDirectory: false },
});

const openFromFileManager = (file: string, { strict = false } = {}) => {
    const tree = (
        <MemoryRouter initialEntries={[fromFileManager(file)]}>
            <Probe />
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId/file/*"
                    element={<ViewFile />}
                />
            </Routes>
        </MemoryRouter>
    );
    render(strict ? <React.StrictMode>{tree}</React.StrictMode> : tree);
};
const settle = () =>
    act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 100));
    });
/**
 * Waits with React's scheduler running between polls, as in a browser, so an effect that re-arms
 * itself runs again during the wait. (Inside act, React batches the updates until the scope ends.)
 */
const idle = (ms: number) => {
    const until = Date.now() + ms;
    return waitFor(() => expect(Date.now()).toBeGreaterThanOrEqual(until), {
        timeout: ms + 1000,
    });
};
const viewer = () => screen.getByTestId("viewer").textContent;
const lastUrl = () => visited[visited.length - 1];
const lookups = (file: string) =>
    fetchFileInfo.mock.calls.filter(([args]: [{ fileKey: string }]) => args.fileKey.endsWith(file))
        .length;

beforeEach(() => {
    jest.clearAllMocks();
    Object.keys(mockGates).forEach((file) => delete mockGates[file]);
    mockFailing.clear();
    mockCallCount = 0;
    visited = [];
});

describe("ViewFile opened from the File Manager without a version", () => {
    it("looks the file up once and puts its version in the URL", async () => {
        openFromFileManager("a.glb");

        await waitFor(() => expect(viewer()).toBe("assetA:assetA/a.glb@v-a.glb"));
        await settle();

        expect(lookups("a.glb")).toBe(1);
        expect(lastUrl()).toBe(`${FILE_ROUTE}/a.glb?version=v-a.glb`);
        expect(viewer()).toBe("assetA:assetA/a.glb@v-a.glb");
    });

    it("does not repeat a lookup that fails", async () => {
        mockFailing.add("a.glb");
        openFromFileManager("a.glb");

        await waitFor(() => expect(lookups("a.glb")).toBeGreaterThan(0));
        await settle();
        await idle(300);

        expect(lookups("a.glb")).toBe(1);
        // The page keeps the file the navigation state names.
        expect(viewer()).toBe("assetA:assetA/a.glb@undefined");
        expect(lastUrl()).toBe(`${FILE_ROUTE}/a.glb`);
    });

    it("keeps a file opened by its link while the previous file's lookup is still out", async () => {
        const slow = gate("a.glb");
        openFromFileManager("a.glb");
        await waitFor(() => expect(lookups("a.glb")).toBe(1));

        // A pasted link to another file of the same asset carries no navigation state.
        visited = [];
        await act(async () => go(`${FILE_ROUTE}/b.glb`));
        await waitFor(() => expect(viewer()).toBe("assetA:assetA/b.glb@v-b.glb"));
        await settle();

        await act(async () => slow.open());
        await settle();

        expect(viewer()).toBe("assetA:assetA/b.glb@v-b.glb");
        expect(lastUrl()).toBe(`${FILE_ROUTE}/b.glb?version=v-b.glb`);
        expect(visited.filter((url) => url.includes("a.glb"))).toEqual([]);
    });

    it("shows the newly opened file, not the previous one, while its version loads", async () => {
        openFromFileManager("a.glb");
        await waitFor(() => expect(viewer()).toBe("assetA:assetA/a.glb@v-a.glb"));
        await settle();

        const slow = gate("b.glb");
        visited = [];
        const next = fromFileManager("b.glb");
        await act(async () => go(next.pathname, { state: next.state }));
        await settle();

        expect(viewer()).toBe("assetA:assetA/b.glb@undefined");
        expect(visited.filter((url) => url.includes("a.glb"))).toEqual([]);

        await act(async () => slow.open());
        await settle();

        expect(viewer()).toBe("assetA:assetA/b.glb@v-b.glb");
        expect(lastUrl()).toBe(`${FILE_ROUTE}/b.glb?version=v-b.glb`);
        expect(lookups("b.glb")).toBe(1);
    });

    it("still applies the version when React runs the lookup effect twice", async () => {
        openFromFileManager("a.glb", { strict: true });

        await waitFor(() => expect(viewer()).toBe("assetA:assetA/a.glb@v-a.glb"));
        await settle();

        expect(lastUrl()).toBe(`${FILE_ROUTE}/a.glb?version=v-a.glb`);
    });
});

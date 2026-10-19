/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The file page follows the URL when it moves to another file of the same asset.
 *
 * React Router keeps the page mounted when only the file path changes, so the previous file is still
 * in hand when the new URL renders. The URL-sync effect must not write that file back into the URL:
 * doing so sent the address, and the next file lookup, back to the previous file.
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
            if (mockGates[rel]) {
                await mockGates[rel].promise;
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
const { fetchAsset, fetchFileInfo } = require("../../services/APIService");

let go: (to: string, options?: { state?: unknown }) => void = () => undefined;
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

const openAt = async (file: string) => {
    render(
        <MemoryRouter initialEntries={[`${FILE_ROUTE}/${file}`]}>
            <Probe />
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId/file/*"
                    element={<ViewFile />}
                />
                <Route path="/databases/:databaseId/assets/:assetId/file" element={<ViewFile />} />
            </Routes>
        </MemoryRouter>
    );
    await waitFor(() => expect(viewer()).toBe(`assetA:assetA/${file}@v-${file}`));
    await settle();
};
const move = async (to: string, options?: { state?: unknown }) => {
    visited = [];
    await act(async () => go(to, options));
};
const settle = () =>
    act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 100));
    });
const viewer = () => screen.getByTestId("viewer").textContent;
const lastUrl = () => visited[visited.length - 1];
const lookedUp = () =>
    fetchFileInfo.mock.calls.map(([args]: [{ fileKey: string }]) => args.fileKey);

beforeEach(() => {
    jest.clearAllMocks();
    Object.keys(mockGates).forEach((file) => delete mockGates[file]);
    visited = [];
});

describe("ViewFile file switch within one asset", () => {
    it("shows the other file when the URL moves to it", async () => {
        await openAt("a.glb");
        fetchFileInfo.mockClear();

        await move(`${FILE_ROUTE}/b.glb`);
        await settle();

        expect(viewer()).toBe("assetA:assetA/b.glb@v-b.glb");
        expect(lastUrl()).toBe(`${FILE_ROUTE}/b.glb?version=v-b.glb`);
        expect(visited.filter((url) => url.includes("a.glb"))).toEqual([]);
        expect(lookedUp().filter((key: string) => key.includes("a.glb"))).toEqual([]);
    });

    it("keeps a version named in the new URL", async () => {
        await openAt("a.glb");

        await move(`${FILE_ROUTE}/b.glb?version=v-old`);
        await settle();

        expect(viewer()).toBe("assetA:assetA/b.glb@v-old");
        expect(visited).toEqual([`${FILE_ROUTE}/b.glb?version=v-old`]);
    });

    it("keeps an older version of the same file named in the new URL", async () => {
        await openAt("a.glb");

        await move(`${FILE_ROUTE}/a.glb?version=v-old`);
        await settle();

        expect(viewer()).toBe("assetA:assetA/a.glb@v-old");
        expect(visited).toEqual([`${FILE_ROUTE}/a.glb?version=v-old`]);
    });

    it("does not go back to the first file while the second one loads", async () => {
        await openAt("a.glb");
        const slow = gate("b.glb");

        await move(`${FILE_ROUTE}/b.glb`);
        await settle();
        expect(visited).toEqual([`${FILE_ROUTE}/b.glb`]);

        await act(async () => slow.open());
        await settle();

        expect(viewer()).toBe("assetA:assetA/b.glb@v-b.glb");
        expect(visited.filter((url) => url.includes("a.glb"))).toEqual([]);
    });

    it("ignores a lookup that finishes after the URL has moved on", async () => {
        await openAt("a.glb");
        const slow = gate("b.glb");

        await move(`${FILE_ROUTE}/b.glb`);
        await move(`${FILE_ROUTE}/c.glb`);
        await settle();
        expect(viewer()).toBe("assetA:assetA/c.glb@v-c.glb");

        await act(async () => slow.open());
        await settle();

        expect(viewer()).toBe("assetA:assetA/c.glb@v-c.glb");
        expect(lastUrl()).toBe(`${FILE_ROUTE}/c.glb?version=v-c.glb`);
    });

    it("shows the other file when it is opened with its details in navigation state", async () => {
        await openAt("a.glb");

        await move(`${FILE_ROUTE}/b.glb`, {
            state: { filename: "b.glb", key: "assetA/b.glb", versionId: "v-b.glb" },
        });
        await settle();

        expect(viewer()).toBe("assetA:assetA/b.glb@v-b.glb");
        expect(lastUrl()).toBe(`${FILE_ROUTE}/b.glb?version=v-b.glb`);
    });

    it("keeps the page when only the version of the open file changes", async () => {
        await openAt("a.glb");
        const assetReads = fetchAsset.mock.calls.length;

        await move(`${FILE_ROUTE}/a.glb?version=v-old`, {
            state: { filename: "a.glb", key: "assetA/a.glb", versionId: "v-old" },
        });
        await settle();

        expect(viewer()).toBe("assetA:assetA/a.glb@v-old");
        expect(lastUrl()).toBe(`${FILE_ROUTE}/a.glb?version=v-old`);
        expect(fetchAsset.mock.calls.length).toBe(assetReads);
    });
});

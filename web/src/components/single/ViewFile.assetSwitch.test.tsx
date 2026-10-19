/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The file page starts fresh when the URL moves to another asset.
 *
 * React Router keeps a route's element mounted when only its params change, so opening asset B's
 * file link in a tab showing asset A's file re-rendered the same page instance. Asset A's loaded file
 * survived into asset B's render, and the URL-sync effect rewrote the address to asset A's file key,
 * which the page then requested from asset B's fileInfo route and reported as "File Not Found".
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";

let mockLocation: { pathname: string; search: string; state: unknown };
let mockParams: { databaseId: string; assetId: string };
const mockNavigate = jest.fn();

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useLocation: () => mockLocation,
    useNavigate: () => mockNavigate,
    useParams: () => mockParams,
}));

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
        async ({ assetId, fileKey }: { databaseId: string; assetId: string; fileKey: string }) => [
            true,
            {
                key: `${assetId}/${fileKey.replace(/^\//, "")}`,
                fileName: fileKey.split("/").pop(),
                versions: [{ versionId: `v-${assetId}`, isLatest: true }],
            },
        ]
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
    default: (props: { files: { key: string }[]; assetId: string }) => (
        <div data-testid="viewer">{`${props.assetId}:${props.files[0]?.key}`}</div>
    ),
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const ViewFile = require("./ViewFile").default;
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { fetchFileInfo } = require("../../services/APIService");

/** A pasted link: no navigation state, the file path in the URL. */
const openLink = (databaseId: string, assetId: string, file: string) => {
    mockParams = { databaseId, assetId };
    mockLocation = {
        pathname: `/databases/${databaseId}/assets/${assetId}/file/${file}`,
        search: "",
        state: undefined,
    };
};

beforeEach(() => {
    jest.clearAllMocks();
});

describe("ViewFile asset switch", () => {
    it("loads the new asset's file when the URL moves to another asset", async () => {
        // The page's replace-navigations feed back into the location, as the router does.
        let rerenderPage: () => void = () => {};
        const Harness = () => {
            const [, setTick] = React.useState(0);
            rerenderPage = () => setTick((t) => t + 1);
            return <ViewFile />;
        };
        mockNavigate.mockImplementation((url: string) => {
            const [pathname, search = ""] = url.split("?");
            mockLocation = { pathname, search: search ? `?${search}` : "", state: undefined };
            rerenderPage();
        });

        openLink("db1", "assetA", "a.glb");
        render(<Harness />);
        await waitFor(() =>
            expect(screen.getByTestId("viewer")).toHaveTextContent("assetA:assetA/a.glb")
        );

        await act(async () => {
            openLink("db2", "assetB", "b.glb");
            rerenderPage();
        });
        await waitFor(() =>
            expect(screen.getByTestId("viewer")).toHaveTextContent("assetB:assetB/b.glb")
        );
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 50));
        });

        // The page reads the file again once it has added ?version= to the URL, so asset B may be
        // read more than once; what matters is that every read names asset B's own file.
        const assetBLookups = fetchFileInfo.mock.calls
            .map(([args]: [{ assetId: string; fileKey: string }]) => args)
            .filter((args: { assetId: string }) => args.assetId === "assetB");
        expect(assetBLookups.length).toBeGreaterThan(0);
        expect(new Set(assetBLookups.map((args: { fileKey: string }) => args.fileKey))).toEqual(
            new Set(["b.glb"])
        );
        expect(screen.getByTestId("viewer")).toHaveTextContent("assetB:assetB/b.glb");
        mockNavigate.mockReset();
    });

    it("never rewrites the new asset's URL to the previous asset's file", async () => {
        openLink("db1", "assetA", "a.glb");
        const { rerender } = render(<ViewFile />);
        await waitFor(() =>
            expect(screen.getByTestId("viewer")).toHaveTextContent("assetA:assetA/a.glb")
        );
        mockNavigate.mockClear();

        openLink("db2", "assetB", "b.glb");
        rerender(<ViewFile />);
        await waitFor(() =>
            expect(screen.getByTestId("viewer")).toHaveTextContent("assetB:assetB/b.glb")
        );

        const targets = mockNavigate.mock.calls.map(([url]: [string]) => url);
        expect(targets.length).toBeGreaterThan(0);
        expect(
            targets.every((url: string) =>
                url.startsWith("/databases/db2/assets/assetB/file/b.glb")
            )
        ).toBe(true);
    });
});

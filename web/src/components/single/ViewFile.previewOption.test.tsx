/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The file page offers Preview whenever the file it holds has a preview file, whichever of the file
 * read and the asset details answers first. The choices used to be built once, when the asset
 * details arrived, from the file known at that moment; a direct link whose file read answered first
 * (the asset details were slower) had no preview file yet, so Preview was missing for good.
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

let mockLocation: { pathname: string; search: string; state: unknown };

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useLocation: () => mockLocation,
    useNavigate: () => jest.fn(),
    useParams: () => ({ databaseId: "db1", assetId: "asset1" }),
}));

const mockAsset = {
    assetId: "asset1",
    databaseId: "db1",
    assetName: "Seeded Asset",
    isDistributable: true,
};
jest.mock("../../services/APIService", () => ({
    fetchAsset: jest.fn(),
    fetchFileInfo: jest.fn(),
}));
jest.mock("../../services/FileOperationsService", () => ({ archiveFile: jest.fn() }));
jest.mock("../metadata/FileMetadata", () => ({ __esModule: true, default: () => null }));

const mockTables: { previewFile?: string }[] = [];
jest.mock("../filemanager/components/FileVersionsTable", () => ({
    FileVersionsTable: (props: { previewFile?: string }) => {
        mockTables.push({ previewFile: props.previewFile });
        return null;
    },
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
    default: (props: { files: { key: string }[] }) => (
        <div data-testid="viewer">{props.files[0]?.key}</div>
    ),
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const ViewFile = require("./ViewFile").default;
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require("../../services/APIService");

const fileInfoAnswer = (previewFile: string | undefined) => [
    true,
    {
        key: "asset1/a.glb",
        fileName: "a.glb",
        previewFile,
        versions: [
            { versionId: "v2", isLatest: true },
            { versionId: "v1", isLatest: false },
        ],
    },
];

/** Resolves the asset details after `ms`, and the file read at once: the file answers first. */
const assetDetailsAfter = (ms: number) =>
    api.fetchAsset.mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve(mockAsset), ms))
    );

const direct = (search = "") => {
    mockLocation = {
        pathname: "/databases/db1/assets/asset1/file/asset1%2Fa.glb",
        search,
        state: undefined,
    };
};

const NOTICE = /This preview belongs to the latest version of the file/;

beforeEach(() => {
    mockTables.length = 0;
    api.fetchAsset.mockReset();
    api.fetchFileInfo.mockReset();
});

describe("ViewFile Preview option", () => {
    it("offers Preview, before File, when the file read answers before the asset details", async () => {
        api.fetchFileInfo.mockResolvedValue(fileInfoAnswer("asset1/a.glb.previewFile.gif"));
        assetDetailsAfter(300);
        direct();
        render(<ViewFile />);

        const preview = await screen.findByText("Preview", {}, { timeout: 3000 });
        const file = screen.getAllByText("File")[0];
        expect(
            preview.compareDocumentPosition(file) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
    });

    it("offers Preview when the asset details answer first", async () => {
        api.fetchFileInfo.mockImplementation(
            () =>
                new Promise((resolve) =>
                    setTimeout(() => resolve(fileInfoAnswer("asset1/a.glb.previewFile.gif")), 300)
                )
        );
        api.fetchAsset.mockResolvedValue(mockAsset);
        direct();
        render(<ViewFile />);
        expect(await screen.findByText("Preview", {}, { timeout: 3000 })).toBeInTheDocument();
    });

    it("offers no Preview for a file without a preview file, and starts on File", async () => {
        api.fetchFileInfo.mockResolvedValue(fileInfoAnswer(undefined));
        assetDetailsAfter(100);
        direct();
        render(<ViewFile />);
        await waitFor(() => expect(screen.getByTestId("viewer")).toBeInTheDocument());
        await act(async () => {
            await new Promise((r) => setTimeout(r, 200));
        });
        expect(screen.queryByText("Preview")).not.toBeInTheDocument();
        expect(screen.getByTestId("viewer")).toHaveTextContent("asset1/a.glb");
    });

    it("keeps the tab the user chose when the options are built again", async () => {
        api.fetchFileInfo.mockResolvedValue(fileInfoAnswer("asset1/a.glb.previewFile.gif"));
        assetDetailsAfter(50);
        direct();
        render(<ViewFile />);
        const tab = await screen.findByText("Preview", {}, { timeout: 3000 });
        await act(async () => {
            fireEvent.click(tab);
        });
        await waitFor(() =>
            expect(screen.getByTestId("viewer")).toHaveTextContent("a.glb.previewFile.gif")
        );
    });

    it("shows the latest preview with the older-version notice for a link to an older version", async () => {
        api.fetchFileInfo.mockResolvedValue(fileInfoAnswer("asset1/a.glb.previewFile.gif"));
        assetDetailsAfter(50);
        direct("?version=v1");
        render(<ViewFile />);
        const tab = await screen.findByText("Preview", {}, { timeout: 3000 });
        await act(async () => {
            fireEvent.click(tab);
        });
        expect(await screen.findByText(NOTICE)).toBeInTheDocument();
    });

    it("gives the versions list the preview file, so a version it opens keeps the Preview option", async () => {
        api.fetchFileInfo.mockResolvedValue(fileInfoAnswer("asset1/a.glb.previewFile.gif"));
        assetDetailsAfter(50);
        direct();
        render(<ViewFile />);
        await screen.findByText("Preview", {}, { timeout: 3000 });
        await waitFor(() =>
            expect(mockTables[mockTables.length - 1]?.previewFile).toBe(
                "asset1/a.glb.previewFile.gif"
            )
        );
    });
});

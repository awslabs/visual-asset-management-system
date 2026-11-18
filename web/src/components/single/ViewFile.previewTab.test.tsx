/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The file page's Preview tab shows the preview file, a separate S3 object beside the source file.
 * The viewer must request it without the source file's S3 version id, which does not exist for the
 * preview object (the download is refused).
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

jest.mock("../../services/APIService", () => ({
    fetchAssetWithStatus: jest.fn().mockResolvedValue([
        true,
        {
            assetId: "asset1",
            databaseId: "db1",
            assetName: "Seeded Asset",
            isDistributable: true,
        },
    ]),
    fetchFileInfo: jest.fn(),
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

const mockFiles: { key: string; versionId?: string }[][] = [];
jest.mock("../../visualizerPlugin/components/DynamicViewer", () => {
    const MockDynamicViewer = (props: { files: { key: string; versionId?: string }[] }) => {
        mockFiles.push(props.files);
        return <div data-testid="viewer">{props.files[0]?.key}</div>;
    };
    return { __esModule: true, default: MockDynamicViewer };
});

// eslint-disable-next-line @typescript-eslint/no-var-requires
const ViewFile = require("./ViewFile").default;

const state = (assetVersionId?: string) => ({
    filename: "a.glb",
    key: "asset1/a.glb",
    isDirectory: false,
    versionId: "source-v1",
    assetVersionId,
    previewFile: "asset1/a.glb.previewFile.gif",
});

const lastFile = () => mockFiles[mockFiles.length - 1][0];

const open = async (assetVersionId?: string) => {
    mockLocation = {
        pathname: "/databases/db1/assets/asset1/file",
        search: "",
        state: state(assetVersionId),
    };
    render(<ViewFile />);
    await waitFor(() => expect(screen.getByTestId("viewer")).toBeInTheDocument());
    const tab = await screen.findByText("Preview");
    await act(async () => {
        fireEvent.click(tab);
    });
};

beforeEach(() => {
    mockFiles.length = 0;
});

describe("ViewFile preview tab", () => {
    it("requests the preview file without the source file's version id", async () => {
        await open();
        await waitFor(() => expect(lastFile().key).toBe("asset1/a.glb.previewFile.gif"));
        expect(lastFile().versionId).toBeUndefined();
    });

    it("keeps the source file's version id on the File tab", async () => {
        await open();
        await act(async () => {
            fireEvent.click(screen.getByText("File"));
        });
        await waitFor(() => expect(lastFile().key).toBe("asset1/a.glb"));
        expect(lastFile().versionId).toBe("source-v1");
    });
});

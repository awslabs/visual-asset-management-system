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

const state = (assetVersionId?: string, versionId = "source-v1") => ({
    filename: "a.glb",
    key: "asset1/a.glb",
    isDirectory: false,
    versionId,
    assetVersionId,
    previewFile: "asset1/a.glb.previewFile.gif",
});

const NOTICE = "This preview belongs to the latest version of the file";

/** Answers the page's file-info lookup: `source-v2` is the latest version. */
const answerVersions = () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { fetchFileInfo } = require("../../services/APIService");
    fetchFileInfo.mockResolvedValue([
        true,
        {
            versions: [
                { versionId: "source-v2", isLatest: true },
                { versionId: "source-v1", isLatest: false },
            ],
        },
    ]);
};

const lastFile = () => mockFiles[mockFiles.length - 1][0];

const open = async (assetVersionId?: string, versionId?: string) => {
    mockLocation = {
        pathname: "/databases/db1/assets/asset1/file",
        search: "",
        state: state(assetVersionId, versionId),
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
    answerVersions();
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

    it("says the preview belongs to the latest version when an older file version is viewed", async () => {
        await open(undefined, "source-v1");
        expect(await screen.findByText(new RegExp(NOTICE))).toBeInTheDocument();
        // The request is unchanged: the preview file, no version id.
        expect(lastFile().key).toBe("asset1/a.glb.previewFile.gif");
        expect(lastFile().versionId).toBeUndefined();
    });

    it("shows no notice for the latest file version", async () => {
        const { fetchFileInfo } = require("../../services/APIService");
        await open(undefined, "source-v2");
        await waitFor(() => expect(fetchFileInfo).toHaveBeenCalled());
        await act(async () => {
            await Promise.resolve();
        });
        expect(screen.queryByText(new RegExp(NOTICE))).not.toBeInTheDocument();
    });

    it("shows no notice on the File tab of an older version", async () => {
        await open(undefined, "source-v1");
        await screen.findByText(new RegExp(NOTICE));
        await act(async () => {
            fireEvent.click(screen.getByText("File"));
        });
        expect(screen.queryByText(new RegExp(NOTICE))).not.toBeInTheDocument();
    });

    it("shows no notice when viewing through an asset version", async () => {
        await open("3", "source-v1");
        await act(async () => {
            await Promise.resolve();
        });
        expect(screen.queryByText(new RegExp(NOTICE))).not.toBeInTheDocument();
    });
});

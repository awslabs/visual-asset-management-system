/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * "View v1" and "View Current" open the file page with the snapshot file's path in the asset (#1943).
 *
 * A snapshot names a file by its path without the leading "/". The API reads a value without one as the
 * file's full key when the asset holds it, so the snapshot path "A1/cloud.las" of an asset stored at "A1/"
 * named the root file "A1/cloud.las" instead of the file in the subfolder "A1/". The file page is now
 * handed the path with its leading "/".
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import AssetVersionComparison from "./AssetVersionComparison";
import { fetchAssetVersion } from "../../../services/AssetVersionService";
import { fetchAssetS3Files } from "../../../services/APIService";

jest.mock("../../../services/AssetVersionService", () => ({
    fetchAssetVersion: jest.fn(),
    compareAssetVersions: jest.fn(),
}));
jest.mock("../../../services/APIService", () => ({ fetchAssetS3Files: jest.fn() }));
jest.mock("./AssetVersionManager", () => {
    const { createContext } = jest.requireActual("react");
    return { AssetVersionContext: createContext(null) };
});
jest.mock("../../filemanager/modals/FileViewerModal", () => () => null);
jest.mock("../../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => false,
}));
jest.mock("../../../visualizerPlugin/core/viewableExtensions", () => ({
    isFilenameComparableAsVersions: () => false,
}));

const version = {
    Version: "1",
    DateModified: "2026-01-02T00:00:00Z",
    Comment: "first",
    description: "",
    specifiedPipelines: [],
    createdBy: "user",
    isCurrent: false,
    fileCount: 1,
} as any;

const FilePageProbe: React.FC = () => {
    const location = useLocation();
    return <div data-testid="file-page">{(location.state as any)?.key}</div>;
};

function renderComparison() {
    (fetchAssetVersion as jest.Mock).mockResolvedValue([
        true,
        {
            assetId: "A1",
            assetVersionId: "1",
            dateCreated: "2026-01-02T00:00:00Z",
            files: [
                {
                    relativeKey: "A1/cloud.las",
                    versionId: "v-sub",
                    isArchived: false,
                    exists: true,
                    size: 10,
                    lastModified: "2026-01-02T00:00:00Z",
                    etag: "e1",
                },
            ],
        },
    ]);
    (fetchAssetS3Files as jest.Mock).mockResolvedValue([
        true,
        [
            {
                fileName: "cloud.las",
                key: "A1/A1/cloud.las",
                relativePath: "/A1/cloud.las",
                isFolder: false,
                size: 10,
                dateCreatedCurrentVersion: "2026-01-03T00:00:00Z",
                versionId: "cur",
                etag: "e1",
                isArchived: false,
            },
        ],
    ]);
    render(
        <MemoryRouter initialEntries={["/compare"]}>
            <Routes>
                <Route
                    path="/compare"
                    element={
                        <AssetVersionComparison
                            databaseId="db-1"
                            assetId="A1"
                            version1={version}
                            compareWithCurrent={true}
                            onClose={() => undefined}
                        />
                    }
                />
                <Route
                    path="/databases/:databaseId/assets/:assetId/file"
                    element={<FilePageProbe />}
                />
            </Routes>
        </MemoryRouter>
    );
}

describe("AssetVersionComparison: View opens the file by its path in the asset", () => {
    beforeEach(() => jest.clearAllMocks());

    it.each([["View v1"], ["View Current"]])("%s names the subfolder file", async (label) => {
        renderComparison();
        await userEvent.click(await screen.findByRole("button", { name: label }));
        expect(await screen.findByTestId("file-page")).toHaveTextContent("/A1/cloud.las");
        expect(screen.getByTestId("file-page").textContent).toBe("/A1/cloud.las");
    });
});

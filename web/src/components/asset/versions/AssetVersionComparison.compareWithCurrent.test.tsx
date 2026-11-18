/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Compare with Current Files matches each file of the version snapshot to the asset's current file at
 * the same path. The list-files API names a current file by `relativePath` (with a leading "/") and
 * also lists folders; the snapshot names a file by `relativeKey` (no leading "/") and lists no folders.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
    fileCount: 2,
} as any;

const snapshotFile = (relativeKey: string, etag: string) => ({
    relativeKey,
    versionId: `v-${relativeKey}`,
    isArchived: false,
    exists: true,
    size: 10,
    lastModified: "2026-01-02T00:00:00Z",
    etag,
});

const currentItem = (relativePath: string, etag: string, isFolder = false) => ({
    fileName: relativePath.split("/").pop(),
    key: `asset-1${relativePath}`,
    relativePath,
    isFolder,
    size: isFolder ? undefined : 10,
    dateCreatedCurrentVersion: "2026-01-03T00:00:00Z",
    versionId: isFolder ? undefined : `cur-${relativePath}`,
    etag: isFolder ? undefined : etag,
    isArchived: false,
});

/** The summary line `<strong>{label}:</strong> {count}` (Cloudscape may wrap it in another div). */
const summaryLine = (label: string, count: number) =>
    screen.getAllByText(
        (_content, element) =>
            element?.tagName === "DIV" &&
            element.textContent?.replace(/^\W*/, "").trim() === `${label}: ${count}`
    )[0];

describe("AssetVersionComparison: compare with current files", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("matches snapshot files to current files by their path, ignoring folders", async () => {
        (fetchAssetVersion as jest.Mock).mockResolvedValue([
            true,
            {
                assetId: "asset-1",
                assetVersionId: "1",
                dateCreated: "2026-01-02T00:00:00Z",
                files: [snapshotFile("model.glb", "e1"), snapshotFile("textures/a.png", "e2")],
            },
        ]);
        (fetchAssetS3Files as jest.Mock).mockResolvedValue([
            true,
            [
                currentItem("/", "", true),
                currentItem("/textures/", "", true),
                currentItem("/model.glb", "e1"),
                currentItem("/textures/a.png", "e2-changed"),
                currentItem("/notes.txt", "e3"),
            ],
        ]);

        render(
            <MemoryRouter>
                <AssetVersionComparison
                    databaseId="db-1"
                    assetId="asset-1"
                    version1={version}
                    compareWithCurrent={true}
                    onClose={() => undefined}
                />
            </MemoryRouter>
        );

        await waitFor(() => expect(summaryLine("Total Files", 3)).toBeInTheDocument());
        expect(summaryLine("Unchanged", 1)).toBeInTheDocument();
        expect(summaryLine("Modified", 1)).toBeInTheDocument();
        expect(summaryLine("Added", 1)).toBeInTheDocument();
        expect(summaryLine("Removed", 0)).toBeInTheDocument();
    });

    it("matches a snapshot file whose key keeps a leading slash", async () => {
        (fetchAssetVersion as jest.Mock).mockResolvedValue([
            true,
            {
                assetId: "asset-1",
                assetVersionId: "1",
                dateCreated: "2026-01-02T00:00:00Z",
                files: [snapshotFile("/model.glb", "e1")],
            },
        ]);
        (fetchAssetS3Files as jest.Mock).mockResolvedValue([
            true,
            [currentItem("/", "", true), currentItem("/model.glb", "e1")],
        ]);

        render(
            <MemoryRouter>
                <AssetVersionComparison
                    databaseId="db-1"
                    assetId="asset-1"
                    version1={version}
                    compareWithCurrent={true}
                    onClose={() => undefined}
                />
            </MemoryRouter>
        );

        await waitFor(() => expect(summaryLine("Total Files", 1)).toBeInTheDocument());
        expect(summaryLine("Unchanged", 1)).toBeInTheDocument();
        expect(summaryLine("Removed", 0)).toBeInTheDocument();
    });
});

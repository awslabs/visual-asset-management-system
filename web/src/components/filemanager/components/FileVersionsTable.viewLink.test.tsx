/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A version's "View" link names the file the way the hosting page does (#1943).
 *
 * The API reads a file path with a leading "/" as the file's path in the asset, and a value without one as
 * the file's full key when the asset holds it. The link used to cut a leading "{assetId}/" segment off the
 * key, which for the file "A1/A1/cloud.las" of an asset stored at "A1/" gave "A1/cloud.las": the API read
 * that as the root file's key. The link now carries the hosting page's path, or the key as it is.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { FileVersionsTable } from "./FileVersionsTable";

jest.mock("../../../services/APIService", () => ({
    downloadAsset: jest.fn(),
    revertFileVersion: jest.fn(),
}));
jest.mock("../../../services/AssetVersionService", () => ({
    fetchFileVersions: jest.fn(),
}));
jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const { fetchFileVersions } = require("../../../services/AssetVersionService");

const version = (versionId: string, isLatest: boolean) => ({
    versionId,
    lastModified: "2026-09-12T15:30:18+00:00",
    size: 10,
    isLatest,
    storageClass: "STANDARD",
    isArchived: false,
    assetVersionIds: [],
    changeSource: "upload",
    changeUserId: "alice",
});

/** Stands in for the file page, so the link itself is what gets asserted. */
const FilePageProbe: React.FC = () => {
    const location = useLocation();
    return (
        <div data-testid="file-page">
            {JSON.stringify({ pathname: location.pathname, key: (location.state as any)?.key })}
        </div>
    );
};

function renderTable(filePath: string, linkFilePath?: string) {
    fetchFileVersions.mockResolvedValue([
        true,
        { versions: [version("v-current", true), version("v-old", false)] },
    ]);
    return render(
        <MemoryRouter initialEntries={["/databases/db1/assets/A1"]}>
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId"
                    element={
                        <FileVersionsTable
                            databaseId="db1"
                            assetId="A1"
                            filePath={filePath}
                            linkFilePath={linkFilePath}
                            fileName="cloud.las"
                            currentVersionId="v-current"
                            displayMode="container"
                            visible
                        />
                    }
                />
                <Route
                    path="/databases/:databaseId/assets/:assetId/file/*"
                    element={<FilePageProbe />}
                />
            </Routes>
        </MemoryRouter>
    );
}

async function openOlderVersion() {
    await userEvent.click(await screen.findByText("View"));
    return JSON.parse((await screen.findByTestId("file-page")).textContent || "{}");
}

describe("FileVersionsTable — the View link names the file as the hosting page does", () => {
    beforeEach(() => jest.clearAllMocks());

    it("carries the hosting page's path in the asset with its leading slash", async () => {
        renderTable("A1/A1/cloud.las", "/A1/cloud.las");
        const page = await openOlderVersion();
        expect(page.pathname).toBe("/databases/db1/assets/A1/file/%2FA1%2Fcloud.las");
        expect(page.key).toBe("A1/A1/cloud.las");
    });

    it("carries the full key as it is when the host names no path, never a key with its first segment cut", async () => {
        renderTable("A1/A1/cloud.las");
        const page = await openOlderVersion();
        expect(page.pathname).toBe("/databases/db1/assets/A1/file/A1%2FA1%2Fcloud.las");
    });
});

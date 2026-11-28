/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * #870: on an archived asset's page a file's versions are read with showArchived and listed
 * without their Download, View and Revert actions, which need the asset unarchived.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { FileVersionsTable } from "./FileVersionsTable";
import { ArchivedAssetContext } from "../../../context/ArchivedAssetContext";

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

const versions = [
    { versionId: "v2", lastModified: "2026-10-02T10:00:00Z", size: 10, isLatest: true },
    { versionId: "v1", lastModified: "2026-10-01T10:00:00Z", size: 9, isLatest: false },
];

function renderTable(archived: boolean) {
    fetchFileVersions.mockResolvedValue([true, { versions }]);
    return render(
        <MemoryRouter>
            <ArchivedAssetContext.Provider value={archived}>
                <FileVersionsTable
                    databaseId="db1"
                    assetId="a1"
                    filePath="/a.txt"
                    fileName="a.txt"
                    currentVersionId="v2"
                    displayMode="modal"
                    visible
                />
            </ArchivedAssetContext.Provider>
        </MemoryRouter>
    );
}

describe("FileVersionsTable on an archived asset", () => {
    beforeEach(() => jest.clearAllMocks());

    it("reads the versions with showArchived and offers no actions", async () => {
        renderTable(true);
        expect(await screen.findByText("v1", { exact: false })).toBeInTheDocument();
        expect(fetchFileVersions).toHaveBeenCalledWith(
            expect.objectContaining({ filePath: "/a.txt", showArchived: true })
        );
        expect(screen.queryByRole("button", { name: "Download" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Revert To" })).not.toBeInTheDocument();
    });

    it("a live asset's versions keep their actions", async () => {
        renderTable(false);
        expect((await screen.findAllByRole("button", { name: "Download" })).length).toBe(2);
        expect(screen.getByRole("button", { name: "Revert To" })).toBeInTheDocument();
        expect(fetchFileVersions).toHaveBeenCalledWith(
            expect.objectContaining({ showArchived: false })
        );
    });
});

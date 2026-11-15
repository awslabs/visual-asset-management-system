/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * #870: the read services of an asset's page send showArchived=true only when asked, so an
 * archived asset's files, versions, metadata, links and comments are found, and a live asset's
 * requests are unchanged.
 */

import {
    fetchAllComments,
    fetchAssetLinks,
    fetchAssetS3FilesPage,
    fetchFileInfo,
} from "./APIService";
import { fetchAssetVersion, fetchFileVersions } from "./AssetVersionService";
import { fetchMetadata } from "./MetadataService";
import { apiClient } from "./apiClient";

jest.mock("./apiClient", () => ({
    ApiError: class extends Error {},
    apiClient: { get: jest.fn() },
}));

const mockGet = apiClient.get as jest.Mock;

/** The query string parameters of every GET made, in order. */
const sentQueries = () => mockGet.mock.calls.map((call) => call[1]?.queryStringParameters);

describe("archived asset read services", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => {});
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    describe.each([
        [
            "listFiles",
            (showArchived?: boolean) =>
                fetchAssetS3FilesPage({ databaseId: "db1", assetId: "a1", showArchived }),
            { items: [] },
        ],
        [
            "fileInfo",
            (showArchived?: boolean) =>
                fetchFileInfo({
                    databaseId: "db1",
                    assetId: "a1",
                    fileKey: "/a.txt",
                    showArchived,
                }),
            { message: {} },
        ],
        [
            "file versions",
            (showArchived?: boolean) =>
                fetchFileVersions({
                    databaseId: "db1",
                    assetId: "a1",
                    filePath: "/a.txt",
                    showArchived,
                }),
            { versions: [] },
        ],
        [
            "asset links",
            (showArchived?: boolean) =>
                fetchAssetLinks({ databaseId: "db1", assetId: "a1", showArchived }),
            { related: [], parents: [], children: [] },
        ],
        [
            "asset metadata",
            (showArchived?: boolean) =>
                fetchMetadata("asset", "a1", "db1", undefined, undefined, undefined, showArchived),
            { metadata: [] },
        ],
        [
            "file metadata",
            (showArchived?: boolean) =>
                fetchMetadata("file", "a1", "db1", "/a.txt", "metadata", undefined, showArchived),
            { metadata: [] },
        ],
        [
            "asset link metadata",
            (showArchived?: boolean) =>
                fetchMetadata(
                    "assetLink",
                    "link-1",
                    undefined,
                    undefined,
                    undefined,
                    undefined,
                    showArchived
                ),
            { metadata: [] },
        ],
    ])("%s", (_label, call, reply) => {
        it("sends showArchived=true when asked", async () => {
            mockGet.mockResolvedValue(reply);
            await call(true);
            expect(sentQueries()[0]).toEqual(expect.objectContaining({ showArchived: "true" }));
        });

        it("sends no showArchived for a live asset", async () => {
            mockGet.mockResolvedValue(reply);
            await call(undefined);
            expect(sentQueries()[0]?.showArchived).toBeUndefined();
        });
    });

    it("a database's metadata never carries showArchived", async () => {
        mockGet.mockResolvedValue({ metadata: [] });
        await fetchMetadata("database", "db1", undefined, undefined, undefined, undefined, true);
        expect(sentQueries()[0]?.showArchived).toBeUndefined();
    });

    it("a version's details are read with showArchived only when asked", async () => {
        mockGet.mockResolvedValue({ message: {} });
        await fetchAssetVersion({ databaseId: "db1", assetId: "a1", assetVersionId: "1" });
        await fetchAssetVersion({
            databaseId: "db1",
            assetId: "a1",
            assetVersionId: "1",
            showArchived: true,
        });
        expect(mockGet.mock.calls[0][1]).toEqual({});
        expect(sentQueries()[1]).toEqual({ showArchived: "true" });
    });

    it("every page of an archived asset's comments carries showArchived", async () => {
        mockGet
            .mockResolvedValueOnce({ message: { Items: [{ c: 1 }], NextToken: "t1" } })
            .mockResolvedValueOnce({ message: { Items: [{ c: 2 }] } });
        await fetchAllComments({ assetId: "a1", showArchived: true });
        expect(mockGet).toHaveBeenCalledTimes(2);
        for (const query of sentQueries()) {
            expect(query).toEqual(expect.objectContaining({ showArchived: "true" }));
        }
    });

    it("a live asset's comments are read as before", async () => {
        mockGet.mockResolvedValueOnce({ message: { Items: [{ c: 1 }] } });
        await fetchAllComments({ assetId: "a1" });
        expect(mockGet.mock.calls[0][1]).toEqual({});
    });
});

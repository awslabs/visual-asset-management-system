/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `listFileVersions` returns the S3 OBJECT versions of one file.
 *
 * This is not interchangeable with the asset-version list it replaced. An execution's `versionId` is
 * passed to `head_object(VersionId=...)` in `executeWorkflow._input_exists_in_s3`, so a VAMS asset
 * version id fails the pre-launch existence check, and an asset-scoped list shows identical options
 * for every file in the asset. The dimension is the contract, which is why it is asserted here rather
 * than only through the component.
 */

import { listFileVersions } from "./assets";

jest.mock("../../../services/APIService", () => ({
    searchAssets: jest.fn(),
    fetchAllAssets: jest.fn(),
    fetchDatabaseAssets: jest.fn(),
    fetchAssetS3Files: jest.fn(),
    fetchFileInfo: jest.fn(),
}));
jest.mock("../../../services/appCache", () => ({ appCache: { getItem: jest.fn() } }));

const api = () => require("../../../services/APIService");

/** The GET /fileInfo?includeVersions=true envelope (FileInfoResponseModel in models/assetsV3.py). */
const fileInfo = (versions: any[]) => [
    true,
    {
        fileName: "pump.glb",
        key: "/pump.glb",
        relativePath: "/pump.glb",
        isFolder: false,
        lastModified: "2026-07-30T00:00:00Z",
        versions,
    },
];

describe("listFileVersions", () => {
    beforeEach(() => jest.clearAllMocks());

    it("asks the file-scoped endpoint for versions, keyed on the file path", async () => {
        api().fetchFileInfo.mockResolvedValue(fileInfo([]));
        await listFileVersions("db1", "a1", "/models/pump.glb");
        // includeVersions must be requested explicitly — the endpoint omits the version list otherwise
        // and the selector would silently offer only "Latest".
        expect(api().fetchFileInfo).toHaveBeenCalledWith({
            databaseId: "db1",
            assetId: "a1",
            fileKey: "/models/pump.glb",
            includeVersions: true,
        });
    });

    it("returns the S3 version ids, newest first as the server ordered them", async () => {
        api().fetchFileInfo.mockResolvedValue(
            fileInfo([
                { versionId: "v3", isLatest: true, lastModified: "2026-07-30", size: 10 },
                { versionId: "v1", isLatest: false, lastModified: "2026-07-01", size: 9 },
            ])
        );
        const [ok, versions] = await listFileVersions("db1", "a1", "/pump.glb");
        expect(ok).toBe(true);
        expect((versions as any[]).map((v) => v.versionId)).toEqual(["v3", "v1"]);
        expect((versions as any[])[0].isLatest).toBe(true);
    });

    it("tags each entry with the file it belongs to", async () => {
        // The rows in a multi-file selection each hold their own list; carrying the key makes a
        // mismatched pairing detectable rather than silent.
        api().fetchFileInfo.mockResolvedValue(fileInfo([{ versionId: "v1", isLatest: true }]));
        const [, versions] = await listFileVersions("db1", "a1", "/models/pump.glb");
        expect((versions as any[])[0].relativeKey).toBe("/models/pump.glb");
    });

    it("drops delete markers", async () => {
        // A delete marker is a version id that holds no bytes; selecting one would name a version
        // head_object reports as gone.
        api().fetchFileInfo.mockResolvedValue(
            fileInfo([
                { versionId: "v-live", isLatest: true, isArchived: false },
                { versionId: "v-marker", isLatest: false, isArchived: true },
            ])
        );
        const [, versions] = await listFileVersions("db1", "a1", "/pump.glb");
        expect((versions as any[]).map((v) => v.versionId)).toEqual(["v-live"]);
    });

    it("drops entries with no version id", async () => {
        api().fetchFileInfo.mockResolvedValue(fileInfo([{ versionId: "" }, { versionId: "v1" }]));
        const [, versions] = await listFileVersions("db1", "a1", "/pump.glb");
        expect((versions as any[]).map((v) => v.versionId)).toEqual(["v1"]);
    });

    it("treats a response with no version list as no versions, not an error", async () => {
        // A bucket without versioning enabled returns the file with `versions` absent. That is a
        // legitimate state — "Latest" remains a valid choice — so it must not surface as a failure.
        api().fetchFileInfo.mockResolvedValue([
            true,
            { fileName: "pump.glb", relativePath: "/pump.glb", isFolder: false },
        ]);
        const [ok, versions] = await listFileVersions("db1", "a1", "/pump.glb");
        expect(ok).toBe(true);
        expect(versions).toEqual([]);
    });

    it("reports a failed lookup rather than presenting it as an empty history", async () => {
        api().fetchFileInfo.mockResolvedValue([false, "Access denied"]);
        const [ok, message] = await listFileVersions("db1", "a1", "/pump.glb");
        expect(ok).toBe(false);
        expect(message).toBe("Access denied");
    });

    it("surfaces a thrown error as a message instead of rejecting", async () => {
        // Callers consume the [ok, data] tuple; a rejection would break the query hook's error path.
        api().fetchFileInfo.mockRejectedValue(new Error("network down"));
        const [ok, message] = await listFileVersions("db1", "a1", "/pump.glb");
        expect(ok).toBe(false);
        expect(message).toBe("network down");
    });
});

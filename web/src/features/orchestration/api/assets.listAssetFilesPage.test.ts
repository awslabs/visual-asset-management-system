/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { listAssetFilesPage } from "./assets";

jest.mock("../../../services/APIService", () => ({
    ...jest.requireActual("../../../services/APIService"),
    fetchAssetS3FilesPage: jest.fn(),
}));

const { fetchAssetS3FilesPage } = jest.requireMock("../../../services/APIService");

describe("listAssetFilesPage", () => {
    beforeEach(() => jest.clearAllMocks());

    it("derives the asset-relative path from the full key when the listing is scoped by a prefix", async () => {
        // Scoped by prefix, the API reports relativePath relative to the PREFIX ("/b000.txt"); the key
        // still carries the asset id and the folder, which is what the picker must send.
        fetchAssetS3FilesPage.mockResolvedValue({
            success: true,
            items: [
                { key: "asset1/bulk/b000.txt", relativePath: "/b000.txt", isFolder: false },
                {
                    key: "asset1/bulk/deeper/b001.txt",
                    relativePath: "/deeper/b001.txt",
                    isFolder: false,
                },
            ],
            nextToken: null,
        });
        const [ok, page] = await listAssetFilesPage("db1", "asset1", { prefix: "/bulk/" });
        expect(ok).toBe(true);
        expect((page as any).items.map((f: any) => f.relativePath)).toEqual([
            "/bulk/b000.txt",
            "/bulk/deeper/b001.txt",
        ]);
        expect(fetchAssetS3FilesPage).toHaveBeenCalledWith(
            expect.objectContaining({ prefix: "/bulk/" })
        );
    });

    it("keeps an unscoped listing's relativePath and drops folders and the asset root", async () => {
        fetchAssetS3FilesPage.mockResolvedValue({
            success: true,
            items: [
                { key: "asset1/a1.txt", relativePath: "/a1.txt", isFolder: false },
                { key: "asset1/docs/", relativePath: "/docs/", isFolder: true },
                { key: "asset1/", relativePath: "/", isFolder: false },
                { key: "other/x.txt", relativePath: "x.txt", isFolder: false },
            ],
            nextToken: "t2",
        });
        const [ok, page] = await listAssetFilesPage("db1", "asset1");
        expect(ok).toBe(true);
        expect((page as any).items.map((f: any) => f.relativePath)).toEqual(["/a1.txt", "/x.txt"]);
        expect((page as any).nextToken).toBe("t2");
    });
});

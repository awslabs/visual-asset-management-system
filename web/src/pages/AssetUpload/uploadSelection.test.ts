/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { fetchExistingPaths } from "./uploadSelection";
import { fetchAssetS3FilesPage } from "../../services/APIService";

jest.mock("../../services/APIService", () => ({ fetchAssetS3FilesPage: jest.fn() }));

const listPage = fetchAssetS3FilesPage as jest.Mock;

describe("fetchExistingPaths", () => {
    beforeEach(() => listPage.mockReset());

    it.each([
        // The destination goes out as its path in the asset; the API drops its one leading "/" (#1977)
        ["/scene/", "/scene/"],
        ["//", "//"],
        ["//sub/", "//sub/"],
    ])("lists under %p with the prefix %p", async (destination, prefix) => {
        listPage.mockResolvedValue({ success: true, items: [] });
        await fetchExistingPaths({ databaseId: "db1", assetId: "a1", destination });
        expect(listPage.mock.calls[0][0].prefix).toBe(prefix);
    });

    it("lists the whole asset for the root destination", async () => {
        listPage.mockResolvedValue({ success: true, items: [] });
        await fetchExistingPaths({ databaseId: "db1", assetId: "a1", destination: "/" });
        expect(listPage.mock.calls[0][0].prefix).toBeNull();
    });

    it("marks each listed file at its own path, an empty segment kept", async () => {
        listPage.mockResolvedValue({
            success: true,
            items: [
                { relativePath: "/a.png", isArchived: false },
                { relativePath: "//b.png", isArchived: true },
                { relativePath: "/sub/", isFolder: true },
            ],
        });
        const result = await fetchExistingPaths({
            databaseId: "db1",
            assetId: "a1",
            destination: "/scene/",
        });
        expect(Array.from(result.paths.entries())).toEqual([
            ["/scene/a.png", "exists"],
            ["/scene//b.png", "archived"],
        ]);
        expect(result.complete).toBe(true);
    });
});

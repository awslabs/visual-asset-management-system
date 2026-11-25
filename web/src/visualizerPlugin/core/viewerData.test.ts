/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Potree and VEERUM viewers build a file's viewer data URL with `auxiliaryPreviewStreamUrl` (#1855).
 * The file is sent by the key the host gives: the API reads a full key and an asset-relative path alike
 * and decides which file of the asset it names, so the viewer never guesses a relative path. Stripping a
 * leading `{assetId}/` missed every asset whose location is not `{assetId}/` (a base prefix, an asset on
 * an existing folder) and named the wrong file in a subfolder named like the asset.
 */
import { auxiliaryPreviewStreamUrl, POTREE_VIEWER_METADATA } from "./viewerData";

const API = "https://vams.example.com/api/";
const STREAM = `${API}database/db1/assets/asset-1/auxiliaryPreviewAssets/stream/`;
const metadataUrl = (key: string) =>
    auxiliaryPreviewStreamUrl(API, "db1", "asset-1", key, POTREE_VIEWER_METADATA);

describe("auxiliaryPreviewStreamUrl", () => {
    it.each([
        ["an {assetId}/ asset", "asset-1/scans/cloud.laz"],
        ["a bucket with a base prefix", "vams/asset-1/scans/cloud.laz"],
        ["an asset on an existing folder", "custom/path/cloud.laz"],
        ["a single-file asset's object", "projects/site.laz"],
        ["a single-file asset's companion file", "projects/site.laz/extra/cloud.laz"],
        ["a subfolder named like the asset", "asset-1/asset-1/cloud.laz"],
    ])("sends the full key of %s as given", (_case, key) => {
        expect(metadataUrl(key)).toBe(`${STREAM}${key}/preview/PotreeViewer/metadata.json`);
    });

    it("sends an asset-relative key from a host without the full key, without its leading slash", () => {
        expect(metadataUrl("/scans/cloud.laz")).toBe(
            `${STREAM}scans/cloud.laz/preview/PotreeViewer/metadata.json`
        );
    });

    it("encodes each segment and keeps the slashes, so sibling files resolve beside metadata.json", () => {
        expect(metadataUrl("asset-1/site scans/#1 cloud?.laz")).toBe(
            `${STREAM}asset-1/site%20scans/%231%20cloud%3F.laz/preview/PotreeViewer/metadata.json`
        );
    });

    it("encodes the asset and database of the file", () => {
        expect(
            auxiliaryPreviewStreamUrl(API, "db 1", "asset/1", "k.laz", POTREE_VIEWER_METADATA)
        ).toBe(
            `${API}database/db%201/assets/asset%2F1/auxiliaryPreviewAssets/stream/` +
                "k.laz/preview/PotreeViewer/metadata.json"
        );
    });

    it("joins any data path under the file's preview folder", () => {
        expect(auxiliaryPreviewStreamUrl(API, "db1", "asset-1", "a.laz", "/r/octree.bin")).toBe(
            `${STREAM}a.laz/preview/r/octree.bin`
        );
    });
});

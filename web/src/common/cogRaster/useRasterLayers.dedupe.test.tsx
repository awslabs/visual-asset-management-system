/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// The same raster reached by two routes (a source that resolves to its COG, and the COG selected
// directly, unpinned or pinned) is listed once. Unlike useRasterLayers.test.tsx this runs the real
// resolver, so a mismatch between what the resolver returns and what the manager compares shows up;
// only the API, the presigned URL and the file bytes are faked (d17-D1).

import { act, renderHook, waitFor } from "@testing-library/react";
import { loadFixtureTiff } from "./fixtures.testdata";

jest.mock("../../services/APIService", () => ({
    fetchFileMetadata: jest.fn(),
    fetchFileInfo: jest.fn(),
    downloadAsset: jest.fn(),
}));
jest.mock("geotiff", () => ({ ...jest.requireActual("geotiff"), fromUrl: jest.fn() }));
jest.mock("./cogProtocol", () => ({ registerCogProtocol: jest.fn().mockResolvedValue(undefined) }));
jest.mock("./presignedUrl", () => ({
    ...jest.requireActual("./presignedUrl"),
    keepFresh: jest.fn(() => ({ stop: jest.fn(), refreshNow: jest.fn() })),
    requestPresignedUrl: jest.fn(),
}));

import { fromUrl } from "geotiff";
import { fetchFileInfo, fetchFileMetadata } from "../../services/APIService";
import { requestPresignedUrl, RasterFileRef } from "./presignedUrl";
import { MapLike } from "./RasterLayerManager";
import { clearRasterAttributeCache } from "./rasterResolver";
import { useRasterLayers } from "./useRasterLayers";

// The file manager names a file by its full stored key (the asset's root key, then the path); the
// COG link recorded on a source is the path inside the asset. The API resolves both to one path.
const ROOT = "xa1b2c3/";
const SOURCE: RasterFileRef = { databaseId: "db", assetId: "a", key: `${ROOT}s.tif` };
const COG_KEY = "/s.tif.cog.tif";
const COG_STORED_KEY = `${ROOT}s.tif.cog.tif`;
const LATEST = "B7Kq93hHxDUx04QeOjJZ5vmFjokD2lHz";
const relativePathOf = (key: string) => `/${key.replace(/^\/+/, "").replace(ROOT, "")}`;

function fakeMap(): MapLike {
    const layers = new Set<string>();
    const sources = new Set<string>();
    return {
        addSource: (id: string) => void sources.add(id),
        removeSource: (id: string) => void sources.delete(id),
        getSource: (id: string) => (sources.has(id) ? { id } : undefined),
        addLayer: (layer: any) => void layers.add(layer.id),
        removeLayer: (id: string) => void layers.delete(id),
        getLayer: (id: string) => (layers.has(id) ? { id } : undefined),
        moveLayer: jest.fn(),
        setPaintProperty: jest.fn(),
        getStyle: () => ({ layers: [] }),
        fitBounds: jest.fn(),
        on: jest.fn(),
        off: jest.fn(),
    };
}

beforeEach(() => {
    jest.clearAllMocks();
    clearRasterAttributeCache();
    (requestPresignedUrl as jest.Mock).mockImplementation(
        async (f: any) => `https://s3${f.key}?X-Amz-Date=20991117T170000Z&X-Amz-Expires=3600`
    );
    (fromUrl as jest.Mock).mockImplementation(async () => loadFixtureTiff("cog-3857.tif"));
    (fetchFileMetadata as jest.Mock).mockImplementation(async ({ filePath }: any) => ({
        metadata:
            relativePathOf(filePath) === relativePathOf(SOURCE.key)
                ? [{ metadataKey: "cog_link_cogFile", metadataValue: COG_KEY }]
                : [],
    }));
    // The API's file-info answer: the file has one version, the latest.
    (fetchFileInfo as jest.Mock).mockImplementation(async ({ fileKey }: any) => [
        true,
        {
            size: 1000,
            relativePath: relativePathOf(fileKey),
            versions: [{ versionId: LATEST, isLatest: true, isArchived: false }],
        },
    ]);
});

async function addAll(files: RasterFileRef[], delays: number[] = []) {
    const map = fakeMap();
    const { result } = renderHook(() => useRasterLayers(map));
    await waitFor(() => expect(result.current.entries).toEqual([]));
    await act(async () => {
        await Promise.all(
            files.map(async (file, index) => {
                if (delays[index]) await new Promise((r) => setTimeout(r, delays[index]));
                return result.current.addFile(file);
            })
        );
    });
    return result;
}

describe("one COG reached by two routes is one layer (d17-D1)", () => {
    const pinned = (versionId: string): RasterFileRef => ({
        databaseId: "db",
        assetId: "a",
        key: COG_STORED_KEY,
        versionId,
    });

    it("source + the COG pinned at its latest version, either order, either timing", async () => {
        const cases: [RasterFileRef[], number[]][] = [
            [[SOURCE, pinned(LATEST)], []],
            [[pinned(LATEST), SOURCE], []],
            [
                [SOURCE, pinned(LATEST)],
                [0, 30],
            ],
            [
                [SOURCE, pinned(LATEST)],
                [30, 0],
            ],
        ];
        for (const [files, delays] of cases) {
            const result = await addAll(files, delays);
            expect(result.current.entries.map((e) => e.id)).toHaveLength(1);
        }
    });

    it("the COG unpinned and pinned at its latest version", async () => {
        const result = await addAll([
            { databaseId: "db", assetId: "a", key: COG_STORED_KEY },
            pinned(LATEST),
        ]);
        expect(result.current.entries).toHaveLength(1);
    });

    it("a pin on an older version stays a separate layer", async () => {
        const result = await addAll([SOURCE, pinned("older-version")]);
        expect(result.current.entries).toHaveLength(2);
    });

    it("a file of the same name in another folder stays a separate layer", async () => {
        const result = await addAll([
            { databaseId: "db", assetId: "a", key: COG_STORED_KEY },
            { databaseId: "db", assetId: "a", key: `${ROOT}other/s.tif.cog.tif` },
        ]);
        expect(result.current.entries).toHaveLength(2);
    });

    it("keeps the two apart when the API cannot say who the files are", async () => {
        (fetchFileInfo as jest.Mock).mockResolvedValue([false, "Not authorized"]);
        const result = await addAll([SOURCE, pinned(LATEST)]);
        expect(result.current.entries).toHaveLength(2);
    });
});

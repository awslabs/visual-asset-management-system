/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { loadFixtureTiff, fixtureSize } from "./fixtures.testdata";

jest.mock("../../services/APIService", () => ({
    fetchFileMetadata: jest.fn(),
    fetchFileInfo: jest.fn(),
    downloadAsset: jest.fn(),
}));
jest.mock("geotiff", () => ({ ...jest.requireActual("geotiff"), fromUrl: jest.fn() }));
jest.mock("./presignedUrl", () => ({
    ...jest.requireActual("./presignedUrl"),
    requestPresignedUrl: jest.fn(),
}));
jest.mock("./placedImage", () => ({
    ...jest.requireActual("./placedImage"),
    buildPlacedImage: jest.fn(),
}));

import { fromUrl } from "geotiff";
import { fetchFileInfo, fetchFileMetadata } from "../../services/APIService";
import { RasterDownloadRefused, requestPresignedUrl } from "./presignedUrl";
import { buildPlacedImage } from "./placedImage";
import {
    attributeFacts,
    clearRasterAttributeCache,
    cogKeyFor,
    isCogFileName,
    isStaleCog,
    prepareRaster,
} from "./rasterResolver";

const FILE = { databaseId: "db", assetId: "a1", key: "maps/site.tif" };
const rows = (values: Record<string, unknown>) => ({
    metadata: Object.entries(values).map(([metadataKey, metadataValue]) => ({
        metadataKey,
        metadataValue,
    })),
});

function attributesByKey(map: Record<string, Record<string, unknown>>) {
    (fetchFileMetadata as jest.Mock).mockImplementation(async ({ filePath }: any) =>
        rows(map[filePath] ?? {})
    );
}

beforeEach(() => {
    jest.clearAllMocks();
    clearRasterAttributeCache();
    (requestPresignedUrl as jest.Mock).mockImplementation(
        async (f: any) => `https://s3/${f.key}?X-Amz-Date=20261117T170000Z&X-Amz-Expires=3600`
    );
    (fetchFileInfo as jest.Mock).mockResolvedValue([true, { size: 1000, versions: [] }]);
    (buildPlacedImage as jest.Mock).mockResolvedValue({
        blob: new Blob(["x"]),
        corners: [
            [10, 50],
            [10.1, 50],
            [10.1, 49.9],
            [10, 49.9],
        ],
        width: 4,
        height: 4,
    });
});

describe("attributeFacts", () => {
    it("reads promoted raster facts, accepting string booleans", () => {
        expect(
            attributeFacts(
                rows({ ext_is_cog: "true", ext_crs: "EPSG:3857", ext_georeferenced: true }).metadata
            )
        ).toEqual({ isCog: true, crs: "EPSG:3857", georeferenced: true });
    });
    it("reads the COG link", () => {
        expect(
            attributeFacts(
                rows({ cogFile: "maps/site.tif.cog.tif", cogOfVersionId: "v1" }).metadata
            )
        ).toEqual({ cogFile: "maps/site.tif.cog.tif", cogOfVersionId: "v1" });
    });
    it("is empty for no rows", () => {
        expect(attributeFacts(undefined)).toEqual({});
    });
});

describe("naming", () => {
    it("recognises a converted COG name and a stored link", () => {
        expect(isCogFileName("a/b.tif.cog.tif")).toBe(true);
        expect(isCogFileName("a/b.tif")).toBe(false);
        expect(cogKeyFor("/maps/site.tif.cog.tif")).toBe("maps/site.tif.cog.tif");
    });
});

describe("prepareRaster from attributes", () => {
    it("draws a Web Mercator COG on the tile path without opening the file", async () => {
        attributesByKey({
            [FILE.key]: { ext_is_cog: true, ext_crs: "EPSG:3857", ext_georeferenced: true },
        });
        const result: any = await prepareRaster(FILE);
        expect(result.mode).toBe("tile");
        expect(result.usedCog).toBe(false);
        expect(fromUrl).not.toHaveBeenCalled();
    });

    it("refuses a file with no georeference without requesting it", async () => {
        attributesByKey({ [FILE.key]: { ext_georeferenced: false } });
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({ refused: true, reason: "no-georeference" });
        expect(requestPresignedUrl).not.toHaveBeenCalled();
        expect(fromUrl).not.toHaveBeenCalled();
    });

    it("refuses a CRS that is not bundled, from the attributes alone", async () => {
        attributesByKey({
            [FILE.key]: { ext_is_cog: false, ext_crs: "EPSG:2056", ext_georeferenced: true },
        });
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({ refused: true, reason: "unsupported-crs" });
        expect(fromUrl).not.toHaveBeenCalled();
    });

    it("places a small file in a bundled system, opening it for its pixels", async () => {
        attributesByKey({
            [FILE.key]: { ext_is_cog: false, ext_crs: "EPSG:32633", ext_georeferenced: true },
        });
        (fromUrl as jest.Mock).mockResolvedValue(await loadFixtureTiff("utm33-float-nodata.tif"));
        const result: any = await prepareRaster(FILE, {
            sizeBytes: fixtureSize("utm33-float-nodata.tif"),
        });
        expect(result).toMatchObject({ mode: "placed", approximate: true });
        expect(result.approximateNote).toMatch(/approximate placement \(not reprojected\)/);
        expect(buildPlacedImage).toHaveBeenCalled();
    });
});

describe("prepareRaster from the header", () => {
    beforeEach(() => attributesByKey({}));

    it("reads the header when there are no attributes, and tiles a Web Mercator COG", async () => {
        (fromUrl as jest.Mock).mockResolvedValue(await loadFixtureTiff("cog-3857.tif"));
        const result: any = await prepareRaster(FILE, { sizeBytes: 5 * 1024 ** 3 });
        expect(result.mode).toBe("tile");
        expect(buildPlacedImage).not.toHaveBeenCalled();
    });

    it("refuses a large striped file as not cloud-optimized", async () => {
        (fromUrl as jest.Mock).mockResolvedValue(await loadFixtureTiff("striped-3857.tif"));
        const result: any = await prepareRaster(FILE, { sizeBytes: 500 * 1024 ** 2 });
        expect(result).toMatchObject({ refused: true, reason: "not-cloud-optimized" });
        expect(result.message).toMatch(/TIF-to-COG pipeline/);
    });

    it("refuses a file over the extent limit with the convert message", async () => {
        (fromUrl as jest.Mock).mockResolvedValue(await loadFixtureTiff("wgs84-wide.tif"));
        const result: any = await prepareRaster(FILE, { sizeBytes: 1000 });
        expect(result).toMatchObject({ refused: true, reason: "too-large-to-place" });
    });

    it("refuses a file with no georeference found in the header", async () => {
        (fromUrl as jest.Mock).mockResolvedValue(await loadFixtureTiff("no-georeference.tif"));
        const result: any = await prepareRaster(FILE, { sizeBytes: 1000 });
        expect(result).toMatchObject({ refused: true, reason: "no-georeference" });
    });

    it("looks up the size when the caller does not know it", async () => {
        (fromUrl as jest.Mock).mockResolvedValue(await loadFixtureTiff("wgs84-striped.tif"));
        (fetchFileInfo as jest.Mock).mockResolvedValue([true, { size: 200 * 1024 ** 2 }]);
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({ refused: true, reason: "too-large-to-place" });
    });
});

describe("prefer the converted COG", () => {
    const COG_KEY = "maps/site.tif.cog.tif";

    it("draws the COG beside the source when the link is recorded", async () => {
        attributesByKey({
            [FILE.key]: { ext_is_cog: false, ext_crs: "EPSG:32633", cogFile: COG_KEY },
            [COG_KEY]: {
                ext_is_cog: true,
                ext_crs: "EPSG:3857",
                ext_georeferenced: true,
                cogOfVersionId: "v2",
            },
        });
        (fetchFileInfo as jest.Mock).mockResolvedValue([
            true,
            { versions: [{ versionId: "v2", isLatest: true }] },
        ]);
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({
            mode: "tile",
            usedCog: true,
            staleCog: false,
            sourceKey: FILE.key,
        });
        expect(result.file.key).toBe(COG_KEY);
    });

    it("flags a COG made from an earlier version of its source", async () => {
        attributesByKey({
            [FILE.key]: { cogFile: COG_KEY },
            [COG_KEY]: { ext_is_cog: true, ext_crs: "EPSG:3857", cogOfVersionId: "v1" },
        });
        (fetchFileInfo as jest.Mock).mockResolvedValue([
            true,
            { versions: [{ versionId: "v2", isLatest: true }] },
        ]);
        const result: any = await prepareRaster(FILE);
        expect(result.staleCog).toBe(true);
        await expect(isStaleCog(FILE, "v2")).resolves.toBe(false);
        await expect(isStaleCog(FILE, undefined)).resolves.toBe(false);
    });

    it("falls back to the source when the COG is refused", async () => {
        attributesByKey({
            [FILE.key]: {
                ext_is_cog: true,
                ext_crs: "EPSG:3857",
                ext_georeferenced: true,
                cogFile: COG_KEY,
            },
        });
        (requestPresignedUrl as jest.Mock).mockImplementation(async (f: any) => {
            if (f.key === COG_KEY) throw new RasterDownloadRefused("not found");
            return `https://s3/${f.key}`;
        });
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({ mode: "tile", usedCog: false });
        expect(result.file.key).toBe(FILE.key);
    });

    it("does not look for a COG when a COG is opened directly", async () => {
        const direct = { ...FILE, key: COG_KEY };
        attributesByKey({
            [COG_KEY]: { ext_is_cog: true, ext_crs: "EPSG:3857", cogFile: "other.cog.tif" },
        });
        const result: any = await prepareRaster(direct);
        expect(result.file.key).toBe(COG_KEY);
        expect(result.usedCog).toBe(false);
    });

    it("can be switched off", async () => {
        attributesByKey({
            [FILE.key]: { ext_is_cog: true, ext_crs: "EPSG:3857", cogFile: COG_KEY },
        });
        const result: any = await prepareRaster(FILE, { preferCog: false });
        expect(result.usedCog).toBe(false);
    });
});

describe("refusals from the API", () => {
    it("shows the API's reason for a refused download", async () => {
        attributesByKey({ [FILE.key]: { ext_is_cog: true, ext_crs: "EPSG:3857" } });
        (requestPresignedUrl as jest.Mock).mockRejectedValue(
            new RasterDownloadRefused("Asset is quarantined and cannot be downloaded")
        );
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({
            refused: true,
            reason: "download-refused",
            message: "Asset is quarantined and cannot be downloaded",
        });
    });

    it("says it could not read a file that fails to open", async () => {
        attributesByKey({});
        (fromUrl as jest.Mock).mockRejectedValue(new Error("boom"));
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({ refused: true, reason: "unreadable" });
    });
});

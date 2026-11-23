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
    RASTER_ATTRIBUTE_KEYS,
    attributeFacts,
    clearRasterAttributeCache,
    cogKeyFor,
    fileIdentity,
    isCogFileName,
    isStaleCog,
    lookupSize,
    prepareRaster,
} from "./rasterResolver";

const FILE = { databaseId: "db", assetId: "a1", key: "/maps/site.tif" };
const COG_KEY = "/maps/site.tif.cog.tif";
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

/** The header each presigned URL opens, by the file key in the URL. */
function headers(map: Record<string, string>) {
    (fromUrl as jest.Mock).mockImplementation(async (url: string) => {
        const key = Object.keys(map).find((candidate) => url.includes(`${candidate}?`));
        if (!key) throw new Error(`no fixture for ${url}`);
        return loadFixtureTiff(map[key]);
    });
}

beforeEach(() => {
    jest.clearAllMocks();
    clearRasterAttributeCache();
    (requestPresignedUrl as jest.Mock).mockImplementation(
        async (f: any) => `https://s3${f.key}?X-Amz-Date=20261117T170000Z&X-Amz-Expires=3600`
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
    it("reads the COG link under the flat cog_link_* keys the COG pipeline writes", () => {
        expect(RASTER_ATTRIBUTE_KEYS.cogFile).toBe("cog_link_cogFile");
        expect(
            attributeFacts(
                rows({
                    cog_link_cogFile: COG_KEY,
                    cog_link_cogOfVersionId: "v1",
                    cog_link_outputCrs: "EPSG:3857",
                }).metadata
            )
        ).toEqual({ cogFile: COG_KEY, cogOfVersionId: "v1", cogOutputCrs: "EPSG:3857" });
    });
    it("ignores the bare names an earlier draft of the contract used", () => {
        expect(attributeFacts(rows({ cogFile: COG_KEY, cogOfVersionId: "v1" }).metadata)).toEqual(
            {}
        );
    });
    it("is empty for no rows", () => {
        expect(attributeFacts(undefined)).toEqual({});
    });
});

describe("naming", () => {
    it("recognises a converted COG name and keeps the asset-relative leading slash", () => {
        expect(isCogFileName("a/b.tif.cog.tif")).toBe(true);
        expect(isCogFileName("a/b.tif")).toBe(false);
        expect(cogKeyFor("/maps/site.tif.cog.tif")).toBe("/maps/site.tif.cog.tif");
        expect(cogKeyFor("maps/site.tif.cog.tif")).toBe("/maps/site.tif.cog.tif");
    });
});

describe("attributes", () => {
    it("reads the attribute snapshot of a pinned asset version", async () => {
        attributesByKey({});
        headers({ [FILE.key]: "cog-3857.tif" });
        await prepareRaster({ ...FILE, assetVersionId: "3" });
        expect(fetchFileMetadata).toHaveBeenCalledWith(
            expect.objectContaining({ filePath: FILE.key, assetVersionId: "3" })
        );
    });

    it("names the pinned version in the label, and not for an unpinned file", async () => {
        attributesByKey({});
        headers({ [FILE.key]: "cog-3857.tif" });
        const pinned: any = await prepareRaster({ ...FILE, versionId: "v7" });
        expect(pinned.label).toBe("site.tif (version v7)");
        clearRasterAttributeCache();
        const latest: any = await prepareRaster(FILE);
        expect(latest.label).toBe("site.tif");
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
});

describe("the header decides what is drawn (D4)", () => {
    it("tiles a Web Mercator COG and carries its extent for framing", async () => {
        attributesByKey({
            [FILE.key]: { ext_is_cog: true, ext_crs: "EPSG:3857", ext_georeferenced: true },
        });
        headers({ [FILE.key]: "cog-3857.tif" });
        const result: any = await prepareRaster(FILE);
        expect(result.mode).toBe("tile");
        expect(result.bboxWgs84).toHaveLength(4);
        expect(result.bboxWgs84[0]).toBeLessThanOrEqual(10);
    });

    it("does not tile a file whose attributes wrongly claim a Web Mercator COG", async () => {
        attributesByKey({
            [FILE.key]: { ext_is_cog: true, ext_crs: "EPSG:3857", ext_georeferenced: true },
        });
        headers({ [FILE.key]: "wgs84-striped.tif" });
        const result: any = await prepareRaster(FILE, {
            sizeBytes: fixtureSize("wgs84-striped.tif"),
        });
        expect(result).toMatchObject({ mode: "placed", approximate: true });
    });

    it("places a small file in a bundled system, opening it for its pixels", async () => {
        attributesByKey({
            [FILE.key]: { ext_is_cog: false, ext_crs: "EPSG:32633", ext_georeferenced: true },
        });
        headers({ [FILE.key]: "utm33-float-nodata.tif" });
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
        headers({ [FILE.key]: "cog-3857.tif" });
        const result: any = await prepareRaster(FILE, { sizeBytes: 5 * 1024 ** 3 });
        expect(result.mode).toBe("tile");
        expect(buildPlacedImage).not.toHaveBeenCalled();
    });

    it("refuses a large striped file as not cloud-optimized", async () => {
        headers({ [FILE.key]: "striped-3857.tif" });
        const result: any = await prepareRaster(FILE, { sizeBytes: 500 * 1024 ** 2 });
        expect(result).toMatchObject({ refused: true, reason: "not-cloud-optimized" });
        expect(result.message).toMatch(/COG Conversion pipeline/);
    });

    it("refuses a file over the extent limit with the convert message", async () => {
        headers({ [FILE.key]: "wgs84-wide.tif" });
        const result: any = await prepareRaster(FILE, { sizeBytes: 1000 });
        expect(result).toMatchObject({ refused: true, reason: "too-large-to-place" });
    });

    it("refuses a file with no georeference found in the header", async () => {
        headers({ [FILE.key]: "no-georeference.tif" });
        const result: any = await prepareRaster(FILE, { sizeBytes: 1000 });
        expect(result).toMatchObject({ refused: true, reason: "no-georeference" });
    });
});

describe("decode cap (D1)", () => {
    it("refuses a pixel bomb with a tiny extent and file size before any pixel is decoded", async () => {
        attributesByKey({});
        headers({ [FILE.key]: "pixel-bomb-4326.tif" });
        const result: any = await prepareRaster(FILE, {
            sizeBytes: fixtureSize("pixel-bomb-4326.tif"),
        });
        expect(result).toMatchObject({ refused: true, reason: "too-many-pixels" });
        expect(result.message).toMatch(/11,000 x 10,000 pixels/);
        expect(buildPlacedImage).not.toHaveBeenCalled();
    });
});

describe("the size guard fails closed (D2)", () => {
    beforeEach(() => attributesByKey({}));

    it("uses the size the header read reported (Content-Range) when the caller has none", async () => {
        const tiff: any = await loadFixtureTiff("wgs84-striped.tif");
        Object.defineProperty(tiff.source, "fileSize", { get: () => 177 * 1024 ** 2 });
        (fromUrl as jest.Mock).mockResolvedValue(tiff);
        (fetchFileInfo as jest.Mock).mockResolvedValue([false, "unavailable"]);
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({ refused: true, reason: "too-large-to-place" });
        expect(buildPlacedImage).not.toHaveBeenCalled();
    });

    it("needs no size lookup when the header's byte counts prove the file small", async () => {
        headers({ [FILE.key]: "wgs84-striped.tif" });
        (fetchFileInfo as jest.Mock).mockResolvedValue([true, { size: 200 * 1024 ** 2 }]);
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({ mode: "placed" });
        expect(fetchFileInfo).not.toHaveBeenCalled();
    });

    it("places a file of unknown size whose byte counts prove it small", async () => {
        headers({ [FILE.key]: "wgs84-striped.tif" });
        (fetchFileInfo as jest.Mock).mockResolvedValue([false, "unavailable"]);
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({ mode: "placed" });
    });
});

describe("prefer the converted COG", () => {
    it("draws the COG named by cog_link_cogFile when it is recorded", async () => {
        attributesByKey({
            [FILE.key]: { ext_is_cog: false, ext_crs: "EPSG:32633", cog_link_cogFile: COG_KEY },
            [COG_KEY]: {
                ext_is_cog: true,
                ext_crs: "EPSG:3857",
                ext_georeferenced: true,
                cog_link_cogOfVersionId: "v2",
            },
        });
        headers({ [COG_KEY]: "cog-3857.tif" });
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
        expect(requestPresignedUrl).toHaveBeenCalledWith(expect.objectContaining({ key: COG_KEY }));
    });

    it("flags a COG made from an earlier version of its source", async () => {
        attributesByKey({
            [FILE.key]: { cog_link_cogFile: COG_KEY },
            [COG_KEY]: { cog_link_cogOfVersionId: "v1", cog_link_outputCrs: "EPSG:3857" },
        });
        headers({ [COG_KEY]: "cog-3857.tif" });
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
        attributesByKey({ [FILE.key]: { cog_link_cogFile: COG_KEY } });
        headers({ [FILE.key]: "cog-3857.tif" });
        (requestPresignedUrl as jest.Mock).mockImplementation(async (f: any) => {
            if (f.key === COG_KEY) throw new RasterDownloadRefused("not found");
            return `https://s3${f.key}?X-Amz-Expires=3600`;
        });
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({ mode: "tile", usedCog: false });
        expect(result.file.key).toBe(FILE.key);
    });

    it("does not look for a COG when a COG is opened directly", async () => {
        const direct = { ...FILE, key: COG_KEY };
        attributesByKey({ [COG_KEY]: { cog_link_cogFile: "/other.cog.tif" } });
        headers({ [COG_KEY]: "cog-3857.tif" });
        const result: any = await prepareRaster(direct);
        expect(result.file.key).toBe(COG_KEY);
        expect(result.usedCog).toBe(false);
    });

    it("can be switched off", async () => {
        attributesByKey({ [FILE.key]: { cog_link_cogFile: COG_KEY } });
        headers({ [FILE.key]: "cog-3857.tif" });
        const result: any = await prepareRaster(FILE, { preferCog: false });
        expect(result.usedCog).toBe(false);
    });
});

describe("a file opened at a version (R4-1)", () => {
    // The source has a COG made from its latest version, v2; v1 is older. Asset version 3 recorded v1,
    // asset version 4 recorded v2 together with the COG.
    const VERSIONS = [
        {
            versionId: "v2",
            isLatest: true,
            size: 2000,
            assetVersionIds: [{ id: "4", label: "v4" }],
        },
        {
            versionId: "v1",
            isLatest: false,
            size: 1000,
            assetVersionIds: [{ id: "3", label: "v3" }],
        },
    ];
    beforeEach(() => {
        (fetchFileInfo as jest.Mock).mockResolvedValue([true, { size: 2000, versions: VERSIONS }]);
        headers({ [FILE.key]: "cog-3857.tif", [COG_KEY]: "cog-3857.tif" });
    });

    it("draws an older file version itself, labelled with its version, and flags the COG", async () => {
        attributesByKey({
            [FILE.key]: { cog_link_cogFile: COG_KEY },
            [COG_KEY]: { cog_link_cogOfVersionId: "v2", cog_link_outputCrs: "EPSG:3857" },
        });
        const result: any = await prepareRaster({ ...FILE, versionId: "v1" });
        expect(result).toMatchObject({
            mode: "tile",
            usedCog: false,
            staleCog: true,
            label: "site.tif (version v1)",
        });
        expect(result.file).toEqual({ ...FILE, versionId: "v1" });
        expect(requestPresignedUrl).toHaveBeenCalledTimes(1);
        expect(requestPresignedUrl).toHaveBeenCalledWith({ ...FILE, versionId: "v1" });
    });

    it("draws the COG for a file version the COG was made from", async () => {
        attributesByKey({
            [FILE.key]: { cog_link_cogFile: COG_KEY },
            [COG_KEY]: { cog_link_cogOfVersionId: "v2", cog_link_outputCrs: "EPSG:3857" },
        });
        const result: any = await prepareRaster({ ...FILE, versionId: "v2" });
        expect(result).toMatchObject({ usedCog: true, staleCog: false, label: "site.tif.cog.tif" });
        expect(result.file).toEqual({ databaseId: "db", assetId: "a1", key: COG_KEY });
    });

    it("draws a file opened through an asset version that has no COG of its version", async () => {
        // Asset version 3 holds no COG (its snapshot has no attributes for it); the latest COG is of v2.
        (fetchFileMetadata as jest.Mock).mockImplementation(
            async ({ filePath, assetVersionId }: any) => {
                if (filePath === FILE.key) return rows({ cog_link_cogFile: COG_KEY });
                if (filePath === COG_KEY && !assetVersionId) {
                    return rows({ cog_link_cogOfVersionId: "v2" });
                }
                return rows({});
            }
        );
        const result: any = await prepareRaster({ ...FILE, assetVersionId: "3" });
        expect(result).toMatchObject({
            usedCog: false,
            staleCog: true,
            label: "site.tif (asset version 3)",
        });
        expect(result.file).toEqual({ ...FILE, assetVersionId: "3" });
        expect(requestPresignedUrl).toHaveBeenCalledTimes(1);
        expect(fetchFileInfo).toHaveBeenCalledWith(
            expect.objectContaining({ fileKey: FILE.key, includeVersions: true })
        );
    });

    it("draws the COG an asset version recorded with the version it was made from", async () => {
        (fetchFileMetadata as jest.Mock).mockImplementation(
            async ({ filePath, assetVersionId }: any) => {
                if (filePath === FILE.key) return rows({ cog_link_cogFile: COG_KEY });
                if (filePath === COG_KEY && assetVersionId === "4") {
                    return rows({
                        cog_link_cogOfVersionId: "v2",
                        cog_link_outputCrs: "EPSG:3857",
                    });
                }
                return rows({ cog_link_cogOfVersionId: "v9" });
            }
        );
        const result: any = await prepareRaster({ ...FILE, assetVersionId: "4" });
        expect(result).toMatchObject({ usedCog: true, staleCog: false });
        expect(result.file).toEqual({ ...FILE, key: COG_KEY, assetVersionId: "4" });
    });

    it("does not flag the COG when one of the version exists but is refused", async () => {
        attributesByKey({
            [FILE.key]: { cog_link_cogFile: COG_KEY },
            [COG_KEY]: { cog_link_cogOfVersionId: "v1" },
        });
        (requestPresignedUrl as jest.Mock).mockImplementation(async (f: any) => {
            if (f.key === COG_KEY) throw new RasterDownloadRefused("quarantined");
            return `https://s3${f.key}?X-Amz-Expires=3600`;
        });
        const result: any = await prepareRaster({ ...FILE, versionId: "v1" });
        expect(result).toMatchObject({ usedCog: false, staleCog: false });
        expect(result.file.key).toBe(FILE.key);
    });

    it("draws the latest COG, flagged as older, for a file opened at its latest version", async () => {
        // The file page passes the version a file is at even when it is the latest.
        attributesByKey({
            [FILE.key]: { cog_link_cogFile: COG_KEY },
            [COG_KEY]: { cog_link_cogOfVersionId: "v1", cog_link_outputCrs: "EPSG:3857" },
        });
        const result: any = await prepareRaster({ ...FILE, versionId: "v2" });
        expect(result).toMatchObject({ usedCog: true, staleCog: true, label: "site.tif.cog.tif" });
        expect(result.file).toEqual({ databaseId: "db", assetId: "a1", key: COG_KEY });
    });

    it("draws an unpinned file from its COG, as before", async () => {
        attributesByKey({
            [FILE.key]: { cog_link_cogFile: COG_KEY },
            [COG_KEY]: { cog_link_cogOfVersionId: "v2", cog_link_outputCrs: "EPSG:3857" },
        });
        const result: any = await prepareRaster(FILE);
        expect(result).toMatchObject({ usedCog: true, staleCog: false });
    });

    it("looks up the size of the version a file is opened at", async () => {
        await expect(lookupSize({ ...FILE, versionId: "v1" })).resolves.toBe(1000);
        await expect(lookupSize({ ...FILE, assetVersionId: "3" })).resolves.toBe(1000);
        await expect(lookupSize({ ...FILE, assetVersionId: "9" })).resolves.toBeUndefined();
        await expect(lookupSize(FILE)).resolves.toBe(2000);
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

describe("fileIdentity (d17-D1)", () => {
    it("returns the API's asset-relative path and the latest version", async () => {
        (fetchFileInfo as jest.Mock).mockResolvedValue([
            true,
            {
                relativePath: "/maps/site.tif.cog.tif",
                versions: [
                    { versionId: "old", isLatest: false },
                    { versionId: "new", isLatest: true },
                ],
            },
        ]);
        await expect(
            fileIdentity({ databaseId: "db", assetId: "a1", key: "xa1/maps/site.tif.cog.tif" })
        ).resolves.toEqual({ relativePath: "/maps/site.tif.cog.tif", latestVersionId: "new" });
        expect(fetchFileInfo).toHaveBeenCalledWith(
            expect.objectContaining({ fileKey: "xa1/maps/site.tif.cog.tif", includeVersions: true })
        );
    });

    it("says nothing when the API refuses or fails", async () => {
        (fetchFileInfo as jest.Mock).mockResolvedValue([false, "Not authorized"]);
        await expect(fileIdentity(FILE)).resolves.toEqual({});
        (fetchFileInfo as jest.Mock).mockRejectedValue(new Error("network"));
        await expect(fileIdentity(FILE)).resolves.toEqual({});
    });
});

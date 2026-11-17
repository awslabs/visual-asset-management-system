/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { fromUrl } from "geotiff";
import { fetchFileInfo, fetchFileMetadata } from "../../services/APIService";
import { RasterDownloadRefused, RasterFileRef, requestPresignedUrl } from "./presignedUrl";
import { PlacedImage, buildPlacedImage } from "./placedImage";
import {
    RasterDecision,
    RasterFacts,
    RefusalReason,
    decideRasterDisplay,
} from "./rasterEligibility";
import { readTiffFacts } from "./tiffHeader";

/**
 * Attribute keys the viewer reads. `ext_*` are the promoted raster facts the system metadata pipeline
 * writes for a TIFF (#1600); `cog*` link a source to its converted COG (#1601). Both are looked up
 * by key in one place so a rename in either pipeline is a one-line change here.
 */
export const RASTER_ATTRIBUTE_KEYS = {
    isCog: "ext_is_cog",
    crs: "ext_crs",
    georeferenced: "ext_georeferenced",
    cogFile: "cogFile",
    cogOfVersionId: "cogOfVersionId",
} as const;

/** The suffix #1601 appends to a source's file name for its converted COG. */
export const COG_SUFFIX = ".cog.tif";

export interface RasterAttributes extends RasterFacts {
    cogFile?: string;
    cogOfVersionId?: string;
}

function asBoolean(value: unknown): boolean | undefined {
    if (value === true || value === "true" || value === "True") return true;
    if (value === false || value === "false" || value === "False") return false;
    return undefined;
}

/** Reads the raster facts and COG link out of a file's attribute rows (metadataKey/metadataValue). */
export function attributeFacts(rows: any[] | undefined | null): RasterAttributes {
    const byKey = new Map<string, unknown>();
    for (const row of rows ?? []) {
        if (row && typeof row.metadataKey === "string")
            byKey.set(row.metadataKey, row.metadataValue);
    }
    const facts: RasterAttributes = {};
    const isCog = asBoolean(byKey.get(RASTER_ATTRIBUTE_KEYS.isCog));
    if (isCog !== undefined) facts.isCog = isCog;
    const georeferenced = asBoolean(byKey.get(RASTER_ATTRIBUTE_KEYS.georeferenced));
    if (georeferenced !== undefined) facts.georeferenced = georeferenced;
    const crs = byKey.get(RASTER_ATTRIBUTE_KEYS.crs);
    if (typeof crs === "string" && crs.trim()) facts.crs = crs.trim();
    const cogFile = byKey.get(RASTER_ATTRIBUTE_KEYS.cogFile);
    if (typeof cogFile === "string" && cogFile.trim()) facts.cogFile = cogFile.trim();
    const cogOfVersionId = byKey.get(RASTER_ATTRIBUTE_KEYS.cogOfVersionId);
    if (typeof cogOfVersionId === "string" && cogOfVersionId.trim()) {
        facts.cogOfVersionId = cogOfVersionId.trim();
    }
    return facts;
}

/** True for a name that is a converted COG: `<source>.cog.tif`. */
export function isCogFileName(key: string): boolean {
    return key.toLowerCase().endsWith(COG_SUFFIX);
}

/** True for a TIFF the viewer can try: .tif or .tiff, including `.cog.tif`. */
export function isTiffName(key: string): boolean {
    return /\.tiff?$/i.test(key);
}

/** The key of a COG that sits beside its source, relative to the asset, as the contract names it. */
export function cogKeyFor(cogFile: string): string {
    // The link holds the COG's path relative to the asset root; keys elsewhere in the web carry the
    // same relative form, so it is used as given (a leading slash is not part of a key).
    return cogFile.replace(/^\/+/, "");
}

export interface RasterRefusal {
    refused: true;
    reason: RefusalReason | "download-refused";
    message: string;
}

export interface PreparedRaster {
    refused?: false;
    /** Stable id for the layer: the effective file's asset and key. */
    id: string;
    label: string;
    mode: "tile" | "placed";
    /** The file that is drawn. It is the converted COG when one was preferred. */
    file: RasterFileRef;
    sourceKey: string;
    usedCog: boolean;
    /** The COG was made from an earlier version of its source. */
    staleCog: boolean;
    approximate: boolean;
    approximateNote: string | null;
    bboxWgs84: [number, number, number, number] | null;
    /** Presigned URL of the drawn file (tile mode: the source of the layer). */
    url: string;
    /** Placed mode: the drawn image and its corners. */
    placed?: PlacedImage;
}

export function rasterLabel(key: string): string {
    const parts = key.split("/");
    return parts[parts.length - 1] || key;
}

export function rasterId(file: RasterFileRef): string {
    return `${file.databaseId}/${file.assetId}/${file.key}${
        file.versionId ? `@${file.versionId}` : ""
    }`;
}

// Bounded memory of attribute reads, so reopening the same file does not repeat the request. The
// oldest entry goes first once the bound is reached.
const ATTRIBUTE_CACHE_MAX = 64;
const attributeCache = new Map<string, Promise<RasterAttributes>>();

export function clearRasterAttributeCache(): void {
    attributeCache.clear();
}

export function readRasterAttributes(file: RasterFileRef): Promise<RasterAttributes> {
    const cacheKey = rasterId(file);
    const cached = attributeCache.get(cacheKey);
    if (cached) return cached;
    const request = fetchFileMetadata({
        databaseId: file.databaseId,
        assetId: file.assetId,
        filePath: file.key,
        type: "attribute",
    })
        .then((result: any) => attributeFacts(result?.metadata))
        // A file whose attributes cannot be read is read from its header instead.
        .catch(() => ({} as RasterAttributes));
    attributeCache.set(cacheKey, request);
    if (attributeCache.size > ATTRIBUTE_CACHE_MAX) {
        const oldest = attributeCache.keys().next().value;
        if (oldest !== undefined) attributeCache.delete(oldest);
    }
    return request;
}

export interface PrepareOptions {
    /** File size when the caller already knows it (a file list or a search hit). */
    sizeBytes?: number;
    /** Prefer the converted COG recorded on the source. On unless a caller opts out. */
    preferCog?: boolean;
    signal?: AbortSignal;
}

function refusal(reason: RasterRefusal["reason"], message: string): RasterRefusal {
    return { refused: true, reason, message };
}

async function lookupSize(file: RasterFileRef, known?: number): Promise<number | undefined> {
    if (known !== undefined) return known;
    try {
        const [ok, info] = await fetchFileInfo({
            databaseId: file.databaseId,
            assetId: file.assetId,
            fileKey: file.key,
        });
        const size = ok ? Number((info as any)?.size) : NaN;
        return Number.isFinite(size) ? size : undefined;
    } catch {
        return undefined;
    }
}

/** Whether the source's latest version differs from the version a COG was converted from. */
export async function isStaleCog(
    source: RasterFileRef,
    cogOfVersionId: string | undefined
): Promise<boolean> {
    if (!cogOfVersionId) return false;
    try {
        const [ok, info] = await fetchFileInfo({
            databaseId: source.databaseId,
            assetId: source.assetId,
            fileKey: source.key,
            includeVersions: true,
        });
        if (!ok) return false;
        const versions: any[] = (info as any)?.versions ?? [];
        const latest = versions.find((version) => version?.isLatest) ?? versions[0];
        return !!latest?.versionId && latest.versionId !== cogOfVersionId;
    } catch {
        return false;
    }
}

/**
 * Works out how to show one raster file and gets everything the map needs for it.
 *
 *  1. Reads the file's attributes. When a converted COG is recorded (`cogFile`) and the caller did not
 *     open a COG directly, the COG is requested through the same download route; if the API refuses it
 *     or it is gone, the source is used.
 *  2. Decides from the attributes (ext_crs, ext_is_cog, ext_georeferenced) before the file is opened:
 *     a Web Mercator COG goes straight to the tile path and a file with no georeference or an
 *     unsupported system is refused without a read.
 *  3. Only a file that must be placed is opened, by ranged reads. When the attributes are absent the
 *     TIFF header is read here instead.
 *
 * Access is decided by the API at every request; nothing here derives a permission.
 */
export async function prepareRaster(
    file: RasterFileRef,
    options: PrepareOptions = {}
): Promise<PreparedRaster | RasterRefusal> {
    const sourceKey = file.key;
    let target = file;
    let usedCog = false;
    let staleCog = false;
    let url: string | undefined;

    const sourceAttributes = await readRasterAttributes(file);

    if (options.preferCog !== false && !isCogFileName(file.key) && sourceAttributes.cogFile) {
        const cogRef: RasterFileRef = {
            databaseId: file.databaseId,
            assetId: file.assetId,
            key: cogKeyFor(sourceAttributes.cogFile),
        };
        try {
            const cogUrl = await requestPresignedUrl(cogRef);
            target = cogRef;
            url = cogUrl;
            usedCog = true;
            const cogAttributes = await readRasterAttributes(cogRef);
            staleCog = await isStaleCog(file, cogAttributes.cogOfVersionId);
        } catch {
            // The COG is refused, gone or not yet written: the source is shown instead.
            target = file;
            url = undefined;
            usedCog = false;
        }
    }

    const attributes = usedCog ? await readRasterAttributes(target) : sourceAttributes;
    const sizeKnown = usedCog ? undefined : options.sizeBytes;
    let facts: RasterFacts = {
        georeferenced: attributes.georeferenced,
        crs: attributes.crs,
        isCog: attributes.isCog,
        sizeBytes: sizeKnown,
    };
    // A converted COG is Web Mercator by construction, so its own attributes may be missing for a moment
    // after the pipeline wrote it; the header read below settles it.
    const haveAttributeAnswer =
        facts.georeferenced !== undefined || facts.crs !== undefined || facts.isCog !== undefined;

    let decision: RasterDecision | null = null;
    if (haveAttributeAnswer && facts.crs !== undefined && facts.isCog !== undefined) {
        const early = decideRasterDisplay(facts);
        if (early.kind !== "placed") decision = early;
    } else if (facts.georeferenced === false) {
        decision = decideRasterDisplay(facts);
    }

    const buildTile = async (): Promise<PreparedRaster> => ({
        id: rasterId(target),
        label: rasterLabel(target.key),
        mode: "tile",
        file: target,
        sourceKey,
        usedCog,
        staleCog,
        approximate: false,
        approximateNote: null,
        bboxWgs84: null,
        url: url ?? (url = await requestPresignedUrl(target)),
    });

    try {
        if (decision?.kind === "tile") return await buildTile();
        if (decision?.kind === "refuse") return refusal(decision.reason, decision.message);

        // The file has to be opened: no usable attributes, or it is to be placed.
        url = url ?? (await requestPresignedUrl(target));
        const tiff = await fromUrl(url, { allowFullFile: false }, options.signal);
        const header = await readTiffFacts(tiff, await lookupSize(target, sizeKnown));
        facts = {
            georeferenced: header.georeferenced,
            crs: header.crs,
            isCog: attributes.isCog ?? header.isCog,
            sizeBytes: header.sizeBytes,
            bboxWgs84: header.bboxWgs84,
        };
        const final = decideRasterDisplay(facts);
        if (final.kind === "refuse") return refusal(final.reason, final.message);
        if (final.kind === "tile") return await buildTile();

        const placed = await buildPlacedImage(tiff, header, options.signal);
        return {
            id: rasterId(target),
            label: rasterLabel(target.key),
            mode: "placed",
            file: target,
            sourceKey,
            usedCog,
            staleCog,
            approximate: final.approximate,
            approximateNote: final.approximateNote,
            bboxWgs84: header.bboxWgs84 ?? null,
            url,
            placed,
        };
    } catch (error: any) {
        if (error instanceof RasterDownloadRefused) {
            return refusal("download-refused", error.message);
        }
        if (error?.name === "AbortError") throw error;
        return refusal("unreadable", "This raster could not be read.");
    }
}

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { fromUrl } from "geotiff";
import { fetchFileInfo, fetchFileMetadata } from "../../services/APIService";
import { RasterDownloadRefused, RasterFileRef, requestPresignedUrl } from "./presignedUrl";
import { PlacedImage, RasterTooLarge, buildPlacedImage } from "./placedImage";
import { RasterFacts, RefusalReason, decideRasterDisplay } from "./rasterEligibility";
import { readTiffFacts } from "./tiffHeader";

/**
 * Attribute keys the viewer reads. `ext_*` are the promoted raster facts the system metadata pipeline
 * writes for a TIFF (#1600). `cog_link_*` are the flat keys the COG Conversion pipeline (#1601) writes:
 * `cog_link_cogFile` on the source, and `cog_link_cogOfVersionId` and `cog_link_outputCrs` on the
 * COG. All are looked up by key in one place so a rename in either pipeline is a one-line change here.
 */
export const RASTER_ATTRIBUTE_KEYS = {
    isCog: "ext_is_cog",
    crs: "ext_crs",
    georeferenced: "ext_georeferenced",
    cogFile: "cog_link_cogFile",
    cogOfVersionId: "cog_link_cogOfVersionId",
    cogOutputCrs: "cog_link_outputCrs",
} as const;

/** The suffix #1601 appends to a source's file name for its converted COG. */
export const COG_SUFFIX = ".cog.tif";

export interface RasterAttributes extends RasterFacts {
    cogFile?: string;
    cogOfVersionId?: string;
    /** The CRS the COG pipeline wrote the COG in (on a converted COG). */
    cogOutputCrs?: string;
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
    const cogOutputCrs = byKey.get(RASTER_ATTRIBUTE_KEYS.cogOutputCrs);
    if (typeof cogOutputCrs === "string" && cogOutputCrs.trim()) {
        facts.cogOutputCrs = cogOutputCrs.trim();
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

/**
 * The download key of a COG named by `cog_link_cogFile`. The pipeline writes the COG's path relative to
 * the asset with a leading slash (`/maps/a.tif.cog.tif`), the asset-relative shape the download route
 * accepts, so the slash is kept (added when missing).
 */
export function cogKeyFor(cogFile: string): string {
    return `/${cogFile.replace(/^\/+/, "")}`;
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
    /**
     * A converted COG is recorded, but it was not made from the version shown. With `usedCog` the COG is
     * drawn and its source has moved on since; without it, the file was opened at a version (a file
     * version or an asset version) the COG was not made from, so that version of the source is drawn.
     */
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

/**
 * The file's attributes. With an asset version the snapshot taken with that version is read. The
 * attribute route has no per-file version parameter, so a file pinned only by its file version id is
 * read with its current attributes.
 */
export function readRasterAttributes(file: RasterFileRef): Promise<RasterAttributes> {
    const cacheKey = `${rasterId(file)}#${file.assetVersionId ?? ""}`;
    const cached = attributeCache.get(cacheKey);
    if (cached) return cached;
    const request = fetchFileMetadata({
        databaseId: file.databaseId,
        assetId: file.assetId,
        filePath: file.key,
        type: "attribute",
        assetVersionId: file.assetVersionId,
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

/** True for a file opened at a version: a file version id or an asset version. */
function isPinned(file: RasterFileRef): boolean {
    return !!(file.versionId || file.assetVersionId);
}

/** One version of a file, as the file-info route lists it. */
interface FileVersionEntry {
    versionId: string;
    isLatest: boolean;
    size?: number;
    /** The asset versions that record this file version. */
    assetVersionIds: string[];
}

/** The file's versions from the file-info route; empty when the API cannot say. */
async function readFileVersions(file: RasterFileRef): Promise<FileVersionEntry[]> {
    try {
        const [ok, info] = await fetchFileInfo({
            databaseId: file.databaseId,
            assetId: file.assetId,
            fileKey: file.key,
            includeVersions: true,
        });
        if (!ok) return [];
        const versions: any[] = (info as any)?.versions ?? [];
        return versions
            .filter((version) => typeof version?.versionId === "string" && version.versionId)
            .map((version) => {
                const size = Number(version.size);
                return {
                    versionId: version.versionId,
                    isLatest: version.isLatest === true,
                    size: Number.isFinite(size) ? size : undefined,
                    assetVersionIds: (Array.isArray(version.assetVersionIds)
                        ? version.assetVersionIds
                        : []
                    )
                        .map((entry: any) => entry?.id)
                        .filter((id: unknown): id is string => typeof id === "string"),
                };
            });
    } catch {
        return [];
    }
}

/**
 * The version a pinned file is opened at: its file version, or the version its asset version records.
 * Undefined for an unpinned file, or when the API does not say.
 */
function pinnedVersion(
    file: RasterFileRef,
    versions: FileVersionEntry[]
): FileVersionEntry | undefined {
    if (file.versionId) return versions.find((version) => version.versionId === file.versionId);
    if (file.assetVersionId) {
        return versions.find((version) =>
            version.assetVersionIds.includes(file.assetVersionId as string)
        );
    }
    return undefined;
}

/** The file's size from the file-info route: for a pinned file, the size of the version it is opened at. */
export async function lookupSize(file: RasterFileRef): Promise<number | undefined> {
    if (isPinned(file)) return pinnedVersion(file, await readFileVersions(file))?.size;
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

/**
 * The converted COG made from the version a pinned file is opened at. The COG as the file's asset version
 * recorded it is tried first, then the COG's latest version; a COG is taken only when its
 * `cog_link_cogOfVersionId` names that version and the API grants its download. Without one, says
 * whether a COG of that version was found at all (and refused).
 */
async function cogOfPinnedVersion(
    file: RasterFileRef,
    cogKey: string,
    shownVersionId: string
): Promise<{ ref: RasterFileRef; url: string } | { matched: boolean }> {
    const base = { databaseId: file.databaseId, assetId: file.assetId, key: cogKey };
    const candidates: RasterFileRef[] = file.assetVersionId
        ? [{ ...base, assetVersionId: file.assetVersionId }, base]
        : [base];
    let matched = false;
    for (const ref of candidates) {
        const attributes = await readRasterAttributes(ref);
        if (attributes.cogOfVersionId !== shownVersionId) continue;
        matched = true;
        try {
            return { ref, url: await requestPresignedUrl(ref) };
        } catch {
            // Refused, gone or not yet written: try the next candidate, or draw the source.
        }
    }
    return { matched };
}

/** Who a file is, as the API names it: its asset-relative path and the version its latest is at. */
export interface FileIdentity {
    /** The path inside the asset, with a leading slash, whatever form of the key was asked about. */
    relativePath?: string;
    /** The version id the unpinned (latest) file is at right now. */
    latestVersionId?: string;
}

/**
 * The file's identity from the file-info route, or an empty one when the API cannot say. A file can
 * be named by its full stored key (the file manager) or by its path inside the asset (a source's
 * recorded COG); the API resolves both to one `relativePath`. Used to tell whether two entries are
 * the same bytes. The API answers; nothing is derived here.
 */
export async function fileIdentity(file: RasterFileRef): Promise<FileIdentity> {
    try {
        const [ok, info] = await fetchFileInfo({
            databaseId: file.databaseId,
            assetId: file.assetId,
            fileKey: file.key,
            includeVersions: true,
        });
        if (!ok) return {};
        const versions: any[] = (info as any)?.versions ?? [];
        const latest = versions.find((version) => version?.isLatest) ?? versions[0];
        const relativePath = (info as any)?.relativePath;
        return {
            relativePath:
                typeof relativePath === "string" && relativePath ? relativePath : undefined,
            latestVersionId:
                typeof latest?.versionId === "string" && latest.versionId
                    ? latest.versionId
                    : undefined,
        };
    } catch {
        return {};
    }
}

/** The version id the unpinned (latest) file is at right now, or undefined when the API cannot say. */
export async function latestVersionId(file: RasterFileRef): Promise<string | undefined> {
    return (await fileIdentity(file)).latestVersionId;
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
 *  1. Reads the file's attributes. When a converted COG is recorded (`cog_link_cogFile`) and the caller
 *     did not open a COG directly, the COG is requested through the same download route; if the API
 *     refuses it or it is gone, the source is used. A file opened at a version (a file version or an
 *     asset version) is drawn from a COG only when the COG was made from that version; otherwise that
 *     version of the source is drawn, labelled with its version and flagged `staleCog`.
 *  2. Refuses from the attributes (ext_crs, ext_georeferenced) before the file is opened: no
 *     georeference, or a coordinate system the viewer cannot place.
 *  3. Reads the TIFF header by ranged reads and decides from it, so wrong or stale attributes cannot
 *     send a file to the tile path. Only a Web Mercator COG is tiled; a small file is placed, its
 *     decode size, fetch size and extent checked first (the placed path fails closed).
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

    const cogFile =
        options.preferCog !== false && !isCogFileName(file.key)
            ? sourceAttributes.cogFile
            : undefined;

    let pinned = false;
    if (cogFile && isPinned(file)) {
        const shown = pinnedVersion(file, await readFileVersions(file));
        // The file page passes the version a file is at even when it is the latest; a latest file
        // version is the file as it is now and takes the latest COG, as an unpinned file does.
        pinned = !!file.assetVersionId || !shown?.isLatest;
        if (pinned) {
            // Opened at a version: only a COG made from that version stands in for it.
            const shownVersionId = file.versionId ?? shown?.versionId;
            const cog = shownVersionId
                ? await cogOfPinnedVersion(file, cogKeyFor(cogFile), shownVersionId)
                : { matched: false };
            if ("url" in cog) {
                target = cog.ref;
                url = cog.url;
                usedCog = true;
            } else {
                // The version itself is drawn, named by its file version or its asset version.
                staleCog = !!shownVersionId && !cog.matched;
            }
        }
    }
    if (cogFile && !pinned) {
        const cogRef: RasterFileRef = {
            databaseId: file.databaseId,
            assetId: file.assetId,
            key: cogKeyFor(cogFile),
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
    // Refusals the attributes settle without opening the file: no georeference, or a coordinate
    // system the viewer cannot place. Anything that would be drawn is checked against the header.
    const early = decideRasterDisplay({
        georeferenced: attributes.georeferenced,
        crs: attributes.crs ?? attributes.cogOutputCrs,
        isCog: attributes.isCog,
    });
    if (
        early.kind === "refuse" &&
        (early.reason === "no-georeference" || early.reason === "unsupported-crs") &&
        (attributes.georeferenced === false || (attributes.crs ?? attributes.cogOutputCrs))
    ) {
        return refusal(early.reason, early.message);
    }

    try {
        // The header decides the path: the attributes can be stale or wrong, and the tile library
        // draws a blank layer, with no error, for a file that is not a Web Mercator COG.
        url = url ?? (await requestPresignedUrl(target));
        const tiff = await fromUrl(url, { allowFullFile: false }, options.signal);
        const header = await readTiffFacts(tiff, sizeKnown);
        let final = decideRasterDisplay(header);
        if (final.kind === "refuse" && final.reason === "size-unknown") {
            // Neither the caller nor the header read knew the size: ask the API once more.
            header.sizeBytes = await lookupSize(target);
            final = decideRasterDisplay(header);
        }
        if (final.kind === "refuse") return refusal(final.reason, final.message);

        const common = {
            id: rasterId(target),
            label:
                rasterLabel(target.key) +
                (usedCog
                    ? ""
                    : target.versionId
                    ? ` (version ${target.versionId})`
                    : target.assetVersionId
                    ? ` (asset version ${target.assetVersionId})`
                    : ""),
            file: target,
            sourceKey,
            usedCog,
            staleCog,
            bboxWgs84: header.bboxWgs84 ?? null,
            url,
        };
        if (final.kind === "tile") {
            return { ...common, mode: "tile", approximate: false, approximateNote: null };
        }
        const placed = await buildPlacedImage(tiff, header, options.signal);
        return {
            ...common,
            mode: "placed",
            approximate: final.approximate,
            approximateNote: final.approximateNote,
            placed,
        };
    } catch (error: any) {
        if (error instanceof RasterDownloadRefused) {
            return refusal("download-refused", error.message);
        }
        if (error?.name === "AbortError") throw error;
        if (error instanceof RasterTooLarge) return refusal("too-many-pixels", error.message);
        return refusal("unreadable", "This raster could not be read.");
    }
}

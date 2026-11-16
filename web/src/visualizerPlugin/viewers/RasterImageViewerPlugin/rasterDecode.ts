/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Browser-side decoding of TGA and TIFF images for the TIFF/TGA viewer. No React here.
 *
 * Every file this reads is untrusted input: another user of the asset may have uploaded it. The
 * decoders (`utif2`, `tga-js`) trust the file's own header, so each step that can allocate memory or
 * loop for as long as a header says is checked first:
 *
 *  - The TIFF directory chain is read here, not by `UTIF.decode`: that function follows a cyclic
 *    chain forever, builds an array of any length a tag entry names, and descends into EXIF, GPS and
 *    maker-note directories. This reader walks each directory once, reads only the tags decoding
 *    needs, and refuses an entry whose value lies outside the file.
 *  - Width x height, and the uncompressed size those imply, are held to the pixel cap and to
 *    {@link MAX_SAMPLE_BYTES} before any pixel is decoded.
 *  - Every strip and tile must lie inside the file, a deflate strip is inflated once with a bound on
 *    its output before the decoder inflates it, and a JPEG strip's own dimensions must match the
 *    strip.
 *  - A variant the decoders do not implement (BigTIFF, separate colour planes, old-style JPEG, an
 *    unknown compression) is refused rather than drawn wrongly.
 *
 * A failure is a {@link RasterDecodeError} whose `reason` tells the viewer whether to show the
 * generated preview (all of them) and which sentence to use.
 */

import { extensionToken } from "../../core/extensionMatching";
import type { PakoModule, TgaConstructor, UtifModule } from "./dependencies";

export type RasterKind = "tga" | "tiff";

/** Default cap on the file size the viewer downloads to decode (bytes). */
export const DEFAULT_MAX_DECODE_BYTES = 256 * 1024 * 1024;
/** Default cap on the pixels of one decoded page or image. */
export const DEFAULT_MAX_DECODE_PIXELS = 100_000_000;
/** Ceiling on the uncompressed sample data of one TIFF page, whatever the pixel cap is. */
export const MAX_SAMPLE_BYTES = 1024 * 1024 * 1024;
/** Most TIFF pages listed; a directory chain longer than this is cut. */
export const MAX_TIFF_PAGES = 1000;
/** Most strips or tiles in one page. */
const MAX_CHUNKS = 1_000_000;
/** Pixels sampled to choose the display range of high bit depth data. */
const STATS_SAMPLE_PIXELS = 2_000_000;
const STATS_BINS = 4096;
/** Share of samples clipped at each end when high bit depth data is scaled for display. */
const CLIP_FRACTION = 0.005;

export type RasterFailure = "too-large" | "too-many-pixels" | "unsupported" | "corrupt";

export class RasterDecodeError extends Error {
    constructor(public readonly reason: RasterFailure, message: string) {
        super(message);
        this.name = "RasterDecodeError";
    }
}

export interface RasterLimits {
    maxBytes: number;
    maxPixels: number;
}

/** The caps from the viewer's `customParameters`; a missing or invalid value keeps its default. */
export function limitsFromParameters(parameters?: Record<string, any>): RasterLimits {
    const pick = (value: unknown, fallback: number) =>
        typeof value === "number" && Number.isFinite(value) && value > 0
            ? Math.floor(value)
            : fallback;
    return {
        maxBytes: pick(parameters?.maxDecodeBytes, DEFAULT_MAX_DECODE_BYTES),
        maxPixels: pick(parameters?.maxDecodePixels, DEFAULT_MAX_DECODE_PIXELS),
    };
}

export function rasterKindOf(filename?: string): RasterKind | null {
    const token = extensionToken(filename);
    if (token === ".tga") return "tga";
    if (token === ".tif" || token === ".tiff") return "tiff";
    return null;
}

/** "256 MiB", "1.5 MiB", "12 KiB". */
export function formatBytes(bytes: number): string {
    if (bytes >= 1024 * 1024) {
        const mib = bytes / (1024 * 1024);
        return `${Number.isInteger(mib) ? mib : mib.toFixed(1)} MiB`;
    }
    return `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

/** "100 megapixels", "0.3 megapixels". */
export function formatMegapixels(pixels: number): string {
    const mp = pixels / 1_000_000;
    return `${Number.isInteger(mp) ? mp : mp.toFixed(1)} megapixels`;
}

export interface DecodedRaster {
    width: number;
    height: number;
    /** RGBA, 4 bytes per pixel, rows top to bottom. */
    data: Uint8ClampedArray;
    /** What was done to the samples to show them, when more than a copy (for the info line). */
    conversion?: string;
}

function checkPixels(width: number, height: number, limits: RasterLimits): number {
    if (!(width > 0) || !(height > 0)) {
        throw new RasterDecodeError("corrupt", "The image has no pixels.");
    }
    const pixels = width * height;
    if (pixels > limits.maxPixels) {
        throw new RasterDecodeError(
            "too-many-pixels",
            `This image is ${formatMegapixels(pixels)}, above the ${formatMegapixels(
                limits.maxPixels
            )} the viewer opens.`
        );
    }
    return pixels;
}

// ---------------------------------------------------------------------------------------------
// TGA
// ---------------------------------------------------------------------------------------------

export interface TgaInfo {
    width: number;
    height: number;
    pixelDepth: number;
    imageType: number;
}

const TGA_HEADER_BYTES = 18;
/** Uncompressed and run-length colour-mapped, true-colour and greyscale images. */
const TGA_IMAGE_TYPES = new Set([1, 2, 3, 9, 10, 11]);

/** Reads the 18-byte TGA header; nothing is decoded. */
export function readTgaHeader(bytes: Uint8Array): TgaInfo {
    if (bytes.length < TGA_HEADER_BYTES) {
        throw new RasterDecodeError("corrupt", "The file is too short to be a TGA image.");
    }
    const imageType = bytes[2];
    if (imageType === 0) {
        throw new RasterDecodeError("corrupt", "The TGA file contains no image data.");
    }
    if (!TGA_IMAGE_TYPES.has(imageType)) {
        throw new RasterDecodeError("unsupported", "This TGA image type is not supported.");
    }
    const width = bytes[12] | (bytes[13] << 8);
    const height = bytes[14] | (bytes[15] << 8);
    const pixelDepth = bytes[16];
    if (![8, 16, 24, 32].includes(pixelDepth)) {
        throw new RasterDecodeError(
            "unsupported",
            `A ${pixelDepth}-bit TGA pixel depth is not supported.`
        );
    }
    return { width, height, pixelDepth, imageType };
}

/** Whether the run-length packets from `start` cover `pixels` pixels without running off the data. */
function rleCovers(bytes: Uint8Array, start: number, pixels: number, sampleBytes: number): boolean {
    let at = start;
    let covered = 0;
    while (covered < pixels) {
        if (at >= bytes.length) return false;
        const header = bytes[at++];
        const count = (header & 127) + 1;
        const needed = header & 128 ? sampleBytes : count * sampleBytes;
        if (at + needed > bytes.length) return false;
        at += needed;
        covered += count;
    }
    return true;
}

export function decodeTga(
    bytes: Uint8Array,
    Tga: TgaConstructor,
    limits: RasterLimits
): DecodedRaster {
    const info = readTgaHeader(bytes);
    const pixels = checkPixels(info.width, info.height, limits);

    const bytesPerPixel = info.pixelDepth >> 3;
    const isRle = info.imageType >= 9;
    const isIndexed = info.imageType === 1 || info.imageType === 9;
    const palette = isIndexed ? (bytes[5] | (bytes[6] << 8)) * (bytes[7] >> 3) : 0;
    const dataStart = TGA_HEADER_BYTES + bytes[0] + palette;
    const sampleBytes = isIndexed ? 1 : bytesPerPixel;
    if (!isRle) {
        // An uncompressed image names its own size, so a truncated file is detectable.
        if (bytes.length < dataStart + pixels * sampleBytes) {
            throw new RasterDecodeError("corrupt", "The TGA file is truncated.");
        }
    } else if (!rleCovers(bytes, dataStart, pixels, sampleBytes)) {
        // tga-js leaves the pixels a short run-length stream does not reach black, so the packets
        // are walked first: the stream must hold every pixel the header names.
        throw new RasterDecodeError("corrupt", "The TGA file is truncated.");
    }

    let decoded: { width: number; height: number; data: Uint8ClampedArray };
    try {
        const tga = new Tga();
        tga.load(bytes);
        decoded = tga.getImageData({
            width: info.width,
            height: info.height,
            data: new Uint8ClampedArray(pixels * 4),
        });
    } catch (error) {
        throw new RasterDecodeError(
            "unsupported",
            `The TGA image could not be decoded (${(error as Error)?.message ?? "unknown error"}).`
        );
    }

    // tga-js reads the alpha bit of a 16-bit pixel the wrong way round for the usual files (the bit
    // set means opaque), and most 16-bit files carry no alpha at all. Showing them opaque is right
    // for the files that exist.
    if (info.pixelDepth === 16 && !(info.imageType === 3 || info.imageType === 11)) {
        for (let i = 3; i < decoded.data.length; i += 4) decoded.data[i] = 255;
    }
    return { width: decoded.width, height: decoded.height, data: decoded.data };
}

// ---------------------------------------------------------------------------------------------
// TIFF: directory reader
// ---------------------------------------------------------------------------------------------

/** A TIFF image directory in the shape `UTIF.decodeImage` reads: `t<tag>` keys holding arrays. */
export type TiffIfd = Record<string, any>;

export interface TiffPageInfo {
    index: number;
    width: number;
    height: number;
    bitsPerSample: number;
    samplesPerPixel: number;
    photometric: number;
    ifd: TiffIfd;
}

export interface TiffDocument {
    buffer: ArrayBuffer;
    pages: TiffPageInfo[];
}

/** Tags decoding or display needs. Every other entry in a directory is skipped unread. */
const WANTED_TAGS = new Set([
    254, 256, 257, 258, 259, 262, 266, 273, 274, 277, 278, 279, 284, 292, 317, 320, 322, 323, 324,
    325, 338, 339, 347,
]);
/** Tags whose value is a list that can run to many entries (strip and tile tables). */
const CHUNK_TABLE_TAGS = new Set([273, 279, 324, 325]);
const MAX_SMALL_ARRAY = 4096;
const MAX_TABLES_BYTES = 65536;
const TIFF_TYPE_BYTES: Record<number, number> = {
    1: 1,
    2: 1,
    3: 2,
    4: 4,
    5: 8,
    6: 1,
    7: 1,
    8: 2,
    9: 4,
    10: 8,
    11: 4,
    12: 8,
    13: 4,
};

function readTiffValues(
    view: DataView,
    type: number,
    count: number,
    offset: number,
    littleEndian: boolean
): number[] {
    const out: number[] = new Array(count);
    for (let i = 0; i < count; i++) {
        switch (type) {
            case 1:
            case 2:
            case 6:
            case 7:
                out[i] = view.getUint8(offset + i);
                break;
            case 3:
                out[i] = view.getUint16(offset + 2 * i, littleEndian);
                break;
            case 4:
            case 13:
                out[i] = view.getUint32(offset + 4 * i, littleEndian);
                break;
            case 8:
                out[i] = view.getInt16(offset + 2 * i, littleEndian);
                break;
            case 9:
                out[i] = view.getInt32(offset + 4 * i, littleEndian);
                break;
            case 11:
                out[i] = view.getFloat32(offset + 4 * i, littleEndian);
                break;
            case 12:
                out[i] = view.getFloat64(offset + 8 * i, littleEndian);
                break;
            default:
                // Rationals: only the quotient is ever wanted, and none of the wanted tags use one.
                out[i] = 0;
        }
    }
    return out;
}

/**
 * Lists the pages of a TIFF from its directories alone: no strip or tile is read. Throws
 * {@link RasterDecodeError} for BigTIFF, a file that is not a TIFF, and a directory that points
 * outside the file.
 */
export function inspectTiff(buffer: ArrayBuffer): TiffDocument {
    const length = buffer.byteLength;
    if (length < 8) {
        throw new RasterDecodeError("corrupt", "The file is too short to be a TIFF image.");
    }
    const view = new DataView(buffer);
    const order = view.getUint16(0, false);
    if (order !== 0x4949 && order !== 0x4d4d) {
        throw new RasterDecodeError("corrupt", "The file is not a TIFF image.");
    }
    const le = order === 0x4949;
    const magic = view.getUint16(2, le);
    if (magic === 43) {
        throw new RasterDecodeError("unsupported", "BigTIFF files are not supported.");
    }
    if (magic !== 42) {
        throw new RasterDecodeError("corrupt", "The file is not a TIFF image.");
    }

    const pages: TiffPageInfo[] = [];
    const seen = new Set<number>();
    let offset = view.getUint32(4, le);
    while (offset !== 0 && pages.length < MAX_TIFF_PAGES && !seen.has(offset)) {
        seen.add(offset);
        if (offset + 2 > length) {
            throw new RasterDecodeError("corrupt", "A TIFF directory lies outside the file.");
        }
        const entries = view.getUint16(offset, le);
        const next = offset + 2 + entries * 12;
        if (next + 4 > length) {
            throw new RasterDecodeError("corrupt", "A TIFF directory lies outside the file.");
        }
        const ifd: TiffIfd = {};
        for (let i = 0; i < entries; i++) {
            const at = offset + 2 + i * 12;
            const tag = view.getUint16(at, le);
            if (!WANTED_TAGS.has(tag)) continue;
            const type = view.getUint16(at + 2, le);
            const count = view.getUint32(at + 4, le);
            const unit = TIFF_TYPE_BYTES[type];
            if (!unit || count === 0) continue;
            const limit = CHUNK_TABLE_TAGS.has(tag)
                ? MAX_CHUNKS
                : tag === 347
                ? MAX_TABLES_BYTES
                : MAX_SMALL_ARRAY;
            if (count > limit) {
                throw new RasterDecodeError(
                    "unsupported",
                    "The TIFF file has more strips or tiles than the viewer reads."
                );
            }
            const size = count * unit;
            const valueAt = size <= 4 ? at + 8 : view.getUint32(at + 8, le);
            if (valueAt + size > length) {
                throw new RasterDecodeError("corrupt", "A TIFF tag value lies outside the file.");
            }
            ifd[`t${tag}`] =
                tag === 347
                    ? new Uint8Array(buffer, valueAt, size)
                    : readTiffValues(view, type, count, valueAt, le);
        }
        const hasSize = ifd.t256?.[0] > 0 && ifd.t257?.[0] > 0;
        const hasData = ifd.t273 || ifd.t324;
        if (hasSize && hasData) {
            const bps = ifd.t258?.[0] ?? 1;
            pages.push({
                index: pages.length,
                width: ifd.t256[0],
                height: ifd.t257[0],
                bitsPerSample: bps,
                samplesPerPixel: ifd.t277?.[0] ?? 1,
                photometric: ifd.t262?.[0] ?? (bps === 1 ? 0 : 2),
                ifd,
            });
        }
        offset = view.getUint32(next, le);
    }
    if (pages.length === 0) {
        throw new RasterDecodeError("corrupt", "The TIFF file contains no readable image.");
    }
    return { buffer, pages };
}

// ---------------------------------------------------------------------------------------------
// TIFF: checks before decoding
// ---------------------------------------------------------------------------------------------

/** Compressions `utif2` decodes reliably: none, CCITT, LZW, JPEG, deflate, PackBits. */
const SUPPORTED_COMPRESSION = new Set([1, 2, 3, 4, 5, 7, 8, 32773, 32946]);
const DEFLATE = new Set([8, 32946]);
/** Photometric interpretations drawn: min-is-white/black, RGB, palette, CMYK, YCbCr (JPEG only). */
const SUPPORTED_PHOTOMETRIC = new Set([0, 1, 2, 3, 5, 6]);

interface PageLayout {
    width: number;
    height: number;
    bps: number;
    spp: number;
    photometric: number;
    compression: number;
    sampleFormat: number;
    pixels: number;
}

function layoutOf(page: TiffPageInfo): PageLayout {
    const ifd = page.ifd;
    const bpsList: number[] = ifd.t258 ?? [1];
    const spp = ifd.t277?.[0] ?? 1;
    const formats: number[] = ifd.t339 ?? [1];
    if (new Set(bpsList).size > 1 || new Set(formats).size > 1) {
        throw new RasterDecodeError(
            "unsupported",
            "TIFF images with a different bit depth per channel are not supported."
        );
    }
    return {
        width: page.width,
        height: page.height,
        bps: bpsList[0],
        spp,
        photometric: page.photometric,
        compression: ifd.t259?.[0] ?? 1,
        sampleFormat: formats[0],
        pixels: page.width * page.height,
    };
}

/** The first `maxBytes` of `chunk`'s JPEG: its frame width and height, or null. */
function jpegFrameSize(chunk: Uint8Array): { width: number; height: number } | null {
    let i = 0;
    const end = Math.min(chunk.length, 65536) - 9;
    if (chunk[0] !== 0xff || chunk[1] !== 0xd8) return null;
    i = 2;
    while (i < end) {
        if (chunk[i] !== 0xff) return null;
        const marker = chunk[i + 1];
        if (marker === 0xff) {
            i += 1;
            continue;
        }
        if (marker >= 0xc0 && marker <= 0xc2) {
            return {
                height: (chunk[i + 5] << 8) | chunk[i + 6],
                width: (chunk[i + 7] << 8) | chunk[i + 8],
            };
        }
        if (marker === 0xd8 || (marker >= 0xd0 && marker <= 0xd7) || marker === 0x01) {
            i += 2;
            continue;
        }
        i += 2 + ((chunk[i + 2] << 8) | chunk[i + 3]);
    }
    return null;
}

/**
 * Refuses a page the decoder would draw wrongly or spend unbounded memory or time on, before
 * `UTIF.decodeImage` runs: the pixel and decoded-size caps, unsupported variants, and every strip
 * or tile checked against the file and against what the page's own dimensions allow it to hold.
 */
export function assessTiffPage(
    page: TiffPageInfo,
    fileLength: number,
    buffer: ArrayBuffer,
    limits: RasterLimits,
    pako: PakoModule | null
): PageLayout {
    const ifd = page.ifd;
    const layout = layoutOf(page);
    checkPixels(layout.width, layout.height, limits);

    if (ifd.t284?.[0] === 2 && layout.spp > 1) {
        throw new RasterDecodeError(
            "unsupported",
            "TIFF images stored in separate colour planes are not supported."
        );
    }
    if (!SUPPORTED_COMPRESSION.has(layout.compression)) {
        throw new RasterDecodeError(
            "unsupported",
            `TIFF compression ${layout.compression} is not supported.`
        );
    }
    if (!SUPPORTED_PHOTOMETRIC.has(layout.photometric)) {
        throw new RasterDecodeError(
            "unsupported",
            `TIFF colour interpretation ${layout.photometric} is not supported.`
        );
    }
    if (layout.photometric === 6 && layout.compression !== 7) {
        throw new RasterDecodeError(
            "unsupported",
            "This TIFF colour interpretation is not supported."
        );
    }
    if (![1, 2, 4, 8, 16, 32, 64].includes(layout.bps)) {
        throw new RasterDecodeError(
            "unsupported",
            `${layout.bps}-bit TIFF samples are not supported.`
        );
    }
    if (layout.sampleFormat === 4 || (layout.sampleFormat === 3 && layout.bps < 32)) {
        throw new RasterDecodeError("unsupported", "These TIFF sample values are not supported.");
    }
    if (layout.bps > 8 && layout.photometric === 3) {
        throw new RasterDecodeError("unsupported", "16-bit palette TIFF images are not supported.");
    }
    if (layout.photometric === 5 && layout.spp < 4) {
        throw new RasterDecodeError("corrupt", "The CMYK TIFF has fewer than four channels.");
    }
    if (layout.photometric === 2 && layout.spp < 3) {
        throw new RasterDecodeError("corrupt", "The RGB TIFF has fewer than three channels.");
    }
    if (layout.photometric === 3 && !ifd.t320) {
        throw new RasterDecodeError("corrupt", "The palette TIFF has no colour map.");
    }

    const rowBytes = Math.ceil((layout.width * layout.spp * layout.bps) / 8);
    if (rowBytes * layout.height > MAX_SAMPLE_BYTES) {
        throw new RasterDecodeError(
            "too-many-pixels",
            "This image needs more memory to decode than the viewer allows."
        );
    }

    // Strips or tiles: where each lies, and how big its decoded data and JPEG frame may be.
    const tiled = !!ifd.t322;
    const offsets: number[] = (tiled ? ifd.t324 : ifd.t273) ?? [];
    const counts: number[] | undefined = tiled ? ifd.t325 : ifd.t279;
    let chunkRows: number;
    let chunkWidth: number;
    let expected: number;
    if (tiled) {
        const tw = ifd.t322[0];
        const th = ifd.t323?.[0];
        if (!(tw > 0) || !(th > 0)) {
            throw new RasterDecodeError("corrupt", "The TIFF tile size is missing.");
        }
        chunkRows = th;
        chunkWidth = tw;
        expected = Math.ceil(layout.width / tw) * Math.ceil(layout.height / th);
        if (Math.ceil((tw * layout.spp * layout.bps) / 8) * th > MAX_SAMPLE_BYTES) {
            throw new RasterDecodeError("corrupt", "The TIFF tile size is not valid.");
        }
    } else {
        chunkRows = Math.min(ifd.t278?.[0] ?? layout.height, layout.height);
        chunkWidth = layout.width;
        if (!(chunkRows > 0))
            throw new RasterDecodeError("corrupt", "The TIFF strip size is not valid.");
        expected = Math.ceil(layout.height / chunkRows);
    }
    if (offsets.length < expected) {
        throw new RasterDecodeError("corrupt", "The TIFF file is missing image data.");
    }
    if (offsets.length > expected) offsets.length = expected;

    const chunkBytes = Math.ceil((chunkWidth * layout.spp * layout.bps) / 8) * chunkRows;
    const bytes = new Uint8Array(buffer);
    for (let i = 0; i < expected; i++) {
        const size = counts?.[i] ?? (layout.compression === 1 ? chunkBytes : undefined);
        if (size === undefined) {
            throw new RasterDecodeError("corrupt", "The TIFF file is missing strip sizes.");
        }
        if (offsets[i] + size > fileLength) {
            throw new RasterDecodeError("corrupt", "The TIFF file is truncated.");
        }
        if (DEFLATE.has(layout.compression)) {
            boundedInflate(bytes, offsets[i], size, chunkBytes, pako);
        } else if (layout.compression === 7 && size > 0) {
            const frame = jpegFrameSize(bytes.subarray(offsets[i], offsets[i] + size));
            if (
                !frame ||
                frame.width > chunkWidth + 16 ||
                frame.height > chunkRows + 16 ||
                frame.width * frame.height > (chunkWidth + 16) * (chunkRows + 16)
            ) {
                throw new RasterDecodeError("corrupt", "A JPEG strip does not match its TIFF.");
            }
        }
    }
    return layout;
}

/**
 * Inflates a zlib strip once, stopping with an error when it would produce more than the strip can
 * hold. `utif2` inflates the same bytes in one call with no bound, so a small file could otherwise
 * ask for gigabytes.
 */
function boundedInflate(
    bytes: Uint8Array,
    offset: number,
    size: number,
    maxOutput: number,
    pako: PakoModule | null
): void {
    if (!pako || size < 7) {
        throw new RasterDecodeError("corrupt", "A deflate strip is damaged.");
    }
    const limit = maxOutput + 1024;
    let produced = 0;
    // The same slice `utif2` inflates: the 2-byte zlib header and 4-byte checksum are skipped.
    const inflator = new pako.Inflate({ raw: true, chunkSize: 65536 });
    inflator.onData = (chunk: Uint8Array) => {
        produced += chunk.length;
        if (produced > limit) {
            throw new RasterDecodeError(
                "corrupt",
                "A deflate strip holds more data than its size."
            );
        }
    };
    try {
        inflator.push(bytes.subarray(offset + 2, offset + size - 4), true);
    } catch (error) {
        if (error instanceof RasterDecodeError) throw error;
        throw new RasterDecodeError("corrupt", "A deflate strip could not be read.");
    }
}

// ---------------------------------------------------------------------------------------------
// TIFF: decode and convert for display
// ---------------------------------------------------------------------------------------------

/** Whether TIFF orientation `value` (tag 274) swaps width and height (values 5 to 8). */
export function orientationSwapsAxes(value?: number): boolean {
    return value !== undefined && value >= 5 && value <= 8;
}

/**
 * Applies the TIFF Orientation tag (274) so the page is shown the way it is meant to be seen.
 * Values: 1 as stored, 2 mirrored horizontally, 3 rotated 180, 4 mirrored vertically, 5 transposed,
 * 6 rotated 90 clockwise, 7 transversed, 8 rotated 90 counter-clockwise. Any other value is 1.
 */
export function orient(raster: DecodedRaster, value?: number): DecodedRaster {
    if (value === undefined || value < 2 || value > 8) return raster;
    const { width: w, height: h, data } = raster;
    const swap = orientationSwapsAxes(value);
    const dw = swap ? h : w;
    const dh = swap ? w : h;
    const out = new Uint8ClampedArray(data.length);
    for (let y = 0; y < dh; y++) {
        for (let x = 0; x < dw; x++) {
            let sx: number;
            let sy: number;
            switch (value) {
                case 2:
                    sx = w - 1 - x;
                    sy = y;
                    break;
                case 3:
                    sx = w - 1 - x;
                    sy = h - 1 - y;
                    break;
                case 4:
                    sx = x;
                    sy = h - 1 - y;
                    break;
                case 5:
                    sx = y;
                    sy = x;
                    break;
                case 6:
                    sx = y;
                    sy = h - 1 - x;
                    break;
                case 7:
                    sx = w - 1 - y;
                    sy = h - 1 - x;
                    break;
                default:
                    sx = w - 1 - y;
                    sy = x;
            }
            const from = (sy * w + sx) * 4;
            const to = (y * dw + x) * 4;
            out[to] = data[from];
            out[to + 1] = data[from + 1];
            out[to + 2] = data[from + 2];
            out[to + 3] = data[from + 3];
        }
    }
    return { ...raster, width: dw, height: dh, data: out };
}

/** Decodes one page to RGBA. `utif`/`pako` are the loaded libraries. */
export function decodeTiffPage(
    utif: UtifModule,
    pako: PakoModule,
    doc: TiffDocument,
    pageIndex: number,
    limits: RasterLimits
): DecodedRaster {
    const page = doc.pages[pageIndex];
    if (!page) throw new RasterDecodeError("corrupt", "That TIFF page does not exist.");
    const layout = assessTiffPage(page, doc.buffer.byteLength, doc.buffer, limits, pako);

    // `decodeImage` writes the decoded data onto the directory object, so work on a copy.
    const ifd: any = { ...page.ifd };
    try {
        (utif.decodeImage as any)(doc.buffer, ifd, []);
    } catch (error) {
        throw new RasterDecodeError(
            "corrupt",
            `The TIFF image could not be decoded (${String((error as Error)?.message ?? error)}).`
        );
    }
    if (!ifd.data) {
        throw new RasterDecodeError("corrupt", "The TIFF file contains no readable image data.");
    }
    try {
        if (layout.photometric === 3 || layout.photometric === 6) {
            // Palette and YCbCr JPEG keep `utif2`'s own conversion.
            const rgba = utif.toRGBA8(ifd);
            return orient(
                {
                    width: layout.width,
                    height: layout.height,
                    data: new Uint8ClampedArray(rgba.buffer, rgba.byteOffset, rgba.byteLength),
                },
                page.ifd.t274?.[0]
            );
        }
        return orient(toRgba(ifd.data, layout, ifd), page.ifd.t274?.[0]);
    } catch (error) {
        if (error instanceof RasterDecodeError) throw error;
        throw new RasterDecodeError(
            "corrupt",
            `The TIFF image could not be converted for display (${String(
                (error as Error)?.message ?? error
            )}).`
        );
    } finally {
        delete ifd.data;
    }
}

/** A sample reader over the decoded strips: row-major, channels interleaved. */
function sampleReader(
    data: Uint8Array,
    layout: PageLayout,
    littleEndian: boolean
): (row: number, x: number, channel: number) => number {
    const { bps, spp, width } = layout;
    const rowBytes = Math.ceil((width * spp * bps) / 8);
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const signed = layout.sampleFormat === 2;
    const isFloat = layout.sampleFormat === 3;
    switch (bps) {
        case 8:
            return signed
                ? (row, x, c) => view.getInt8(row * rowBytes + x * spp + c)
                : (row, x, c) => data[row * rowBytes + x * spp + c];
        // `utif2` leaves 16-bit samples little-endian whatever the file's byte order.
        case 16:
            return signed
                ? (row, x, c) => view.getInt16(row * rowBytes + 2 * (x * spp + c), true)
                : (row, x, c) => view.getUint16(row * rowBytes + 2 * (x * spp + c), true);
        case 32:
            return isFloat
                ? (row, x, c) => view.getFloat32(row * rowBytes + 4 * (x * spp + c), littleEndian)
                : signed
                ? (row, x, c) => view.getInt32(row * rowBytes + 4 * (x * spp + c), littleEndian)
                : (row, x, c) => view.getUint32(row * rowBytes + 4 * (x * spp + c), littleEndian);
        case 64:
            return isFloat
                ? (row, x, c) => view.getFloat64(row * rowBytes + 8 * (x * spp + c), littleEndian)
                : (row, x, c) =>
                      Number(view.getBigUint64(row * rowBytes + 8 * (x * spp + c), littleEndian));
        default: {
            // 1, 2 and 4 bits: samples packed most significant bit first within a row.
            const mask = (1 << bps) - 1;
            return (row, x, c) => {
                const bit = (x * spp + c) * bps;
                return (data[row * rowBytes + (bit >> 3)] >> (8 - bps - (bit & 7))) & mask;
            };
        }
    }
}

/** What a stored sample value of this type maps from, so it can be put on a 0..1 scale. */
function sampleScale(layout: PageLayout): { offset: number; range: number } {
    if (layout.sampleFormat === 3) return { offset: 0, range: 1 };
    const levels = 2 ** layout.bps - 1;
    return layout.sampleFormat === 2
        ? { offset: levels / 2 + 0.5, range: levels }
        : { offset: 0, range: levels };
}

function toRgba(data: Uint8Array, layout: PageLayout, ifd: any): DecodedRaster {
    const { width, height, spp, bps, photometric } = layout;
    const read = sampleReader(data, layout, ifd.isLE !== false);
    const scale = sampleScale(layout);
    const isGrey = photometric === 0 || photometric === 1;
    const isCmyk = photometric === 5;
    const colorChannels = isCmyk ? 4 : isGrey ? 1 : 3;
    const extra: number[] = ifd.t338 ?? [];
    const alphaChannel =
        !isCmyk && spp > colorChannels && (extra[0] === 1 || extra[0] === 2) ? colorChannels : -1;
    const wide = bps > 8 || layout.sampleFormat === 3;
    // CMYK is ink coverage and is not stretched; grey and RGB data of more than 8 bits are.
    const stretch = wide && !isCmyk;

    const norm = (raw: number) => (raw + scale.offset) / scale.range;

    let lo = 0;
    let hi = 1;
    if (stretch) {
        const step = Math.max(1, Math.floor(Math.sqrt((width * height) / STATS_SAMPLE_PIXELS)));
        let min = Infinity;
        let max = -Infinity;
        for (let y = 0; y < height; y += step) {
            for (let x = 0; x < width; x += step) {
                for (let c = 0; c < colorChannels; c++) {
                    const v = norm(read(y, x, c));
                    if (Number.isFinite(v)) {
                        if (v < min) min = v;
                        if (v > max) max = v;
                    }
                }
            }
        }
        if (min <= max) {
            lo = min;
            hi = max;
            if (max > min) {
                const bins = new Uint32Array(STATS_BINS);
                let total = 0;
                const span = max - min;
                for (let y = 0; y < height; y += step) {
                    for (let x = 0; x < width; x += step) {
                        for (let c = 0; c < colorChannels; c++) {
                            const v = norm(read(y, x, c));
                            if (Number.isFinite(v)) {
                                bins[
                                    Math.min(
                                        STATS_BINS - 1,
                                        Math.floor(((v - min) / span) * STATS_BINS)
                                    )
                                ]++;
                                total++;
                            }
                        }
                    }
                }
                const clip = Math.floor(total * CLIP_FRACTION);
                let seen = 0;
                let loBin = 0;
                for (; loBin < STATS_BINS - 1; loBin++) {
                    seen += bins[loBin];
                    if (seen > clip) break;
                }
                seen = 0;
                let hiBin = STATS_BINS - 1;
                for (; hiBin > 0; hiBin--) {
                    seen += bins[hiBin];
                    if (seen > clip) break;
                }
                if (hiBin > loBin) {
                    lo = min + (loBin / STATS_BINS) * span;
                    hi = min + ((hiBin + 1) / STATS_BINS) * span;
                }
            }
        }
        if (!(hi > lo)) {
            // A constant image: show it as mid-grey rather than dividing by zero.
            hi = lo + 1;
        }
    }
    const inv = 255 / (hi - lo);
    const to8 = (v: number) => {
        if (!Number.isFinite(v)) return 0;
        const out = (v - lo) * inv;
        return out <= 0 ? 0 : out >= 255 ? 255 : Math.round(out);
    };
    const fixed8 = (v: number) => {
        const out = v * 255;
        return !Number.isFinite(out) || out <= 0 ? 0 : out >= 255 ? 255 : Math.round(out);
    };

    const out = new Uint8ClampedArray(width * height * 4);
    const whiteIsZero = photometric === 0;
    for (let y = 0; y < height; y++) {
        let o = y * width * 4;
        for (let x = 0; x < width; x++, o += 4) {
            if (isCmyk) {
                const k = 1 - Math.min(1, Math.max(0, norm(read(y, x, 3))));
                out[o] = fixed8((1 - Math.min(1, Math.max(0, norm(read(y, x, 0))))) * k);
                out[o + 1] = fixed8((1 - Math.min(1, Math.max(0, norm(read(y, x, 1))))) * k);
                out[o + 2] = fixed8((1 - Math.min(1, Math.max(0, norm(read(y, x, 2))))) * k);
            } else if (isGrey) {
                let v = stretch ? to8(norm(read(y, x, 0))) : fixed8(norm(read(y, x, 0)));
                if (whiteIsZero) v = 255 - v;
                out[o] = out[o + 1] = out[o + 2] = v;
            } else if (stretch) {
                out[o] = to8(norm(read(y, x, 0)));
                out[o + 1] = to8(norm(read(y, x, 1)));
                out[o + 2] = to8(norm(read(y, x, 2)));
            } else {
                out[o] = fixed8(norm(read(y, x, 0)));
                out[o + 1] = fixed8(norm(read(y, x, 1)));
                out[o + 2] = fixed8(norm(read(y, x, 2)));
            }
            out[o + 3] = alphaChannel >= 0 ? fixed8(norm(read(y, x, alphaChannel))) : 255;
        }
    }

    const notes: string[] = [];
    if (stretch)
        notes.push(
            `${
                layout.sampleFormat === 3 ? "Floating-point" : `${bps}-bit`
            } values scaled for display`
        );
    if (isCmyk) notes.push("CMYK converted to RGB");
    return {
        width,
        height,
        data: out,
        conversion: notes.length ? notes.join("; ") : undefined,
    };
}

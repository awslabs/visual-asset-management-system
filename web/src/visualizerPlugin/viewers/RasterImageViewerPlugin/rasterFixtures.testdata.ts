/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** Generators for tiny TIFF and TGA files, so no binary fixture is committed. */

export interface TiffPageSpec {
    width: number;
    height: number;
    bps: number;
    spp: number;
    photometric: number;
    /** Strip data as stored (already compressed when `compression` is not 1). */
    data: Uint8Array;
    compression?: number;
    sampleFormat?: number;
    planar?: number;
    /** TIFF Orientation tag (274). */
    orientation?: number;
    extraSamples?: number[];
    /** Overrides the strip byte count written for the single strip. */
    stripByteCount?: number;
}

/** A classic TIFF with one strip per page. */
export function buildTiff(
    pages: TiffPageSpec[],
    options: { littleEndian?: boolean; cyclic?: boolean } = {}
): ArrayBuffer {
    const le = options.littleEndian !== false;
    const out: number[] = [];
    const u16 = (v: number) => (le ? [v & 255, (v >> 8) & 255] : [(v >> 8) & 255, v & 255]);
    const u32 = (v: number) =>
        le
            ? [v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255]
            : [(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255];
    out.push(...(le ? [0x49, 0x49] : [0x4d, 0x4d]), ...u16(42), ...u32(8));

    // Layout: header(8), then per page: strip data, tag value area, IFD.
    let ifdOffsetField = 4;
    pages.forEach((page, index) => {
        const dataOffset = out.length;
        out.push(...page.data);
        if (out.length % 2) out.push(0);
        const entries: [number, number, number[]][] = [];
        const sh = (values: number[]) => values.flatMap(u16);
        const lg = (values: number[]) => values.flatMap(u32);
        const compression = page.compression ?? 1;
        entries.push([256, 4, lg([page.width])]);
        entries.push([257, 4, lg([page.height])]);
        entries.push([258, 3, sh(new Array(page.spp).fill(page.bps))]);
        entries.push([259, 3, sh([compression])]);
        entries.push([262, 3, sh([page.photometric])]);
        entries.push([273, 4, lg([dataOffset])]);
        entries.push([277, 3, sh([page.spp])]);
        entries.push([278, 4, lg([page.height])]);
        entries.push([279, 4, lg([page.stripByteCount ?? page.data.length])]);
        if (page.orientation) entries.push([274, 3, sh([page.orientation])]);
        if (page.planar) entries.push([284, 3, sh([page.planar])]);
        if (page.extraSamples) entries.push([338, 3, sh(page.extraSamples)]);
        if (page.sampleFormat)
            entries.push([339, 3, sh(new Array(page.spp).fill(page.sampleFormat))]);
        entries.sort((a, b) => a[0] - b[0]);

        const valueArea: number[] = [];
        const ifdStart = out.length + 0;
        const valuesStart = ifdStart + 2 + entries.length * 12 + 4;
        const ifdBytes: number[] = [...u16(entries.length)];
        for (const [tag, type, bytes] of entries) {
            const unit = type === 3 ? 2 : 4;
            ifdBytes.push(...u16(tag), ...u16(type), ...u32(bytes.length / unit));
            if (bytes.length <= 4) {
                ifdBytes.push(...bytes, ...new Array(4 - bytes.length).fill(0));
            } else {
                ifdBytes.push(...u32(valuesStart + valueArea.length));
                valueArea.push(...bytes);
            }
        }
        const last = index === pages.length - 1;
        const next = last ? (options.cyclic ? 8 : 0) : valuesStart + valueArea.length;
        ifdBytes.push(...u32(last && options.cyclic ? ifdOffsetOfFirst(out) : next));
        // Patch the previous "next IFD" (or header) pointer to this IFD.
        const patch = u32(ifdStart);
        for (let i = 0; i < 4; i++) out[ifdOffsetField + i] = patch[i];
        out.push(...ifdBytes, ...valueArea);
        ifdOffsetField = valuesStart - 4;
    });
    return new Uint8Array(out).buffer;
}

/** The offset of the first IFD, read back from the header being built. */
function ifdOffsetOfFirst(out: number[]): number {
    const le = out[0] === 0x49;
    const bytes = out.slice(4, 8);
    return le
        ? bytes[0] | (bytes[1] << 8) | (bytes[2] << 16) | (bytes[3] << 24)
        : (bytes[0] << 24) | (bytes[1] << 16) | (bytes[2] << 8) | bytes[3];
}

/** Little-endian 16-bit samples as bytes. */
export function u16le(values: number[]): Uint8Array {
    const out = new Uint8Array(values.length * 2);
    values.forEach((v, i) => {
        out[2 * i] = v & 255;
        out[2 * i + 1] = v >> 8;
    });
    return out;
}

export function f32le(values: number[]): Uint8Array {
    const out = new Uint8Array(values.length * 4);
    const view = new DataView(out.buffer);
    values.forEach((v, i) => view.setFloat32(4 * i, v, true));
    return out;
}

/** A run-length 24-bit TGA made of one raw packet per row, so it can be cut short. */
export function buildRleTga(width: number, height: number, pixels: number[][]): Uint8Array {
    const header = new Uint8Array(18);
    header[2] = 10;
    header[12] = width & 255;
    header[13] = width >> 8;
    header[14] = height & 255;
    header[15] = height >> 8;
    header[16] = 24;
    const body: number[] = [];
    for (let y = 0; y < height; y++) {
        body.push(width - 1);
        for (let x = 0; x < width; x++) {
            const [r, g, b] = pixels[y * width + x];
            body.push(b, g, r);
        }
    }
    const out = new Uint8Array(header.length + body.length);
    out.set(header);
    out.set(body, header.length);
    return out;
}

/** An uncompressed 24-bit TGA, bottom-left origin unless `topLeft`. Pixels are [r, g, b]. */
export function buildTga(
    width: number,
    height: number,
    pixels: number[][],
    options: { topLeft?: boolean; type?: number; depth?: number } = {}
): Uint8Array {
    const depth = options.depth ?? 24;
    const bytesPer = depth >> 3;
    const header = new Uint8Array(18);
    header[2] = options.type ?? 2;
    header[12] = width & 255;
    header[13] = width >> 8;
    header[14] = height & 255;
    header[15] = height >> 8;
    header[16] = depth;
    header[17] = options.topLeft ? 0x20 : 0;
    const body = new Uint8Array(width * height * bytesPer);
    pixels.forEach(([r, g, b, a], i) => {
        body[i * bytesPer] = b;
        body[i * bytesPer + 1] = g;
        body[i * bytesPer + 2] = r;
        if (bytesPer === 4) body[i * bytesPer + 3] = a ?? 255;
    });
    const out = new Uint8Array(header.length + body.length);
    out.set(header);
    out.set(body, header.length);
    return out;
}

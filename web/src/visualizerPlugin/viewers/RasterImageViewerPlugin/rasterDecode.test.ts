/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import UTIF from "utif2";
import TGA from "tga-js";
import pako from "pako";
import {
    RasterDecodeError,
    decodeTga,
    orient,
    decodeTiffPage,
    inspectTiff,
    limitsFromParameters,
    rasterKindOf,
    readTgaHeader,
} from "./rasterDecode";
import { buildRleTga, buildTga, buildTiff, f32le, u16le } from "./rasterFixtures.testdata";

const limits = { maxBytes: 1 << 20, maxPixels: 10_000 };
const utif: any = UTIF;
const pk: any = pako;

function decode(buffer: ArrayBuffer, page = 0, custom = limits) {
    return decodeTiffPage(utif, pk, inspectTiff(buffer), page, custom);
}

function reasonOf(fn: () => unknown): string | undefined {
    try {
        fn();
    } catch (error) {
        expect(error).toBeInstanceOf(RasterDecodeError);
        return (error as RasterDecodeError).reason;
    }
    return undefined;
}

describe("rasterKindOf and limitsFromParameters", () => {
    it("maps the three extensions and nothing else", () => {
        expect(rasterKindOf("a/b/Scan.TIF")).toBe("tiff");
        expect(rasterKindOf("x.tiff")).toBe("tiff");
        expect(rasterKindOf("x.TGA")).toBe("tga");
        expect(rasterKindOf("x.png")).toBeNull();
        expect(rasterKindOf("tga")).toBeNull();
    });

    it("keeps the defaults for a missing or invalid cap", () => {
        expect(limitsFromParameters(undefined)).toEqual({
            maxBytes: 268435456,
            maxPixels: 100000000,
        });
        expect(limitsFromParameters({ maxDecodeBytes: -1, maxDecodePixels: "x" })).toEqual({
            maxBytes: 268435456,
            maxPixels: 100000000,
        });
        expect(limitsFromParameters({ maxDecodeBytes: 10, maxDecodePixels: 20 })).toEqual({
            maxBytes: 10,
            maxPixels: 20,
        });
    });
});

describe("TGA", () => {
    const pixels = [
        [255, 0, 0],
        [0, 255, 0],
        [0, 0, 255],
        [255, 255, 255],
    ];

    it("decodes a bottom-left 24-bit image, rows top to bottom", () => {
        // Stored bottom row first: bottom = red, green; top = blue, white.
        const file = buildTga(2, 2, pixels);
        const out = decodeTga(file, TGA as any, limits);
        expect([out.width, out.height]).toEqual([2, 2]);
        expect(Array.from(out.data.slice(0, 4))).toEqual([0, 0, 255, 255]); // top-left blue
        expect(Array.from(out.data.slice(8, 12))).toEqual([255, 0, 0, 255]); // bottom-left red
    });

    it("honours a top-left origin and 32-bit alpha", () => {
        const file = buildTga(
            2,
            2,
            pixels.map((p, i) => [...p, i * 50]),
            {
                topLeft: true,
                depth: 32,
            }
        );
        const out = decodeTga(file, TGA as any, limits);
        expect(Array.from(out.data.slice(0, 4))).toEqual([255, 0, 0, 0]);
        expect(out.data[7]).toBe(50);
    });

    it("refuses an image above the pixel cap before decoding", () => {
        const file = buildTga(2, 2, pixels);
        expect(reasonOf(() => decodeTga(file, TGA as any, { ...limits, maxPixels: 3 }))).toBe(
            "too-many-pixels"
        );
    });

    it("refuses a header that claims a huge image without allocating it", () => {
        const header = new Uint8Array(18);
        header[2] = 2;
        header[12] = header[13] = header[14] = header[15] = 255;
        header[16] = 24;
        expect(reasonOf(() => decodeTga(header, TGA as any, limits))).toBe("too-many-pixels");
    });

    it("refuses a truncated uncompressed file, a short file and an unknown type", () => {
        const file = buildTga(2, 2, pixels);
        expect(reasonOf(() => decodeTga(file.slice(0, 25), TGA as any, limits))).toBe("corrupt");
        expect(reasonOf(() => readTgaHeader(new Uint8Array(5)))).toBe("corrupt");
        const odd = buildTga(2, 2, pixels, { type: 7 });
        expect(reasonOf(() => readTgaHeader(odd))).toBe("unsupported");
    });
});

describe("TIFF pages", () => {
    const rgb = new Uint8Array([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255]);

    it("decodes 8-bit RGB, little- and big-endian files", () => {
        for (const littleEndian of [true, false]) {
            const tiff = buildTiff(
                [{ width: 2, height: 2, bps: 8, spp: 3, photometric: 2, data: rgb }],
                { littleEndian }
            );
            const out = decode(tiff);
            expect(Array.from(out.data.slice(0, 8))).toEqual([255, 0, 0, 255, 0, 255, 0, 255]);
            expect(out.conversion).toBeUndefined();
        }
    });

    it("lists every page of a multi-page file and decodes each", () => {
        const page = (v: number) => ({
            width: 2,
            height: 1,
            bps: 8,
            spp: 1,
            photometric: 1,
            data: new Uint8Array([v, v]),
        });
        const tiff = buildTiff([page(10), page(100), page(200)]);
        const doc = inspectTiff(tiff);
        expect(doc.pages.map((p) => p.width)).toEqual([2, 2, 2]);
        expect(decode(tiff, 1).data[0]).toBe(100);
        expect(decode(tiff, 2).data[0]).toBe(200);
    });

    it("scales 16-bit grey data that uses only the low 12 bits instead of showing black", () => {
        const tiff = buildTiff([
            {
                width: 4,
                height: 1,
                bps: 16,
                spp: 1,
                photometric: 1,
                data: u16le([0, 1000, 2000, 4095]),
            },
        ]);
        const out = decode(tiff);
        const grey = [0, 4, 8, 12].map((i) => out.data[i]);
        expect(grey[0]).toBe(0);
        expect(grey[3]).toBe(255);
        expect(grey[1]).toBeGreaterThan(30);
        expect(grey[2]).toBeGreaterThan(grey[1]);
        expect(out.conversion).toMatch(/16-bit values scaled/);
    });

    it("reads 16-bit samples of a big-endian file", () => {
        const be = new Uint8Array([0, 0, 0x10, 0, 0x20, 0, 0x30, 0]);
        const tiff = buildTiff(
            [{ width: 4, height: 1, bps: 16, spp: 1, photometric: 1, data: be }],
            { littleEndian: false }
        );
        const out = decode(tiff);
        expect(out.data[0]).toBe(0);
        expect(out.data[12]).toBe(255);
        expect(out.data[4]).toBeGreaterThan(0);
        expect(out.data[4]).toBeLessThan(out.data[8]);
    });

    it("scales floating-point data and ignores NaN", () => {
        const tiff = buildTiff([
            {
                width: 4,
                height: 1,
                bps: 32,
                spp: 1,
                photometric: 1,
                sampleFormat: 3,
                data: f32le([0, 0.5, NaN, 1]),
            },
        ]);
        const out = decode(tiff);
        expect(out.data[0]).toBe(0);
        expect(out.data[12]).toBe(255);
        expect(out.data[4]).toBeGreaterThan(100);
        expect(out.conversion).toMatch(/Floating-point/);
    });

    it("converts 8-bit and 16-bit CMYK to RGB", () => {
        const cmyk8 = buildTiff([
            {
                width: 2,
                height: 1,
                bps: 8,
                spp: 4,
                photometric: 5,
                // cyan ink only; no ink at all
                data: new Uint8Array([255, 0, 0, 0, 0, 0, 0, 0]),
            },
        ]);
        const out8 = decode(cmyk8);
        expect(Array.from(out8.data.slice(0, 4))).toEqual([0, 255, 255, 255]);
        expect(Array.from(out8.data.slice(4, 8))).toEqual([255, 255, 255, 255]);
        const cmyk16 = buildTiff([
            {
                width: 1,
                height: 1,
                bps: 16,
                spp: 4,
                photometric: 5,
                data: u16le([0, 0, 0, 65535]), // full black ink
            },
        ]);
        expect(Array.from(decode(cmyk16).data)).toEqual([0, 0, 0, 255]);
    });

    it("keeps an alpha channel and inverts white-is-zero data", () => {
        const rgba = buildTiff([
            {
                width: 1,
                height: 1,
                bps: 8,
                spp: 4,
                photometric: 2,
                extraSamples: [2],
                data: new Uint8Array([10, 20, 30, 128]),
            },
        ]);
        expect(Array.from(decode(rgba).data)).toEqual([10, 20, 30, 128]);
        const wiz = buildTiff([
            {
                width: 1,
                height: 1,
                bps: 8,
                spp: 1,
                photometric: 0,
                data: new Uint8Array([40]),
            },
        ]);
        expect(decode(wiz).data[0]).toBe(215);
    });

    it("unpacks 1-bit data", () => {
        const tiff = buildTiff([
            { width: 8, height: 1, bps: 1, spp: 1, photometric: 1, data: new Uint8Array([0xa0]) },
        ]);
        const out = decode(tiff);
        expect([0, 1, 2, 3].map((x) => out.data[x * 4])).toEqual([255, 0, 255, 0]);
    });

    it("inflates a deflate strip", () => {
        const raw = new Uint8Array(16).fill(77);
        const tiff = buildTiff([
            {
                width: 4,
                height: 4,
                bps: 8,
                spp: 1,
                photometric: 1,
                compression: 8,
                data: pk.deflate(raw),
            },
        ]);
        expect(decode(tiff).data[0]).toBe(77);
    });
});

describe("TIFF refusals", () => {
    const page = {
        width: 2,
        height: 2,
        bps: 8,
        spp: 1,
        photometric: 1,
        data: new Uint8Array(4),
    };

    it("refuses a page above the pixel cap before decoding it", () => {
        const tiff = buildTiff([{ ...page, width: 200, height: 200, data: new Uint8Array(40000) }]);
        expect(reasonOf(() => decode(tiff))).toBe("too-many-pixels");
    });

    it("refuses a page whose decoded size is beyond what the viewer allows", () => {
        const tiff = buildTiff([
            { ...page, width: 100000, height: 100000, data: new Uint8Array(4) },
        ]);
        expect(reasonOf(() => decode(tiff, 0, { maxBytes: 1, maxPixels: 100000 * 100000 }))).toBe(
            "too-many-pixels"
        );
    });

    it("stops at a cyclic directory chain instead of looping", () => {
        const tiff = buildTiff([page, page], { cyclic: true });
        expect(inspectTiff(tiff).pages).toHaveLength(2);
    });

    it("refuses a tag that claims billions of values", () => {
        const tiff = buildTiff([page]);
        const bytes = new Uint8Array(tiff);
        const view = new DataView(tiff);
        // The first IFD entry is ImageWidth; make it a SHORT array of 4 billion values.
        const ifd = view.getUint32(4, true);
        view.setUint16(ifd + 2 + 2, 3, true);
        view.setUint32(ifd + 2 + 4, 0xfffffff0, true);
        expect(bytes.length).toBeGreaterThan(0);
        expect(reasonOf(() => decode(tiff))).toBeDefined();
    });

    it("refuses a strip that lies outside the file", () => {
        const tiff = buildTiff([{ ...page, stripByteCount: 0x7ffffff0 }]);
        expect(reasonOf(() => decode(tiff))).toBe("corrupt");
    });

    it("refuses a deflate strip that inflates far beyond its size", () => {
        const bomb = pk.deflate(new Uint8Array(5_000_000));
        const tiff = buildTiff([{ ...page, compression: 8, data: bomb }]);
        expect(reasonOf(() => decode(tiff))).toBe("corrupt");
    });

    it("refuses BigTIFF, planar, unknown compression and non-TIFF data", () => {
        const big = new Uint8Array(16);
        big.set([0x49, 0x49, 43, 0, 8, 0, 0, 0]);
        expect(reasonOf(() => inspectTiff(big.buffer))).toBe("unsupported");
        expect(reasonOf(() => inspectTiff(new Uint8Array(16).buffer))).toBe("corrupt");
        const planar = buildTiff([
            { ...page, spp: 3, photometric: 2, planar: 2, data: new Uint8Array(12) },
        ]);
        expect(reasonOf(() => decode(planar))).toBe("unsupported");
        const odd = buildTiff([{ ...page, compression: 34712 }]);
        expect(reasonOf(() => decode(odd))).toBe("unsupported");
    });
});

describe("TGA run-length data", () => {
    const pixels = [
        [255, 0, 0],
        [0, 255, 0],
        [0, 0, 255],
        [9, 9, 9],
    ];

    it("decodes a complete run-length image", () => {
        const out = decodeTga(buildRleTga(2, 2, pixels), TGA as any, limits);
        expect([out.width, out.height]).toEqual([2, 2]);
    });

    it("refuses one whose packets end before every pixel is covered", () => {
        const file = buildRleTga(2, 2, pixels);
        for (const cut of [file.length - 1, file.length - 8, 20]) {
            expect(reasonOf(() => decodeTga(file.slice(0, cut), TGA as any, limits))).toBe(
                "corrupt"
            );
        }
    });
});

describe("TIFF orientation (tag 274)", () => {
    // Stored 3 x 2, values: row 0 = 10 20 30, row 1 = 40 50 60.
    const stored = new Uint8Array([10, 20, 30, 40, 50, 60]);
    const grey = (o?: number) =>
        decode(
            buildTiff([
                {
                    width: 3,
                    height: 2,
                    bps: 8,
                    spp: 1,
                    photometric: 1,
                    data: stored,
                    orientation: o,
                },
            ])
        );
    const rows = (r: { width: number; height: number; data: Uint8ClampedArray }) =>
        Array.from({ length: r.height }, (_, y) =>
            Array.from({ length: r.width }, (_, x) => r.data[(y * r.width + x) * 4])
        );

    it.each([
        [
            1,
            [
                [10, 20, 30],
                [40, 50, 60],
            ],
        ],
        [
            2,
            [
                [30, 20, 10],
                [60, 50, 40],
            ],
        ],
        [
            3,
            [
                [60, 50, 40],
                [30, 20, 10],
            ],
        ],
        [
            4,
            [
                [40, 50, 60],
                [10, 20, 30],
            ],
        ],
        [
            5,
            [
                [10, 40],
                [20, 50],
                [30, 60],
            ],
        ],
        [
            6,
            [
                [40, 10],
                [50, 20],
                [60, 30],
            ],
        ],
        [
            7,
            [
                [60, 30],
                [50, 20],
                [40, 10],
            ],
        ],
        [
            8,
            [
                [30, 60],
                [20, 50],
                [10, 40],
            ],
        ],
    ])("shows orientation %i the way it is meant to be seen", (value, expected) => {
        expect(rows(grey(value as number))).toEqual(expected);
    });

    it("treats a missing or unknown value as stored", () => {
        expect(rows(grey(undefined))).toEqual(rows(grey(1)));
        expect(rows(grey(9))).toEqual(rows(grey(1)));
        const same = { width: 1, height: 1, data: new Uint8ClampedArray(4) };
        expect(orient(same, 0)).toBe(same);
    });
});

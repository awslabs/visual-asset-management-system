/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The text viewers are offered by NAME, so they can be handed an image, an archive or a compiled
 * binary. These tests pin the classification of a file's first bytes and the loader's request
 * pattern: at most the first 8 KiB of a binary file is ever read (#1198).
 */

import {
    classifyTextSample,
    CONTROL_BYTE_RATIO_LIMIT,
    CONTROL_BYTE_RATIO_LIMIT_INVALID_UTF8,
    decodeText,
    loadTextFile,
    TEXT_SAMPLE_BYTES,
    TextFetchError,
} from "./textContent";

const utf8 = (text: string) => new TextEncoder().encode(text);

const utf16 = (text: string, order: "le" | "be", bom: boolean): Uint8Array => {
    const out = new Uint8Array((bom ? 2 : 0) + text.length * 2);
    let offset = 0;
    if (bom) {
        out.set(order === "le" ? [0xff, 0xfe] : [0xfe, 0xff]);
        offset = 2;
    }
    for (let i = 0; i < text.length; i++) {
        const code = text.charCodeAt(i);
        out[offset + i * 2] = order === "le" ? code & 0xff : code >> 8;
        out[offset + i * 2 + 1] = order === "le" ? code >> 8 : code & 0xff;
    }
    return out;
};

const PNG_HEAD = new Uint8Array([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
]);

/** Deterministic pseudo-random bytes with every 0x00 replaced, so only the ratio rule can fire. */
const noisyBytes = (length: number): Uint8Array => {
    const out = new Uint8Array(length);
    let seed = 12345;
    for (let i = 0; i < length; i++) {
        seed = (seed * 1103515245 + 12345) & 0x7fffffff;
        out[i] = (seed >> 16) & 0xff || 0x01;
    }
    return out;
};

/** `count` suspect control bytes (0x01) in an otherwise plain ASCII sample of `length`. */
const withControlBytes = (length: number, count: number): Uint8Array => {
    const out = new Uint8Array(length).fill(0x61);
    for (let i = 0; i < count; i++) out[i * Math.floor(length / count)] = 0x01;
    return out;
};

describe("classifyTextSample", () => {
    it("treats an empty file as text", () => {
        expect(classifyTextSample(new Uint8Array(), true)).toEqual({
            kind: "text",
            encoding: "utf-8",
        });
    });

    it("reads plain UTF-8, with and without a byte-order mark", () => {
        expect(classifyTextSample(utf8("Apache License\nVersion 2.0\n"), true)).toEqual({
            kind: "text",
            encoding: "utf-8",
        });
        const bom = new Uint8Array([0xef, 0xbb, 0xbf, ...utf8("héllo")]);
        expect(classifyTextSample(bom, true)).toEqual({ kind: "text", encoding: "utf-8" });
    });

    it("reads UTF-16 with a byte-order mark in either order despite its NUL bytes", () => {
        expect(classifyTextSample(utf16("README", "le", true), true)).toEqual({
            kind: "text",
            encoding: "utf-16le",
        });
        expect(classifyTextSample(utf16("README", "be", true), true)).toEqual({
            kind: "text",
            encoding: "utf-16be",
        });
    });

    it("refuses UTF-32 in either order, checked before the UTF-16 marks it begins with", () => {
        expect(
            classifyTextSample(new Uint8Array([0xff, 0xfe, 0x00, 0x00, 0x41, 0, 0, 0]), true)
        ).toEqual({ kind: "unsupported-encoding" });
        expect(
            classifyTextSample(new Uint8Array([0x00, 0x00, 0xfe, 0xff, 0, 0, 0, 0x41]), true)
        ).toEqual({ kind: "unsupported-encoding" });
    });

    it("names UTF-16 without a byte-order mark an unsupported encoding, not binary", () => {
        const text = "Copyright (c) 2026 Example\nPermission is hereby granted";
        expect(classifyTextSample(utf16(text, "le", false), true)).toEqual({
            kind: "unsupported-encoding",
        });
        expect(classifyTextSample(utf16(text, "be", false), true)).toEqual({
            kind: "unsupported-encoding",
        });
    });

    it("calls any NUL byte without a UTF-16 mark binary (PNG, ZIP, gzip, ELF)", () => {
        expect(classifyTextSample(PNG_HEAD, false)).toEqual({ kind: "binary" });
        expect(
            classifyTextSample(new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x14, 0, 0, 0]), false)
        ).toEqual({ kind: "binary" });
        expect(
            classifyTextSample(new Uint8Array([0x1f, 0x8b, 0x08, 0x00, 0, 0, 0, 0]), false)
        ).toEqual({ kind: "binary" });
        expect(
            classifyTextSample(new Uint8Array([0x7f, 0x45, 0x4c, 0x46, 2, 1, 1, 0]), false)
        ).toEqual({ kind: "binary" });
        // One NUL in otherwise plain text is still binary (git's rule).
        expect(
            classifyTextSample(new Uint8Array([...utf8("abc"), 0, ...utf8("def")]), true)
        ).toEqual({ kind: "binary" });
    });

    it("keeps ANSI-coloured logs, backspace overstrike and form feeds as text", () => {
        const log = utf8("\u001b[31mERROR\u001b[0m disk full\n\fpage 2\nb\bbold\tcol\r\n");
        expect(classifyTextSample(log, true)).toEqual({ kind: "text", encoding: "utf-8" });
    });

    it("keeps Latin-1 text (invalid UTF-8, few control bytes) as text, as before", () => {
        const latin1 = new Uint8Array([
            ...utf8("caf"),
            0xe9,
            ...utf8(" cr"),
            0xe8,
            ...utf8("me\n"),
        ]);
        expect(classifyTextSample(latin1, true)).toEqual({ kind: "text", encoding: "utf-8" });
    });

    it("calls random bytes binary even when they hold no NUL", () => {
        expect(classifyTextSample(noisyBytes(TEXT_SAMPLE_BYTES), false)).toEqual({
            kind: "binary",
        });
    });

    it("does not count a multi-byte character cut at the sample edge as invalid", () => {
        // 'é' is 0xC3 0xA9; the sample ends after 0xC3. With a share of control bytes between the
        // two limits, a sample wrongly judged invalid UTF-8 would flip to binary.
        const length = 1000;
        const count = Math.round(
            length * ((CONTROL_BYTE_RATIO_LIMIT + CONTROL_BYTE_RATIO_LIMIT_INVALID_UTF8) / 2)
        );
        const sample = withControlBytes(length, count);
        sample[length - 1] = 0xc3;
        expect(classifyTextSample(sample, false)).toEqual({ kind: "text", encoding: "utf-8" });
        // The same bytes as a WHOLE file end in a truncated character: invalid, so binary.
        expect(classifyTextSample(sample, true)).toEqual({ kind: "binary" });
    });

    it("applies the control-byte limits at their edges", () => {
        const length = 1000;
        const atLimit = Math.floor(length * CONTROL_BYTE_RATIO_LIMIT);
        expect(classifyTextSample(withControlBytes(length, atLimit), true).kind).toBe("text");
        expect(classifyTextSample(withControlBytes(length, atLimit + 1), true).kind).toBe("binary");

        const invalid = (count: number) => {
            const sample = withControlBytes(length, count);
            sample[length - 2] = 0xff; // never valid in UTF-8
            return sample;
        };
        const atInvalidLimit = Math.floor(length * CONTROL_BYTE_RATIO_LIMIT_INVALID_UTF8);
        expect(classifyTextSample(invalid(atInvalidLimit), true).kind).toBe("text");
        expect(classifyTextSample(invalid(atInvalidLimit + 1), true).kind).toBe("binary");
    });
});

describe("decodeText", () => {
    it("strips the byte-order mark of the detected encoding", () => {
        expect(decodeText(utf16("LICENSE", "le", true), "utf-16le")).toBe("LICENSE");
        expect(decodeText(utf16("LICENSE", "be", true), "utf-16be")).toBe("LICENSE");
        expect(decodeText(new Uint8Array([0xef, 0xbb, 0xbf, ...utf8("x")]), "utf-8")).toBe("x");
    });
});

// ---------------------------------------------------------------------------------------------
// loadTextFile
// ---------------------------------------------------------------------------------------------

interface FakeResponseInit {
    status: number;
    bytes?: Uint8Array;
    contentRange?: string;
    /** Deliver the body as a stream of chunks of this size (a 200 the loader reads incrementally). */
    chunkSize?: number;
}

/** A Response-like object, with an observable stream when `chunkSize` is set. */
const fakeResponse = ({
    status,
    bytes = new Uint8Array(),
    contentRange,
    chunkSize,
}: FakeResponseInit) => {
    const stream = { reads: 0, cancelled: false };
    let offset = 0;
    const response: any = {
        ok: status >= 200 && status < 300,
        status,
        headers: {
            get: (name: string) => (name === "Content-Range" ? contentRange ?? null : null),
        },
        arrayBuffer: async () => bytes.slice().buffer,
        body: chunkSize
            ? {
                  getReader: () => ({
                      read: async () => {
                          if (stream.cancelled || offset >= bytes.length) {
                              return { done: true, value: undefined };
                          }
                          stream.reads++;
                          const value = bytes.slice(offset, offset + chunkSize);
                          offset += chunkSize;
                          return { done: false, value };
                      },
                      cancel: async () => {
                          stream.cancelled = true;
                      },
                  }),
              }
            : undefined,
    };
    return { response, stream };
};

const rangeHeaderOf = (init: any): string | undefined => init?.headers?.Range;

const bigText = (bytes: number) => utf8("x".repeat(bytes - 1) + "\n");

describe("loadTextFile", () => {
    it("asks for the first 8 KiB and decodes a small file from that one 206", async () => {
        const body = utf8("MIT License\n");
        const fetchImpl = jest.fn(
            async () =>
                fakeResponse({
                    status: 206,
                    bytes: body,
                    contentRange: `bytes 0-${body.length - 1}/${body.length}`,
                }).response
        );

        const result = await loadTextFile("https://s3/x", undefined, fetchImpl as any);

        expect(result).toEqual({ kind: "text", text: "MIT License\n", encoding: "utf-8" });
        expect(fetchImpl).toHaveBeenCalledTimes(1);
        expect(rangeHeaderOf((fetchImpl.mock.calls[0] as any[])[1])).toBe(
            `bytes=0-${TEXT_SAMPLE_BYTES - 1}`
        );
    });

    it("follows a text 206 of a larger file with exactly one full GET", async () => {
        const whole = bigText(20000);
        const fetchImpl = jest.fn(async (_url: string, init: any) =>
            rangeHeaderOf(init)
                ? fakeResponse({
                      status: 206,
                      bytes: whole.slice(0, TEXT_SAMPLE_BYTES),
                      contentRange: `bytes 0-${TEXT_SAMPLE_BYTES - 1}/${whole.length}`,
                  }).response
                : fakeResponse({ status: 200, bytes: whole }).response
        );

        const result = await loadTextFile("https://s3/x", undefined, fetchImpl as any);

        expect(result.kind).toBe("text");
        expect((result as any).text.length).toBe(20000);
        expect(fetchImpl).toHaveBeenCalledTimes(2);
        expect(rangeHeaderOf((fetchImpl.mock.calls[1] as any[])[1])).toBeUndefined();
    });

    it("stops after the 206 of a binary file: no second request", async () => {
        const fetchImpl = jest.fn(
            async () =>
                fakeResponse({ status: 206, bytes: PNG_HEAD, contentRange: "bytes 0-8191/1048576" })
                    .response
        );

        expect(await loadTextFile("https://s3/x", undefined, fetchImpl as any)).toEqual({
            kind: "binary",
        });
        expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it("decodes a UTF-16 206 with its BOM in the detected encoding", async () => {
        const body = utf16("Apache License", "be", true);
        const fetchImpl = jest.fn(
            async () =>
                fakeResponse({
                    status: 206,
                    bytes: body,
                    contentRange: `bytes 0-${body.length - 1}/${body.length}`,
                }).response
        );

        expect(await loadTextFile("https://s3/x", undefined, fetchImpl as any)).toEqual({
            kind: "text",
            text: "Apache License",
            encoding: "utf-16be",
        });
    });

    it("judges a 206 without a total by its length", async () => {
        const body = utf8("short");
        const fetchImpl = jest.fn(async () => fakeResponse({ status: 206, bytes: body }).response);
        expect(await loadTextFile("https://s3/x", undefined, fetchImpl as any)).toEqual({
            kind: "text",
            text: "short",
            encoding: "utf-8",
        });
        expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it("reads a 200 (Range ignored) as a stream and cancels it on binary content", async () => {
        const binary = new Uint8Array(1024 * 1024);
        binary.set(PNG_HEAD);
        const { response, stream } = fakeResponse({ status: 200, bytes: binary, chunkSize: 4096 });
        const fetchImpl = jest.fn(async () => response);

        expect(await loadTextFile("https://s3/x", undefined, fetchImpl as any)).toEqual({
            kind: "binary",
        });
        expect(stream.cancelled).toBe(true);
        // Two 4 KiB chunks make the 8 KiB sample; the remaining ~1 MiB is never read.
        expect(stream.reads).toBe(2);
        expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it("keeps reading the same 200 stream for a text file (one request)", async () => {
        const whole = bigText(30000);
        const { response, stream } = fakeResponse({ status: 200, bytes: whole, chunkSize: 4096 });
        const fetchImpl = jest.fn(async () => response);

        const result = await loadTextFile("https://s3/x", undefined, fetchImpl as any);

        expect(result.kind).toBe("text");
        expect((result as any).text).toBe(new TextDecoder().decode(whole));
        expect(stream.cancelled).toBe(false);
        expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it("treats S3's 416 for a range on an empty object as empty text", async () => {
        const fetchImpl = jest.fn(async () => fakeResponse({ status: 416 }).response);
        expect(await loadTextFile("https://s3/x", undefined, fetchImpl as any)).toEqual({
            kind: "text",
            text: "",
            encoding: "utf-8",
        });
    });

    it("retries once without Range when the ranged request fails at the network (CORS)", async () => {
        const body = utf8("NOTICE\n");
        const fetchImpl = jest.fn(async (_url: string, init: any) => {
            if (rangeHeaderOf(init)) throw new TypeError("Failed to fetch");
            return fakeResponse({ status: 200, bytes: body, chunkSize: 4096 }).response;
        });

        expect(await loadTextFile("https://s3/x", undefined, fetchImpl as any)).toEqual({
            kind: "text",
            text: "NOTICE\n",
            encoding: "utf-8",
        });
        expect(fetchImpl).toHaveBeenCalledTimes(2);
    });

    it("throws the HTTP status of a refused request so a viewer can classify it", async () => {
        const fetchImpl = jest.fn(async () => fakeResponse({ status: 403 }).response);
        await expect(loadTextFile("https://s3/x", undefined, fetchImpl as any)).rejects.toEqual(
            expect.objectContaining({ name: "TextFetchError", status: 403 })
        );
        await expect(
            loadTextFile("https://s3/x", undefined, fetchImpl as any)
        ).rejects.toBeInstanceOf(TextFetchError);
    });

    it("rethrows an abort without retrying", async () => {
        const controller = new AbortController();
        controller.abort();
        const abortError = Object.assign(new Error("aborted"), { name: "AbortError" });
        const fetchImpl = jest.fn(async () => {
            throw abortError;
        });

        await expect(
            loadTextFile("https://s3/x", controller.signal, fetchImpl as any)
        ).rejects.toBe(abortError);
        expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it("does not retry a network TypeError once the load was aborted", async () => {
        const controller = new AbortController();
        const fetchImpl = jest.fn(async () => {
            controller.abort();
            throw new TypeError("Failed to fetch");
        });
        await expect(
            loadTextFile("https://s3/x", controller.signal, fetchImpl as any)
        ).rejects.toBeInstanceOf(TypeError);
        expect(fetchImpl).toHaveBeenCalledTimes(1);
    });
});

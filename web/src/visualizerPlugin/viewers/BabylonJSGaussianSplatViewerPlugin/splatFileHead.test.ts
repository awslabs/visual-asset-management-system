/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { identifySplatFile, readFileHead, unsupportedSplatMessage } from "./splatFileHead";

/** The 32-byte plaintext header an SPZ version 4 file opens with: NGSP, version, point count. */
const spz4Head = (version = 4) => {
    const head = new Uint8Array(32);
    head.set([0x4e, 0x47, 0x53, 0x50]);
    new DataView(head.buffer).setUint32(4, version, true);
    new DataView(head.buffer).setUint32(8, 20600, true);
    return head;
};

describe("identifySplatFile", () => {
    it("recognizes the gzip container of SPZ versions 2 and 3", () => {
        expect(identifySplatFile(new Uint8Array([0x1f, 0x8b, 0x08, 0x00]))).toEqual({
            kind: "spz-gzip",
        });
    });

    it("recognizes SPZ version 4 and reads its version number", () => {
        expect(identifySplatFile(spz4Head())).toEqual({ kind: "spz-ngsp", version: 4 });
        expect(identifySplatFile(spz4Head(5))).toEqual({ kind: "spz-ngsp", version: 5 });
        expect(identifySplatFile(spz4Head().subarray(0, 6))).toEqual({
            kind: "spz-ngsp",
            version: null,
        });
    });

    it("recognizes a PLY header and nothing else", () => {
        expect(identifySplatFile(new TextEncoder().encode("ply\nformat binary"))).toEqual({
            kind: "ply",
        });
        expect(identifySplatFile(new TextEncoder().encode('{"splat":1}'))).toEqual({
            kind: "unknown",
        });
        expect(identifySplatFile(new Uint8Array())).toEqual({ kind: "unknown" });
    });

    it("reads the version from a head that is a view into a larger buffer", () => {
        const backing = new Uint8Array(64);
        backing.set(spz4Head(), 8);
        expect(identifySplatFile(backing.subarray(8, 24))).toEqual({
            kind: "spz-ngsp",
            version: 4,
        });
    });
});

describe("unsupportedSplatMessage", () => {
    it("lets the files the loader reads through", () => {
        expect(unsupportedSplatMessage({ kind: "spz-gzip" }, "a.spz")).toBe(null);
        expect(unsupportedSplatMessage({ kind: "ply" }, "a.ply")).toBe(null);
    });

    it("names the file and its SPZ version when the loader cannot read it", () => {
        expect(unsupportedSplatMessage({ kind: "spz-ngsp", version: 4 }, "ball.spz")).toBe(
            "ball.spz uses SPZ format version 4. The BabylonJS Gaussian Splat viewer opens SPZ " +
                "versions 2 and 3 and PLY files."
        );
        expect(unsupportedSplatMessage({ kind: "spz-ngsp", version: null }, "b.spz")).toContain(
            "version 4 or later"
        );
        expect(unsupportedSplatMessage({ kind: "unknown" }, "c.spz")).toContain(
            "c.spz is not an SPZ or PLY Gaussian splat file"
        );
    });
});

describe("readFileHead", () => {
    const originalFetch = (globalThis as any).fetch;
    afterEach(() => {
        (globalThis as any).fetch = originalFetch;
    });

    /** A response whose body yields the given chunks and records whether it was cancelled. */
    const streamResponse = (chunks: Uint8Array[], ok = true) => {
        const cancel = jest.fn(() => Promise.resolve());
        let index = 0;
        return {
            cancel,
            response: {
                ok,
                body: {
                    getReader: () => ({
                        read: () =>
                            Promise.resolve(
                                index < chunks.length
                                    ? { done: false, value: chunks[index++] }
                                    : { done: true, value: undefined }
                            ),
                        cancel,
                    }),
                },
            },
        };
    };

    it("asks for the first bytes only and returns them", async () => {
        const { response, cancel } = streamResponse([spz4Head().subarray(0, 16)]);
        const fetchMock = jest.fn().mockResolvedValue(response);
        (globalThis as any).fetch = fetchMock;

        const head = await readFileHead("https://example.test/ball.spz?sig=1");

        expect(fetchMock.mock.calls[0][1].headers).toEqual({ Range: "bytes=0-15" });
        expect(Array.from(head?.subarray(0, 4) ?? [])).toEqual([0x4e, 0x47, 0x53, 0x50]);
        expect(head?.length).toBe(16);
        expect(cancel).toHaveBeenCalled();
    });

    it("stops after the first bytes when the server ignores the range", async () => {
        const body = new Uint8Array(4096).fill(7);
        const second = jest.fn();
        const { response, cancel } = streamResponse([body.subarray(0, 10), body.subarray(10)]);
        const reader = response.body.getReader();
        const read = reader.read;
        response.body.getReader = () => ({
            read: () => {
                second();
                return read();
            },
            cancel,
        });
        (globalThis as any).fetch = jest.fn().mockResolvedValue(response);

        const head = await readFileHead("https://example.test/big.ply");

        expect(head?.length).toBe(16);
        expect(second).toHaveBeenCalledTimes(2);
        expect(cancel).toHaveBeenCalled();
    });

    it("returns null when the bytes cannot be read, so the load goes ahead unchecked", async () => {
        (globalThis as any).fetch = jest.fn().mockResolvedValue(streamResponse([], false).response);
        expect(await readFileHead("https://example.test/a.spz")).toBe(null);
        (globalThis as any).fetch = jest.fn().mockRejectedValue(new TypeError("Failed to fetch"));
        expect(await readFileHead("https://example.test/a.spz")).toBe(null);
    });
});

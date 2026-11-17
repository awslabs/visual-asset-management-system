/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { RasterDecodeError } from "./rasterDecode";
import { buildTga } from "./rasterFixtures.testdata";
import { openRasterSession } from "./rasterSession";

const mockCreateWorker = jest.fn();
jest.mock("./createRasterWorker", () => ({ createRasterWorker: () => mockCreateWorker() }));

const limits = { maxBytes: 1 << 20, maxPixels: 10_000 };
const tga = () =>
    buildTga(2, 1, [
        [255, 0, 0],
        [0, 0, 255],
    ]).buffer.slice(0) as ArrayBuffer;

/** A worker whose behaviour the test scripts. */
function fakeWorker() {
    const worker: any = {
        terminate: jest.fn(),
        posted: [] as any[],
        postMessage: jest.fn((message: any) => worker.posted.push(message)),
    };
    return worker;
}

describe("openRasterSession", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "warn").mockImplementation(() => {});
    });
    afterEach(() => jest.restoreAllMocks());

    it("decodes on the page when the worker cannot be created", async () => {
        mockCreateWorker.mockReturnValue(null);
        const session = await openRasterSession(tga(), "tga", limits);
        expect(session.pages).toEqual([{ index: 0, width: 2, height: 1 }]);
        expect((await session.decode(0)).width).toBe(2);
    });

    it("decodes on the page, with the bytes intact, when the worker script fails to load", async () => {
        const worker = fakeWorker();
        mockCreateWorker.mockReturnValue(worker);
        const opened = openRasterSession(tga(), "tga", limits);
        // A blocked or missing script raises `error` before the worker said it was ready.
        setTimeout(() => worker.onerror({ preventDefault() {} }), 0);
        const session = await opened;
        expect(worker.postMessage).not.toHaveBeenCalled();
        expect(worker.terminate).toHaveBeenCalled();
        expect((await session.decode(0)).height).toBe(1);
    });

    it("decodes on the page when the worker never starts", async () => {
        jest.useFakeTimers();
        try {
            const worker = fakeWorker();
            mockCreateWorker.mockReturnValue(worker);
            const opened = openRasterSession(tga(), "tga", limits, 50);
            await jest.advanceTimersByTimeAsync(60);
            const session = await opened;
            expect(worker.postMessage).not.toHaveBeenCalled();
            expect((await session.decode(0)).width).toBe(2);
        } finally {
            jest.useRealTimers();
        }
    });

    it("keeps the same caps on the page path", async () => {
        mockCreateWorker.mockReturnValue(null);
        await expect(
            openRasterSession(tga(), "tga", { ...limits, maxPixels: 1 }, 1).then((s) => s.decode(0))
        ).rejects.toMatchObject({
            reason: "too-many-pixels",
        });
    });

    it("reports a worker that dies after it started as an out-of-memory style failure", async () => {
        const worker = fakeWorker();
        mockCreateWorker.mockReturnValue(worker);
        const opened = openRasterSession(tga(), "tga", limits);
        setTimeout(() => worker.onmessage({ data: { type: "ready" } }), 0);
        await new Promise((r) => setTimeout(r, 5));
        // The worker received the file, then died.
        expect(worker.postMessage).toHaveBeenCalledTimes(1);
        worker.onerror({ preventDefault() {} });
        await expect(opened).rejects.toMatchObject({
            reason: "unsupported",
            message: expect.stringMatching(/stopped unexpectedly/),
        });
        expect(worker.terminate).toHaveBeenCalled();
    });

    it("passes a decode failure reported by a started worker through unchanged", async () => {
        const worker = fakeWorker();
        mockCreateWorker.mockReturnValue(worker);
        const opened = openRasterSession(tga(), "tga", limits);
        setTimeout(() => worker.onmessage({ data: { type: "ready" } }), 0);
        await new Promise((r) => setTimeout(r, 5));
        worker.onmessage({
            data: { id: 1, ok: false, reason: "corrupt", message: "The TGA file is truncated." },
        });
        await expect(opened).rejects.toEqual(expect.any(RasterDecodeError));
    });

    it("rejects a page switch still in flight when the session is disposed", async () => {
        const worker = fakeWorker();
        mockCreateWorker.mockReturnValue(worker);
        const opened = openRasterSession(tga(), "tga", limits);
        setTimeout(() => worker.onmessage({ data: { type: "ready" } }), 0);
        await new Promise((r) => setTimeout(r, 5));
        worker.onmessage({ data: { id: 1, ok: true, pages: [{ index: 0, width: 2, height: 1 }] } });
        const session = await opened;
        const switching = session.decode(1);
        session.dispose();
        await expect(switching).rejects.toMatchObject({ message: "The image viewer was closed." });
        // Later calls reject cleanly too, and a second dispose does nothing.
        await expect(session.decode(0)).rejects.toBeInstanceOf(RasterDecodeError);
        session.dispose();
        expect(worker.terminate).toHaveBeenCalledTimes(1);
    });
});

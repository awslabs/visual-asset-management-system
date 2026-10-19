/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    CHUNK_RELOAD_GUARD_KEY,
    CHUNK_RELOAD_GUARD_WINDOW_MS,
    importWithRetry,
    isChunkLoadError,
    reloadOnceForChunkError,
    reloadPage,
} from "./chunkLoadRecovery";

/** In-memory stand-in for sessionStorage. */
function memoryStorage(initial: Record<string, string> = {}) {
    const data = { ...initial };
    return {
        data,
        getItem: jest.fn((k: string) => (k in data ? data[k] : null)),
        setItem: jest.fn((k: string, v: string) => {
            data[k] = v;
        }),
        removeItem: jest.fn((k: string) => {
            delete data[k];
        }),
    };
}

const chunkError = () =>
    new TypeError("Failed to fetch dynamically imported module: https://host/assets/App-abc.js");

describe("isChunkLoadError", () => {
    it.each([
        "Failed to fetch dynamically imported module: https://host/assets/App-abc.js",
        "error loading dynamically imported module: https://host/assets/App-abc.js",
        "Importing a module script failed.",
        'Failed to load module script: Expected a JavaScript module script but the server responded with a MIME type of "text/html".',
        "Unable to preload CSS for /assets/App-abc.css",
        "Loading chunk 42 failed.",
    ])("recognizes %s", (message) => {
        expect(isChunkLoadError(new Error(message))).toBe(true);
    });

    it("recognizes a ChunkLoadError by name", () => {
        const e = new Error("anything");
        e.name = "ChunkLoadError";
        expect(isChunkLoadError(e)).toBe(true);
    });

    it.each([
        new Error("Cannot read properties of undefined (reading 'map')"),
        new Error("Network request failed"),
        null,
        undefined,
        {},
    ])("does not treat %p as a chunk failure", (error) => {
        expect(isChunkLoadError(error)).toBe(false);
    });
});

describe("reloadOnceForChunkError", () => {
    it("reloads and records the time when no reload happened recently", () => {
        const storage = memoryStorage();
        const reload = jest.fn();
        expect(reloadOnceForChunkError({ storage, reload, now: () => 1_000_000 })).toBe(true);
        expect(reload).toHaveBeenCalledTimes(1);
        expect(storage.data[CHUNK_RELOAD_GUARD_KEY]).toBe("1000000");
    });

    it("does not reload again inside the guard window", () => {
        const storage = memoryStorage({ [CHUNK_RELOAD_GUARD_KEY]: "1000000" });
        const reload = jest.fn();
        const now = () => 1_000_000 + CHUNK_RELOAD_GUARD_WINDOW_MS - 1;
        expect(reloadOnceForChunkError({ storage, reload, now })).toBe(false);
        expect(reload).not.toHaveBeenCalled();
    });

    it("reloads again once the guard window has passed", () => {
        const storage = memoryStorage({ [CHUNK_RELOAD_GUARD_KEY]: "1000000" });
        const reload = jest.fn();
        const now = () => 1_000_000 + CHUNK_RELOAD_GUARD_WINDOW_MS;
        expect(reloadOnceForChunkError({ storage, reload, now })).toBe(true);
        expect(reload).toHaveBeenCalledTimes(1);
    });

    it("never reloads when the guard cannot be stored", () => {
        const reload = jest.fn();
        expect(reloadOnceForChunkError({ storage: null, reload })).toBe(false);
        const throwing = memoryStorage();
        throwing.setItem.mockImplementation(() => {
            throw new Error("QuotaExceededError");
        });
        expect(reloadOnceForChunkError({ storage: throwing, reload })).toBe(false);
        expect(reload).not.toHaveBeenCalled();
    });
});

describe("reloadPage", () => {
    it("clears the guard before reloading, so a manual reload re-arms the automatic one", () => {
        const storage = memoryStorage({ [CHUNK_RELOAD_GUARD_KEY]: "1000000" });
        const reload = jest.fn();
        reloadPage({ storage, reload });
        expect(storage.data[CHUNK_RELOAD_GUARD_KEY]).toBeUndefined();
        expect(reload).toHaveBeenCalledTimes(1);
    });
});

describe("importWithRetry", () => {
    const opts = (extra = {}) => ({
        retryDelayMs: 0,
        storage: memoryStorage(),
        reload: jest.fn(),
        ...extra,
    });

    it("returns the module on the first success without retrying", async () => {
        const factory = jest.fn().mockResolvedValue({ default: "App" });
        await expect(importWithRetry(factory, opts())).resolves.toEqual({ default: "App" });
        expect(factory).toHaveBeenCalledTimes(1);
    });

    it("retries a chunk failure once and returns the module", async () => {
        const factory = jest
            .fn()
            .mockRejectedValueOnce(chunkError())
            .mockResolvedValueOnce({ default: "App" });
        const o = opts();
        await expect(importWithRetry(factory, o)).resolves.toEqual({ default: "App" });
        expect(factory).toHaveBeenCalledTimes(2);
        expect(o.reload).not.toHaveBeenCalled();
    });

    it("does not retry an error that is not a chunk failure", async () => {
        const boom = new Error("module evaluation failed");
        const factory = jest.fn().mockRejectedValue(boom);
        const o = opts();
        await expect(importWithRetry(factory, o)).rejects.toBe(boom);
        expect(factory).toHaveBeenCalledTimes(1);
        expect(o.reload).not.toHaveBeenCalled();
    });

    it("reloads the page once the retry also fails, and keeps the promise pending", async () => {
        const factory = jest.fn().mockRejectedValue(chunkError());
        const o = opts();
        let settled = false;
        importWithRetry(factory, o).then(
            () => (settled = true),
            () => (settled = true)
        );
        await new Promise((r) => setTimeout(r, 20));
        expect(factory).toHaveBeenCalledTimes(2);
        expect(o.reload).toHaveBeenCalledTimes(1);
        expect(settled).toBe(false);
    });

    it("rethrows for the error boundary when a reload already happened recently", async () => {
        const factory = jest.fn().mockRejectedValue(chunkError());
        const o = opts({
            storage: memoryStorage({ [CHUNK_RELOAD_GUARD_KEY]: String(Date.now()) }),
        });
        await expect(importWithRetry(factory, o)).rejects.toThrow(/dynamically imported module/);
        expect(o.reload).not.toHaveBeenCalled();
    });
});

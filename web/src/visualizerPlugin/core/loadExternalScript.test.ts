/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const SRC = "/viewers/example/bundle.js";

// The in-flight map lives at module scope, so each test needs a fresh module instance.
const freshModule = () => {
    let mod: any;
    jest.isolateModules(() => {
        mod = require("./loadExternalScript");
    });
    return mod;
};

const tags = (src = SRC): HTMLScriptElement[] =>
    Array.from(document.querySelectorAll(`script[src="${src}"]`)) as HTMLScriptElement[];

/** jsdom never fetches or executes the script, so its load event is fired by hand. */
const fireLoad = (script: HTMLScriptElement) => script.onload!(new Event("load") as any);

describe("loadExternalScript", () => {
    beforeEach(() => {
        document.head.replaceChildren();
    });

    it("injects the script once and resolves when it executes", async () => {
        const { loadExternalScript } = freshModule();
        const pending = loadExternalScript(SRC);

        expect(tags()).toHaveLength(1);
        fireLoad(tags()[0]);
        await expect(pending).resolves.toBeUndefined();
    });

    // The load race: a second caller must not inject a competing tag, and above all must not remove
    // the first caller's in-flight tag — removing it fires no error event, so the first caller's
    // promise would never settle and its viewer would wait forever.
    it("shares one in-flight load between concurrent callers", async () => {
        const { loadExternalScript } = freshModule();

        const first = loadExternalScript(SRC);
        const injected = tags()[0];
        const second = loadExternalScript(SRC);
        const third = loadExternalScript(SRC);

        expect(tags()).toHaveLength(1);
        expect(tags()[0]).toBe(injected);
        expect(injected.isConnected).toBe(true);

        fireLoad(injected);
        await expect(Promise.all([first, second, third])).resolves.toEqual([
            undefined,
            undefined,
            undefined,
        ]);
    });

    it("does not re-inject a script that already executed", async () => {
        const { loadExternalScript } = freshModule();
        const first = loadExternalScript(SRC);
        fireLoad(tags()[0]);
        await first;

        await expect(loadExternalScript(SRC)).resolves.toBeUndefined();
        expect(tags()).toHaveLength(1);
    });

    // A tag with no completion marker and nothing in flight is the residue of an attempt that never
    // executed. Resolving on its mere presence is what handed callers an undefined global.
    it("discards an unexecuted leftover tag and injects a fresh one", async () => {
        const { loadExternalScript } = freshModule();
        const stale = document.createElement("script");
        stale.src = SRC;
        document.head.appendChild(stale);

        const pending = loadExternalScript(SRC);
        expect(tags()).toHaveLength(1);
        expect(tags()[0]).not.toBe(stale);

        fireLoad(tags()[0]);
        await expect(pending).resolves.toBeUndefined();
    });

    // A viewer that publishes a window global deletes it on unmount, leaving a tag that executed but
    // a library that is gone. Short-circuiting on the tag alone would resolve without re-executing.
    it("re-executes when the tag ran but the library is no longer ready", async () => {
        const { loadExternalScript } = freshModule();
        let ready = false;
        const isReady = () => ready;

        const first = loadExternalScript(SRC, { isReady });
        ready = true;
        fireLoad(tags()[0]);
        await first;

        // Still ready: no reload.
        await loadExternalScript(SRC, { isReady });
        expect(tags()).toHaveLength(1);

        // The global was torn down, so the bundle must run again.
        ready = false;
        const reload = loadExternalScript(SRC, { isReady });
        ready = true;
        fireLoad(tags()[0]);
        await expect(reload).resolves.toBeUndefined();
    });

    // isReady must not be consulted ahead of the in-flight check, or a second caller would decide a
    // reload was needed and tear out the first caller's still-downloading tag.
    it("joins an in-flight load even when the library is not ready yet", async () => {
        const { loadExternalScript } = freshModule();
        const isReady = () => false;

        const first = loadExternalScript(SRC, { isReady });
        const injected = tags()[0];
        const second = loadExternalScript(SRC, { isReady });

        expect(tags()).toHaveLength(1);
        expect(tags()[0]).toBe(injected);
        expect(injected.isConnected).toBe(true);

        fireLoad(injected);
        await expect(Promise.all([first, second])).resolves.toEqual([undefined, undefined]);
    });

    it("keeps separate srcs independent", async () => {
        const { loadExternalScript } = freshModule();
        const other = "/viewers/example/other.js";

        const a = loadExternalScript(SRC);
        const b = loadExternalScript(other);
        expect(tags()).toHaveLength(1);
        expect(tags(other)).toHaveLength(1);

        fireLoad(tags()[0]);
        fireLoad(tags(other)[0]);
        await expect(Promise.all([a, b])).resolves.toEqual([undefined, undefined]);
    });

    it("rejects on error and lets a later attempt retry", async () => {
        const { loadExternalScript } = freshModule();
        const failing = loadExternalScript(SRC);
        tags()[0].onerror!(new Event("error") as any);
        await expect(failing).rejects.toThrow(`Failed to load script: ${SRC}`);

        const retry = loadExternalScript(SRC);
        expect(tags()).toHaveLength(1);
        fireLoad(tags()[0]);
        await expect(retry).resolves.toBeUndefined();
    });

    it("times out instead of waiting forever", async () => {
        jest.useFakeTimers();
        try {
            const { loadExternalScript } = freshModule();
            const pending = loadExternalScript(SRC, { timeoutMs: 1000 });
            const assertion = expect(pending).rejects.toThrow(/Timed out after 1s/);

            jest.advanceTimersByTime(1000);
            await assertion;
            expect(tags()).toHaveLength(0);
        } finally {
            jest.useRealTimers();
        }
    });

    it("loads as a module when asked", async () => {
        const { loadExternalScript } = freshModule();
        const pending = loadExternalScript(SRC, { asModule: true });

        expect(tags()[0].type).toBe("module");
        fireLoad(tags()[0]);
        await pending;
    });

    it("re-injects after a reset, for a cleanup that drops the global", async () => {
        const { loadExternalScript, resetExternalScript } = freshModule();
        const first = loadExternalScript(SRC);
        fireLoad(tags()[0]);
        await first;

        resetExternalScript(SRC);

        const second = loadExternalScript(SRC);
        fireLoad(tags()[0]);
        await expect(second).resolves.toBeUndefined();
    });
});

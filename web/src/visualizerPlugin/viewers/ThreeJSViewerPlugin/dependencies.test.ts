/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const BUNDLE_SELECTOR = 'script[src="/viewers/threejs/threejs.min.js"]';

// The manager keeps its loaded/in-flight state in module-level statics, so each test needs a fresh
// module instance. No jest.mock factories are involved here, so resetModules is safe.
const freshManager = () => {
    let mod: any;
    jest.isolateModules(() => {
        mod = require("./dependencies");
    });
    return mod.ThreeJSDependencyManager;
};

const scripts = (): HTMLScriptElement[] =>
    Array.from(document.querySelectorAll(BUNDLE_SELECTOR)) as HTMLScriptElement[];

/** jsdom never executes the script, so the bundle's side effect is applied by hand. */
const completeLoad = (script: HTMLScriptElement, withBundle = true) => {
    if (withBundle) {
        (window as any).THREEBundle = { THREE: { revision: "test" } };
    }
    script.onload!(new Event("load") as any);
};

describe("ThreeJSDependencyManager.loadThreeJS", () => {
    beforeEach(() => {
        document.head.replaceChildren();
        delete (window as any).THREEBundle;
        delete (window as any).THREE;
    });

    it("injects a single script and publishes THREE on success", async () => {
        const Manager = freshManager();
        const pending = Manager.loadThreeJS();

        expect(scripts()).toHaveLength(1);
        completeLoad(scripts()[0]);
        await expect(pending).resolves.toBeUndefined();

        expect((window as any).THREE).toEqual({ revision: "test" });
        expect(Manager.isThreeJSLoaded()).toBe(true);
    });

    // The regression this file exists for: a second caller arriving mid-download used to find the
    // first caller's script tag, see no global yet, and remove it to "force a reload". That left the
    // first caller's promise unsettled forever and its viewer stuck on "Processing".
    it("shares one in-flight load between concurrent callers", async () => {
        const Manager = freshManager();

        const first = Manager.loadThreeJS();
        const injected = scripts()[0];
        const second = Manager.loadThreeJS();

        expect(scripts()).toHaveLength(1);
        expect(scripts()[0]).toBe(injected);
        expect(injected.isConnected).toBe(true);

        completeLoad(injected);
        await expect(Promise.all([first, second])).resolves.toEqual([undefined, undefined]);
    });

    it("reuses the loaded bundle without injecting again", async () => {
        const Manager = freshManager();
        const first = Manager.loadThreeJS();
        completeLoad(scripts()[0]);
        await first;

        // A remount clears the published global but not the bundle itself.
        delete (window as any).THREE;
        await Manager.loadThreeJS();

        expect(scripts()).toHaveLength(1);
        expect((window as any).THREE).toEqual({ revision: "test" });
    });

    // The viewer deletes both globals when it unmounts, so opening a second file has to reload the
    // bundle. A settled promise retained from the first load would resolve instantly without
    // re-injecting, and the caller would then fail on "ThreeJS dependencies not loaded".
    it("reloads after the viewer tears down the globals on unmount", async () => {
        const Manager = freshManager();
        const first = Manager.loadThreeJS();
        completeLoad(scripts()[0]);
        await first;

        // Exactly what ThreeJSViewerComponent's cleanup does.
        delete (window as any).THREE;
        delete (window as any).THREEBundle;
        expect(Manager.isThreeJSLoaded()).toBe(false);

        const second = Manager.loadThreeJS();
        expect(scripts()).toHaveLength(1);
        completeLoad(scripts()[0]);
        await expect(second).resolves.toBeUndefined();

        expect(Manager.isThreeJSLoaded()).toBe(true);
        expect((window as any).THREE).toEqual({ revision: "test" });
    });

    it("rejects when the script fails and lets a later attempt retry", async () => {
        const Manager = freshManager();
        const failing = Manager.loadThreeJS();
        scripts()[0].onerror!(new Event("error") as any);
        await expect(failing).rejects.toThrow(
            "Failed to load script: /viewers/threejs/threejs.min.js"
        );

        // The memo was cleared, so this is a real second attempt rather than the cached failure.
        const retry = Manager.loadThreeJS();
        expect(scripts()).toHaveLength(1);
        completeLoad(scripts()[0]);
        await expect(retry).resolves.toBeUndefined();
    });

    it("rejects when the script loads but defines no bundle", async () => {
        const Manager = freshManager();
        const pending = Manager.loadThreeJS();
        completeLoad(scripts()[0], false);
        await expect(pending).rejects.toThrow("not found in window object");
        expect(Manager.isThreeJSLoaded()).toBe(false);
    });

    it("times out instead of waiting forever when the bundle never executes", async () => {
        jest.useFakeTimers();
        try {
            const Manager = freshManager();
            const pending = Manager.loadThreeJS();
            const assertion = expect(pending).rejects.toThrow(/Timed out after 60s/);

            jest.advanceTimersByTime(60000);
            await assertion;
            expect(scripts()).toHaveLength(0);
        } finally {
            jest.useRealTimers();
        }
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import fs from "fs";
import path from "path";
import { waitFor } from "@testing-library/react";
import type * as Dependencies from "./dependencies";

// jsdom cannot evaluate the SDK, so the bundle published at MIRIS_SDK_URL is a stand-in that
// records when it is evaluated and when `defineElements()` registers the custom elements.
const mockEvents: string[] = [];
let mockComponentsFail = false;
// Each `defineElements()` takes the next gate, if any, and settles with it.
const mockComponentsGates: Array<Promise<void>> = [];
let mockComponentsImports = 0;

jest.mock(
    "/viewers/miris/miris-sdk.js",
    () => {
        mockEvents.push("sdk");
        return {
            defineElements: () => {
                mockComponentsImports += 1;
                if (mockComponentsFail) return Promise.reject(new Error("chunk failed"));
                mockEvents.push("components");
                return mockComponentsGates.shift() ?? Promise.resolve();
            },
        };
    },
    { virtual: true }
);

// The manager keeps its load state in statics, so each test takes fresh module instances.
const freshModule = () => {
    jest.resetModules();
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    return require("./dependencies") as typeof Dependencies;
};

const setGpu = (gpu: unknown) => {
    if (gpu === undefined) delete (navigator as Navigator & { gpu?: unknown }).gpu;
    else Object.defineProperty(navigator, "gpu", { value: gpu, configurable: true });
};

beforeEach(() => {
    mockEvents.length = 0;
    mockComponentsFail = false;
});

afterAll(() => setGpu(undefined));

describe("MirisStreamDependencyManager.isDeviceSupported", () => {
    it.each([
        ["supported when an adapter is granted", { requestAdapter: async () => ({}) }, true],
        // Blocklisted GPU, no hardware acceleration, or a virtual desktop.
        ["unsupported when no adapter is granted", { requestAdapter: async () => null }, false],
        [
            "unsupported when the adapter request throws",
            { requestAdapter: () => Promise.reject(new Error("gpu process crashed")) },
            false,
        ],
        ["unsupported without WebGPU", undefined, false],
    ])("reports the device %s, without loading the SDK", async (_label, gpu, supported) => {
        setGpu(gpu);
        const { MirisStreamDependencyManager } = freshModule();
        await expect(MirisStreamDependencyManager.isDeviceSupported()).resolves.toBe(supported);
        expect(mockEvents).toEqual([]);
    });
});

describe("MirisStreamDependencyManager.loadMirisStream", () => {
    it("shares one load between concurrent callers and is a no-op once loaded", async () => {
        const { MirisStreamDependencyManager } = freshModule();
        await Promise.all([
            MirisStreamDependencyManager.loadMirisStream(),
            MirisStreamDependencyManager.loadMirisStream(),
        ]);
        await MirisStreamDependencyManager.loadMirisStream();
        expect(mockEvents).toEqual(["sdk", "components"]);
    });

    it("starts a new attempt after the pending load is abandoned, and a stale attempt does not clear it", async () => {
        const { MirisStreamDependencyManager } = freshModule();
        let failA: (reason: Error) => void = () => undefined;
        let openB: () => void = () => undefined;
        mockComponentsGates.push(
            new Promise<void>((_resolve, reject) => (failA = reject)),
            new Promise<void>((resolve) => (openB = resolve))
        );
        mockComponentsImports = 0;

        const first = MirisStreamDependencyManager.loadMirisStream();
        MirisStreamDependencyManager.abandonPendingLoad();
        const second = MirisStreamDependencyManager.loadMirisStream();
        await waitFor(() => expect(mockComponentsImports).toBe(2));

        // The abandoned attempt fails while the second is still pending.
        failA(new Error("stale attempt failed"));
        await expect(first).rejects.toThrow("stale attempt failed");
        const importsBefore = mockComponentsImports;
        const third = MirisStreamDependencyManager.loadMirisStream();
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(mockComponentsImports).toBe(importsBefore);

        openB();
        await expect(Promise.all([second, third])).resolves.toBeDefined();
    });

    it("rejects when the elements fail to load and retries on the next call", async () => {
        const { MirisStreamDependencyManager } = freshModule();
        mockComponentsFail = true;
        await expect(MirisStreamDependencyManager.loadMirisStream()).rejects.toThrow(
            "chunk failed"
        );

        mockComponentsFail = false;
        jest.resetModules();
        await expect(MirisStreamDependencyManager.loadMirisStream()).resolves.toBeUndefined();
        expect(mockEvents).toContain("components");
    });
});

describe("MirisStreamDependencyManager.cleanup", () => {
    it("lets the next open load again", async () => {
        const { MirisStreamDependencyManager } = freshModule();
        await MirisStreamDependencyManager.loadMirisStream();
        MirisStreamDependencyManager.cleanup();
        await MirisStreamDependencyManager.loadMirisStream();
        expect(mockEvents).toEqual(["sdk", "components", "components"]);
    });
});

describe("MIRIS_SDK_URL", () => {
    it("is where the custom install publishes the bundle's entry", () => {
        // public/ is served from the site root.
        const installDir = path.join(__dirname, "../../../../customInstalls/miris");
        const install = fs.readFileSync(path.join(installDir, "mirisInstall.js"), "utf8");
        const webpackConfig = jest.requireActual(path.join(installDir, "webpack.config.js"));
        const destination = /const destinationDir = "\.\/public(\/[^"]+)";/.exec(install)?.[1];
        const entry = /const bundleEntryFile = "([^"]+)";/.exec(install)?.[1];

        const { MIRIS_SDK_URL } = freshModule();
        expect(`${destination}/${entry}`).toBe(MIRIS_SDK_URL);
        expect(webpackConfig.output.filename).toBe(entry);
    });
});

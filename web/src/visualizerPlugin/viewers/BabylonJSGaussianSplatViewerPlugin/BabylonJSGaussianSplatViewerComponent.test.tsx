/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The viewer creates a WebGL context and starts a render loop. The render-loop
 * closure keeps the Engine reachable, so unless unmount stops the loop and
 * disposes the engine, every open leaks a context that renders off-screen — and a
 * page has a hard cap on live contexts, after which the next viewer open fails.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import BabylonJSGaussianSplatViewerComponent from "./BabylonJSGaussianSplatViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const mockLoadBabylonJS = jest.fn();
jest.mock("./dependencies", () => ({
    BabylonJSGaussianSplatDependencyManager: {
        loadBabylonJS: () => mockLoadBabylonJS(),
    },
}));

interface FakeEngine {
    runRenderLoop: jest.Mock;
    stopRenderLoop: jest.Mock;
    dispose: jest.Mock;
    resize: jest.Mock;
    getRenderingCanvas: jest.Mock;
}

/** Records `WEBGL_lose_context.loseContext()` so the context release can be asserted. */
const loseContextCalls: jest.Mock[] = [];

function makeFakeGlCanvas(): HTMLCanvasElement {
    const lose = jest.fn();
    loseContextCalls.push(lose);
    const gl = {
        getExtension: (name: string) =>
            name === "WEBGL_lose_context" ? { loseContext: lose } : null,
    };
    return {
        getContext: (kind: string) => (kind === "webgl2" || kind === "webgl" ? gl : null),
        remove: jest.fn(),
    } as unknown as HTMLCanvasElement;
}

const engines: FakeEngine[] = [];
const scenes: Array<{ dispose: jest.Mock }> = [];
const observers: Array<{ observe: jest.Mock; disconnect: jest.Mock }> = [];

/**
 * Enough of BABYLON for the initialization path: a context-holding Engine, a
 * Scene, an orbit camera, and a splat import that never settles (so the component
 * stays in its loading state and the control panel is never mounted).
 */
const makeBabylon = () => ({
    Engine: jest.fn().mockImplementation(function FakeEngineCtor(this: any) {
        const engine: FakeEngine = {
            runRenderLoop: jest.fn(),
            stopRenderLoop: jest.fn(),
            dispose: jest.fn(),
            resize: jest.fn(),
            getRenderingCanvas: jest.fn(() => makeFakeGlCanvas()),
        };
        engines.push(engine);
        return engine;
    }),
    Scene: jest.fn().mockImplementation(function FakeSceneCtor(this: any) {
        const scene = {
            dispose: jest.fn(),
            render: jest.fn(),
            clearColor: null,
            createDefaultCameraOrLight: jest.fn(),
            activeCamera: null,
            getEngine: () => engines[engines.length - 1],
        };
        scenes.push(scene);
        return scene;
    }),
    Color4: jest.fn(),
    Vector3: { Zero: () => ({ x: 0, y: 0, z: 0 }) },
    ArcRotateCamera: jest.fn().mockImplementation(() => ({ attachControl: jest.fn() })),
    SceneLoader: { ImportMeshAsync: jest.fn(() => new Promise(() => {})) },
});

const props = {
    assetId: "a1",
    databaseId: "d1",
    assetKey: "splats/scene.spz",
} as any;

describe("BabylonJSGaussianSplatViewerComponent", () => {
    let originalResizeObserver: any;

    beforeEach(() => {
        jest.clearAllMocks();
        engines.length = 0;
        scenes.length = 0;
        observers.length = 0;
        mockLoadBabylonJS.mockResolvedValue(makeBabylon());
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/scene.spz?sig=1"]);
        originalResizeObserver = (globalThis as any).ResizeObserver;
        (globalThis as any).ResizeObserver = class {
            observe = jest.fn();
            unobserve = jest.fn();
            disconnect = jest.fn();
            constructor() {
                observers.push(this as any);
            }
        };
    });

    afterEach(() => {
        (globalThis as any).ResizeObserver = originalResizeObserver;
    });

    it("hands the WebGL context back on unmount, not just the engine", async () => {
        // Disposing the engine is NOT sufficient. Measured live against a real 18 MB .spz, repeatedly
        // opening it reached the browser's live-context ceiling on the eighth mount ("Too many active
        // WebGL contexts. Oldest context will be lost.") while the JS heap stayed flat — so the
        // JavaScript was being collected and only the GPU context was not. The context has to be lost
        // explicitly. This is the half a mocked engine could not previously observe: the old suite
        // asserted `engine.dispose()` was called, which it always was.
        const { unmount } = render(<BabylonJSGaussianSplatViewerComponent {...props} />);
        await waitFor(() => expect(engines).toHaveLength(1));
        // Positive control: the context is NOT released while the viewer is mounted, so the assertion
        // after unmount distinguishes the two states rather than matching either.
        expect(loseContextCalls.some((lose) => lose.mock.calls.length > 0)).toBe(false);

        unmount();

        // If this fails, the context is returned only when the browser happens to collect it, which is
        // what let contexts accumulate across mounts. jest's `expect` takes no message argument (that is
        // Playwright's API), hence the comment rather than a third parameter.
        expect(loseContextCalls.some((lose) => lose.mock.calls.length > 0)).toBe(true);
    });

    it("stops the render loop and disposes the engine and scene on unmount", async () => {
        const { unmount } = render(<BabylonJSGaussianSplatViewerComponent {...props} />);

        await waitFor(() => expect(engines).toHaveLength(1));
        const [engine] = engines;
        const [scene] = scenes;
        expect(engine.runRenderLoop).toHaveBeenCalled();
        // Positive control: nothing is disposed while the viewer is mounted, so
        // the assertions below distinguish the two states.
        expect(engine.stopRenderLoop).not.toHaveBeenCalled();
        expect(engine.dispose).not.toHaveBeenCalled();
        expect(scene.dispose).not.toHaveBeenCalled();

        unmount();

        expect(engine.stopRenderLoop).toHaveBeenCalled();
        expect(scene.dispose).toHaveBeenCalled();
        expect(engine.dispose).toHaveBeenCalled();
        expect(observers[0].disconnect).toHaveBeenCalled();
    });

    it("re-initializes for the next file and leaves one canvas behind", async () => {
        // Browsing files inside one asset version reuses this component instance,
        // so the effect re-runs rather than the component remounting.
        const { container, rerender } = render(
            <BabylonJSGaussianSplatViewerComponent {...props} />
        );
        await waitFor(() => expect(engines).toHaveLength(1));

        rerender(<BabylonJSGaussianSplatViewerComponent {...props} assetKey="splats/other.spz" />);

        // A second engine at all proves the init guard was released; exactly one
        // canvas proves the first one did not stay in the container.
        await waitFor(() => expect(engines).toHaveLength(2));
        expect(engines[0].dispose).toHaveBeenCalled();
        expect(container.querySelectorAll("canvas")).toHaveLength(1);
    });

    it("names the rendering surface for assistive technology", async () => {
        const { container } = render(<BabylonJSGaussianSplatViewerComponent {...props} />);

        await waitFor(() => expect(container.querySelector("canvas")).not.toBeNull());
        const canvas = container.querySelector("canvas");
        expect(canvas?.getAttribute("role")).toBe("img");
        expect(canvas?.getAttribute("aria-label")).toContain("scene.spz");
    });
});

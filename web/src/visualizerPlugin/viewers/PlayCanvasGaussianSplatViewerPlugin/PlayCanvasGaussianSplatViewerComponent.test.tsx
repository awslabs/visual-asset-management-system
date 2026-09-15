/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A PlayCanvas Application owns a WebGL context and an update loop that keeps
 * running against a detached canvas unless it is destroyed. Removing the camera
 * controls' event listeners — all the unmount used to do — releases neither.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import PlayCanvasGaussianSplatViewerComponent from "./PlayCanvasGaussianSplatViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const mockLoadPlayCanvas = jest.fn();
jest.mock("./dependencies", () => ({
    PlayCanvasGaussianSplatDependencyManager: {
        loadPlayCanvas: () => mockLoadPlayCanvas(),
    },
}));

const apps: Array<{ start: jest.Mock; destroy: jest.Mock }> = [];
const observers: Array<{ disconnect: jest.Mock }> = [];

/** Enough of the PlayCanvas engine for the initialization path. */
const makePlayCanvas = () => {
    const noop = jest.fn();
    class Vec3 {
        x: number;
        y: number;
        z: number;
        constructor(x = 0, y = 0, z = 0) {
            this.x = x;
            this.y = y;
            this.z = z;
        }
        set(x: number, y: number, z: number) {
            this.x = x;
            this.y = y;
            this.z = z;
            return this;
        }
        add() {
            return this;
        }
        mulScalar() {
            return this;
        }
    }
    return {
        Application: jest.fn().mockImplementation((canvas: HTMLCanvasElement) => {
            const app = {
                scene: { clusteredLightingEnabled: true },
                autoRender: false,
                graphicsDevice: { canvas, maxPixelRatio: 1, setResolution: jest.fn() },
                setCanvasFillMode: jest.fn(),
                setCanvasResolution: jest.fn(),
                root: { addChild: jest.fn() },
                assets: { add: jest.fn(), load: jest.fn() },
                once: jest.fn(),
                start: jest.fn(),
                destroy: jest.fn(),
            };
            apps.push(app);
            return app;
        }),
        Mouse: jest.fn(),
        TouchDevice: jest.fn(),
        Keyboard: jest.fn(),
        Color: jest.fn(),
        Vec3,
        Entity: jest.fn().mockImplementation(() => ({
            addComponent: jest.fn(),
            setPosition: jest.fn(),
            setEulerAngles: jest.fn(),
            lookAt: jest.fn(),
            getWorldTransform: () => ({ getX: noop, getY: noop }),
            camera: { farClip: 0, nearClip: 0 },
        })),
        // ready() never fires, so the splat never finishes loading and the control
        // panel is never mounted — this test is about the engine's lifecycle.
        Asset: jest.fn().mockImplementation(() => ({ ready: jest.fn(), on: jest.fn() })),
        FILLMODE_FILL_WINDOW: "FILL_WINDOW",
        RESOLUTION_AUTO: "AUTO",
    };
};

const props = {
    assetId: "a1",
    databaseId: "d1",
    assetKey: "splats/scene.ply",
} as any;

describe("PlayCanvasGaussianSplatViewerComponent", () => {
    let originalResizeObserver: any;

    beforeEach(() => {
        jest.clearAllMocks();
        apps.length = 0;
        observers.length = 0;
        mockLoadPlayCanvas.mockResolvedValue(makePlayCanvas());
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/scene.ply?sig=1"]);
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

    it("destroys the application on unmount", async () => {
        const { unmount } = render(<PlayCanvasGaussianSplatViewerComponent {...props} />);

        await waitFor(() => expect(apps).toHaveLength(1));
        const [app] = apps;
        expect(app.start).toHaveBeenCalled();
        // Positive control: still alive while mounted.
        expect(app.destroy).not.toHaveBeenCalled();

        unmount();

        expect(app.destroy).toHaveBeenCalled();
        expect(observers[0].disconnect).toHaveBeenCalled();
    });

    it("re-initializes for the next file and leaves one canvas behind", async () => {
        const { container, rerender } = render(
            <PlayCanvasGaussianSplatViewerComponent {...props} />
        );
        await waitFor(() => expect(apps).toHaveLength(1));

        rerender(<PlayCanvasGaussianSplatViewerComponent {...props} assetKey="splats/other.ply" />);

        await waitFor(() => expect(apps).toHaveLength(2));
        expect(apps[0].destroy).toHaveBeenCalled();
        expect(container.querySelectorAll("canvas")).toHaveLength(1);
    });

    it("names the rendering surface for assistive technology", async () => {
        const { container } = render(<PlayCanvasGaussianSplatViewerComponent {...props} />);

        await waitFor(() => expect(container.querySelector("canvas")).not.toBeNull());
        const canvas = container.querySelector("canvas");
        expect(canvas?.getAttribute("role")).toBe("img");
        expect(canvas?.getAttribute("aria-label")).toContain("scene.ply");
    });
});

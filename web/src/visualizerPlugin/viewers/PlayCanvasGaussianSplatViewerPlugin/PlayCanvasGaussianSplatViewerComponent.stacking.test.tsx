/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the PlayCanvas Gaussian Splat viewer draws over its scene is stacked by what it is: the panel
 * the user works in, and the button that shows it again, above the info box with the controls hint
 * and the file name. While the open panel overlaps the info box, the info box is not drawn, so its
 * text does not show through the panel. The viewer runs over a stand-in PlayCanvas engine whose
 * splat asset is ready at once, with its real panel and stand-ins for the panel's tabs.
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import PlayCanvasGaussianSplatViewerComponent from "./PlayCanvasGaussianSplatViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));
const mockLoadPlayCanvas = jest.fn();
jest.mock("./dependencies", () => ({
    PlayCanvasGaussianSplatDependencyManager: { loadPlayCanvas: () => mockLoadPlayCanvas() },
}));
jest.mock("./components/Controls", () => () => "Controls tab");
jest.mock("./components/SplatInfo", () => () => "Splat info tab");
jest.mock("./components/RenderSettings", () => () => "Render settings tab");

const HINT = "Mouse: Rotate | Wheel: Zoom | Right-click: Pan";

const makePlayCanvas = () => {
    class Vec3 {
        x = 0;
        y = 0;
        z = 0;
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
        Application: jest.fn().mockImplementation((canvas: HTMLCanvasElement) => ({
            scene: { clusteredLightingEnabled: true },
            autoRender: false,
            graphicsDevice: { canvas, maxPixelRatio: 1, setResolution: jest.fn() },
            setCanvasFillMode: jest.fn(),
            setCanvasResolution: jest.fn(),
            root: { addChild: jest.fn() },
            assets: { add: jest.fn(), load: jest.fn() },
            // The first frame after the splat is added, when the viewer frames the camera
            once: jest.fn((_event: string, callback: () => void) => callback()),
            start: jest.fn(),
            destroy: jest.fn(),
        })),
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
            getWorldTransform: () => ({ getX: jest.fn(), getY: jest.fn() }),
            camera: { farClip: 0, nearClip: 0 },
        })),
        // A splat asset that is ready as soon as the viewer waits for it
        Asset: jest.fn().mockImplementation(() => ({
            ready: (callback: () => void) => callback(),
            on: jest.fn(),
        })),
        FILLMODE_FILL_WINDOW: "FILL_WINDOW",
        RESOLUTION_AUTO: "AUTO",
    };
};

// The nearest element at or above `el` with the given CSS position
const closestPositioned = (el: HTMLElement, position: "fixed" | "absolute"): HTMLElement => {
    let node: HTMLElement | null = el;
    while (node && node.style.position !== position) node = node.parentElement;
    if (!node) throw new Error(`No ${position} element at or above ${el.tagName}`);
    return node;
};

// Whether `upper` is drawn above `lower`, two positioned siblings with a z-index each: the higher
// z-index is drawn above, and at an equal z-index the element later in the document is
const isDrawnAbove = (upper: HTMLElement, lower: HTMLElement): boolean => {
    expect(upper.parentElement).toBe(lower.parentElement);
    expect(upper.style.zIndex).not.toBe("");
    expect(lower.style.zIndex).not.toBe("");
    const zUpper = Number(upper.style.zIndex);
    const zLower = Number(lower.style.zIndex);
    if (zUpper !== zLower) return zUpper > zLower;
    return !!(lower.compareDocumentPosition(upper) & Node.DOCUMENT_POSITION_FOLLOWING);
};

const getPanel = () => closestPositioned(screen.getByTitle("Hide panel (Esc)"), "absolute");
const getInfoBox = () => closestPositioned(screen.getByText(HINT), "absolute");

type Box = { left: number; top: number; right: number; bottom: number };

// A 414 px wide viewer: the panel down its left side, the info box at its top right
const PANEL_BOX: Box = { left: 10, top: 20, right: 330, bottom: 876 };
const INFO_OVER_PANEL: Box = { left: 164, top: 10, right: 404, bottom: 144 };
const INFO_BESIDE_PANEL: Box = { left: 1350, top: 10, right: 1590, bottom: 144 };

// Gives the panel and the info box the boxes in `layout` on screen; jsdom has no layout
const layOut = (layout: { info: Box }) =>
    jest
        .spyOn(Element.prototype, "getBoundingClientRect")
        .mockImplementation(function (this: Element) {
            const el = this as HTMLElement;
            const positioned = el.style?.position === "absolute";
            const isPanel = positioned && !!el.querySelector('[title="Hide panel (Esc)"]');
            const isInfoBox = positioned && !!el.textContent?.includes(HINT);
            const { left, top, right, bottom } = isPanel
                ? PANEL_BOX
                : isInfoBox
                ? layout.info
                : { left: 0, top: 0, right: 0, bottom: 0 };
            return { left, top, right, bottom, x: left, y: top, width: right - left } as DOMRect;
        });

const props = { assetId: "asset-1", databaseId: "db-1", assetKey: "splats/scene.ply" } as any;

const renderViewer = async () => {
    render(<PlayCanvasGaussianSplatViewerComponent {...props} />);
    await screen.findByTitle("Hide panel (Esc)");
};

describe("PlayCanvasGaussianSplatViewerComponent stacking", () => {
    beforeEach(() => {
        mockLoadPlayCanvas.mockResolvedValue(makePlayCanvas());
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/scene.ply?sig=1"]);
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("draws the panel, with its tabs, above the info box", async () => {
        await renderViewer();

        const panel = getPanel();
        expect(panel).toContainElement(screen.getByTitle("Controls"));
        expect(panel).toContainElement(screen.getByTitle("Splat Info"));
        expect(isDrawnAbove(panel, getInfoBox())).toBe(true);
    });

    it("draws the button that shows the panel again above the info box", async () => {
        await renderViewer();
        fireEvent.click(screen.getByTitle("Hide panel (Esc)"));

        expect(isDrawnAbove(screen.getByTitle("Show controls panel"), getInfoBox())).toBe(true);
    });

    it("does not draw the info box while the open panel overlaps it", async () => {
        layOut({ info: INFO_OVER_PANEL });
        await renderViewer();

        expect(getInfoBox()).not.toBeVisible();
        expect(getPanel()).toBeVisible();
    });

    it("draws the info box when there is room beside the panel, and while the panel is hidden", async () => {
        const layout = { info: INFO_BESIDE_PANEL };
        layOut(layout);
        await renderViewer();
        expect(getInfoBox()).toBeVisible();

        layout.info = INFO_OVER_PANEL;
        fireEvent(window, new Event("resize"));
        expect(getInfoBox()).not.toBeVisible();

        fireEvent.click(screen.getByTitle("Hide panel (Esc)"));
        expect(getInfoBox()).toBeVisible();
    });
});

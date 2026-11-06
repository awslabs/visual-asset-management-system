/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the BabylonJS Gaussian Splat viewer draws over its scene is stacked by what it is: the panel
 * the user works in, and the button that shows it again, above the info box with the controls hint
 * and the file name. While the open panel overlaps the info box, the info box is not drawn, so its
 * text does not show through the panel. The viewer runs over a stand-in BABYLON whose splat load
 * finishes at once, with its real panel and stand-ins for the panel's tabs.
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import BabylonJSGaussianSplatViewerComponent from "./BabylonJSGaussianSplatViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));
const mockLoadBabylonJS = jest.fn();
jest.mock("./dependencies", () => ({
    BabylonJSGaussianSplatDependencyManager: { loadBabylonJS: () => mockLoadBabylonJS() },
}));
jest.mock("./components/Controls", () => () => "Controls tab");
jest.mock("./components/SplatInfo", () => () => "Splat info tab");
jest.mock("./components/RenderSettings", () => () => "Render settings tab");

const HINT = "Mouse: Rotate | Wheel: Zoom | Right-click: Pan";

const makeBabylon = () => ({
    Engine: jest.fn().mockImplementation(() => ({
        runRenderLoop: jest.fn(),
        stopRenderLoop: jest.fn(),
        dispose: jest.fn(),
        resize: jest.fn(),
        // A canvas without a WebGL context, which the viewer asks for when it closes
        getRenderingCanvas: jest.fn(() => ({ getContext: () => null })),
    })),
    Scene: jest.fn().mockImplementation(() => ({
        dispose: jest.fn(),
        render: jest.fn(),
        clearColor: null,
    })),
    Color4: jest.fn(),
    Vector3: { Zero: () => ({ x: 0, y: 0, z: 0 }) },
    ArcRotateCamera: jest.fn().mockImplementation(() => ({ attachControl: jest.fn() })),
    // A load that finishes with no meshes, after which the viewer shows its panel and info box
    SceneLoader: { ImportMeshAsync: jest.fn(() => Promise.resolve({ meshes: [] })) },
});

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

const props = { assetId: "asset-1", databaseId: "db-1", assetKey: "splats/scene.spz" } as any;

const renderViewer = async () => {
    render(<BabylonJSGaussianSplatViewerComponent {...props} />);
    await screen.findByTitle("Hide panel (Esc)");
};

describe("BabylonJSGaussianSplatViewerComponent stacking", () => {
    const originalFetch = (globalThis as any).fetch;

    beforeEach(() => {
        mockLoadBabylonJS.mockResolvedValue(makeBabylon());
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/scene.spz?sig=1"]);
        // The file's first bytes cannot be read, so the viewer hands the file to the loader
        (globalThis as any).fetch = jest.fn().mockResolvedValue({ ok: false, status: 403 });
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => {
        (globalThis as any).fetch = originalFetch;
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

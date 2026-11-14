/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the ThatOpen IFC BIM viewer draws over its scene is stacked by what it is: the panel the user
 * works in, and the button that shows it again, above the info box with the controls hint, schema
 * and element count. While the open panel overlaps the info box, the info box is not drawn, so its
 * text does not show through the panel. The viewer runs over a stand-in That Open bundle, as in
 * ThatOpenWebIfcViewerComponent.test.tsx, with its real panel and stand-ins for the panel's tabs.
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import ThatOpenWebIfcViewerComponent from "./ThatOpenWebIfcViewerComponent";

const token = (name: string) => ({ token: name });
const OBC_TOKENS = {
    Worlds: token("Worlds"),
    Grids: token("Grids"),
    FragmentsManager: token("FragmentsManager"),
    Raycasters: token("Raycasters"),
};
const OBF_HIGHLIGHTER = token("Highlighter");

const makeBundle = () => {
    const fragments = {
        init: jest.fn(),
        core: { update: jest.fn().mockResolvedValue(undefined) },
        list: { onItemSet: { add: jest.fn() }, get: jest.fn() },
    };
    const services = new Map<any, any>([
        [OBC_TOKENS.Worlds, { create: () => ({}) }],
        [OBC_TOKENS.Grids, { create: jest.fn() }],
        [OBC_TOKENS.FragmentsManager, fragments],
        [OBC_TOKENS.Raycasters, { get: jest.fn() }],
        [
            OBF_HIGHLIGHTER,
            {
                setup: jest.fn(),
                events: {
                    select: { onHighlight: { add: jest.fn() }, onClear: { add: jest.fn() } },
                },
            },
        ],
    ]);
    const components = {
        get: (key: any) => services.get(key),
        init: jest.fn(),
        dispose: jest.fn(),
    };
    return {
        THREE: { Color: jest.fn() },
        OBC: {
            ...OBC_TOKENS,
            Components: jest.fn().mockImplementation(() => components),
            SimpleScene: jest
                .fn()
                .mockImplementation(() => ({ setup: jest.fn(), three: { add: jest.fn() } })),
            SimpleCamera: jest.fn().mockImplementation(() => ({
                controls: { addEventListener: jest.fn() },
                three: {},
            })),
        },
        OBF: {
            Highlighter: OBF_HIGHLIGHTER,
            RendererWith2D: jest.fn().mockImplementation(() => ({ resize: jest.fn() })),
        },
        unzipSync: jest.fn(),
    };
};

let bundle = makeBundle();
jest.mock("./dependencies", () => ({
    ThatOpenWebIfcDependencyManager: {
        loadThatOpenWebIfc: () => Promise.resolve(),
        // eslint-disable-next-line @typescript-eslint/no-use-before-define
        getBundle: () => currentBundle(),
        cleanup: jest.fn(),
    },
}));
const currentBundle = () => bundle;

jest.mock("./utils/ifcLoader", () => ({
    assertIfcSizeWithinLimit: jest.fn(),
    extractIfcBytes: jest.fn(() => new Uint8Array([1, 2, 3])),
    loadIfcModel: jest.fn(() => Promise.resolve({ modelId: "m1", schema: "IFC4" })),
    fitCameraToModels: jest.fn().mockResolvedValue(undefined),
}));
jest.mock("./utils/spatialTree", () => ({
    buildSpatialTree: jest.fn().mockResolvedValue({
        localId: null,
        name: "Categories",
        visible: true,
        children: [],
    }),
}));
jest.mock("./components/ModelTree", () => () => "Model tree tab");
jest.mock("./components/Properties", () => () => "Properties tab");
jest.mock("./components/Tools", () => () => "Tools tab");
jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: () => ({ api: "https://api.test/" }) },
}));
jest.mock("../../../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: () => Promise.resolve("Bearer test"),
}));

const HINT = "Mouse: Rotate | Wheel: Zoom | Right-click: Pan";

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

const getPanel = () => closestPositioned(screen.getByTitle("Hide panel (Esc)"), "fixed");
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
            const isPanel =
                el.style?.position === "fixed" && !!el.querySelector('[title="Hide panel (Esc)"]');
            const isInfoBox = el.style?.position === "absolute" && !!el.textContent?.includes(HINT);
            const { left, top, right, bottom } = isPanel
                ? PANEL_BOX
                : isInfoBox
                ? layout.info
                : { left: 0, top: 0, right: 0, bottom: 0 };
            return { left, top, right, bottom, x: left, y: top, width: right - left } as DOMRect;
        });

const props = { assetId: "asset-1", databaseId: "db-1", assetKey: "models/office.ifc" } as any;

const renderViewer = async () => {
    render(<ThatOpenWebIfcViewerComponent {...props} />);
    await screen.findByTitle("Hide panel (Esc)");
};

describe("ThatOpenWebIfcViewerComponent stacking", () => {
    const originalFetch = (globalThis as any).fetch;

    beforeEach(() => {
        bundle = makeBundle();
        (globalThis as any).fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => "2048" },
            arrayBuffer: async () => new ArrayBuffer(8),
        });
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "warn").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => {
        (globalThis as any).fetch = originalFetch;
        jest.restoreAllMocks();
    });

    it("draws the panel, with its tabs, above the info box", async () => {
        await renderViewer();

        const panel = getPanel();
        expect(panel).toContainElement(screen.getByRole("tab", { name: /Tools/ }));
        expect(panel).toContainElement(screen.getByRole("tab", { name: /Model/ }));
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

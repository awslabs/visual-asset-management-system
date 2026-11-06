/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the Needle USD viewer draws over its scene is stacked by what it is: the panel the user works
 * in (and the button that shows it again) above the info box with the controls hint and scene stats,
 * a notice about files that failed to load above the panel, and the loading cover above all of them.
 * On a narrow window the info box overlaps the panel, and at the same z-index the info box, later in
 * the document, took the clicks on the panel's tabs and its Clear Selection button (#1094). While
 * the open panel overlaps the info box, the info box is not drawn, so its text does not show
 * through the panel.
 *
 * The viewer runs over a stand-in USD module and three.js: each file is stored and drawn without
 * errors, so the viewer finds no missing dependencies and shows the scene after its two waits for
 * the module to settle (1.5 s each, run on fake timers here).
 */

import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import NeedleUSDViewerComponent from "./NeedleUSDViewerComponent";

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ api: "https://api.example.test/" })) },
}));
jest.mock("../../../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(() => Promise.resolve("Bearer test")),
}));
jest.mock("../../../services/APIService", () => ({ downloadAsset: jest.fn() }));
jest.mock("./MouseControls", () => ({
    MouseControls: jest.fn().mockImplementation(() => ({
        hasMoved: false,
        setTarget: jest.fn(),
        dispose: jest.fn(),
    })),
}));
jest.mock("./NeedleUSDMaterialLibrary", () => () => "Material library tab");
jest.mock("./NeedleUSDControls", () => () => "Controls tab");
jest.mock("./dependencies", () => ({
    NeedleUSDDependencyManager: {
        loadUSDViewer: jest.fn(() => Promise.resolve()),
        getUSDBundle: jest.fn(() => (global as any).__fakeUsdBundle),
        cleanup: jest.fn(),
    },
}));

const HINT = "Mouse: Rotate | Wheel: Zoom | Right-click: Pan";

let nextUuid = 1;

class FakeVector3 {
    x = 1;
    y = 1;
    z = 1;
    set() {
        return this;
    }
}

class FakeBox3 {
    union() {
        return this;
    }
    setFromObject() {
        return this;
    }
    getSize() {
        return new FakeVector3();
    }
    getCenter() {
        return new FakeVector3();
    }
}

class FakeObject3D {
    uuid = `object-${nextUuid++}`;
    type = "Group";
    name = "";
    visible = true;
    userData: Record<string, any> = {};
    children: any[] = [];
    // Without a position the details leave out the world transforms, which need real three.js
    position: any = undefined;
    rotation: any = { x: 0, y: 0, z: 0 };
    add(child: any) {
        this.children.push(child);
    }
    traverse(callback: (obj: any) => void) {
        callback(this);
        this.children.forEach((child) => child.traverse(callback));
    }
}

class FakeLight extends FakeObject3D {
    type = "Light";
    position = new FakeVector3();
}

const fakeThree = {
    Scene: class extends FakeObject3D {
        type = "Scene";
        background: any = null;
    },
    Group: FakeObject3D,
    Color: class {},
    AmbientLight: FakeLight,
    DirectionalLight: FakeLight,
    Box3: FakeBox3,
    Vector3: FakeVector3,
    Vector2: class {
        x = 0;
        y = 0;
    },
    Raycaster: class {
        setFromCamera = jest.fn();
        intersectObjects = jest.fn(() => []);
    },
    PerspectiveCamera: class {
        fov = 60;
        aspect = 1;
        near = 0.1;
        far = 1000;
        position = { set: jest.fn() };
        lookAt = jest.fn();
        updateProjectionMatrix = jest.fn();
    },
    WebGLRenderer: class {
        domElement = document.createElement("canvas");
        setSize = jest.fn();
        setPixelRatio = jest.fn();
        render = jest.fn();
        dispose = jest.fn();
    },
};

// A USD module whose stage loads without reporting a missing file
const fakeUsdModule = {
    FS_createPath: jest.fn(),
    FS_createDataFile: jest.fn(),
    FS_analyzePath: jest.fn(() => ({ exists: false })),
    FS_readdir: jest.fn(() => []),
    FS_unlink: jest.fn(),
    FS_rmdir: jest.fn(),
    HdWebSyncDriver: class {
        isDeleted = false;
        Draw = jest.fn();
        SetTime = jest.fn();
        delete() {
            this.isDeleted = true;
        }
        GetStage() {
            return {
                GetUpAxis: () => "y".charCodeAt(0),
                GetEndTimeCode: () => 0,
                GetTimeCodesPerSecond: () => 24,
            };
        }
    },
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
    const zUpper = Number(upper.style.zIndex);
    const zLower = Number(lower.style.zIndex);
    expect(upper.style.zIndex).not.toBe("");
    expect(lower.style.zIndex).not.toBe("");
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

// Lets the viewer's load run to the scene: its downloads, then the two waits for the module
const finishLoading = async () => {
    for (let step = 0; step < 6; step++) {
        await act(async () => {
            await jest.advanceTimersByTimeAsync(1000);
        });
    }
};

const viewer = (props: { assetKey?: string; multiFileKeys?: string[] }) => (
    <NeedleUSDViewerComponent
        assetId="asset-1"
        databaseId="db-1"
        viewerMode="collapse"
        onViewerModeChange={jest.fn()}
        {...props}
    />
);

describe("NeedleUSDViewerComponent stacking", () => {
    const originalFetch = global.fetch;
    const originalSharedArrayBuffer = (global as any).SharedArrayBuffer;

    beforeAll(() => {
        Element.prototype.scrollIntoView = jest.fn();
    });

    beforeEach(() => {
        jest.useFakeTimers();
        (global as any).__fakeUsdBundle = {
            THREE: fakeThree,
            ThreeRenderDelegateInterface: class {},
            getUsdModule: jest.fn(() => Promise.resolve(fakeUsdModule)),
        };
        // The viewer checks for it before it loads the module
        (global as any).SharedArrayBuffer = originalSharedArrayBuffer ?? ArrayBuffer;
        global.fetch = jest.fn((url: string) =>
            Promise.resolve(
                String(url).includes("broken.usdc")
                    ? { ok: false, status: 404 }
                    : { ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) }
            )
        ) as any;
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "warn").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => {
        jest.useRealTimers();
        global.fetch = originalFetch;
        (global as any).SharedArrayBuffer = originalSharedArrayBuffer;
        delete (global as any).__fakeUsdBundle;
        jest.restoreAllMocks();
    });

    it("draws the panel, with its tabs and Clear Selection, above the info box", async () => {
        render(viewer({ assetKey: "models/part.usdc" }));
        await finishLoading();

        // Select the file in the scene graph, so Clear Selection is shown
        fireEvent.click(document.querySelector("[data-node-id]") as HTMLElement);
        const clearSelection = screen.getByTitle("Clear all selections");

        const panel = getPanel();
        expect(panel).toContainElement(clearSelection);
        expect(panel).toContainElement(screen.getByTitle("Controls"));
        expect(panel).toContainElement(screen.getByRole("separator", { name: "Resize tree view" }));
        expect(isDrawnAbove(panel, getInfoBox())).toBe(true);
    });

    it("draws the button that shows the panel again above the info box", async () => {
        render(viewer({ assetKey: "models/part.usdc" }));
        await finishLoading();
        fireEvent.click(screen.getByTitle("Hide panel (Esc)"));

        expect(isDrawnAbove(screen.getByTitle("Show controls panel"), getInfoBox())).toBe(true);
    });

    it("draws a notice about files that failed to load above the panel and the info box, and the loading cover above the panel", async () => {
        render(viewer({ multiFileKeys: ["models/part.usdc", "models/broken.usdc"] }));
        const ring = document.querySelector('[class^="loading-spinner-"]') as HTMLElement;
        const coverZ = Number(closestPositioned(ring, "absolute").style.zIndex);
        await finishLoading();

        const notice = closestPositioned(screen.getByText(/Some files failed to load/), "absolute");
        expect(notice).toContainElement(screen.getByTitle("Dismiss warnings"));
        expect(isDrawnAbove(notice, getPanel())).toBe(true);
        expect(isDrawnAbove(notice, getInfoBox())).toBe(true);
        expect(coverZ).toBeGreaterThan(Number(getPanel().style.zIndex));
    });

    describe("the info box beside the open panel", () => {
        it("is not drawn while the open panel overlaps it, so the panel reads on its own", async () => {
            layOut({ info: INFO_OVER_PANEL });
            render(viewer({ assetKey: "models/part.usdc" }));
            await finishLoading();

            expect(getInfoBox()).not.toBeVisible();
            expect(getPanel()).toBeVisible();
            expect(screen.getByTitle("Controls")).toBeVisible();
        });

        it("is drawn when there is room beside the panel", async () => {
            layOut({ info: INFO_BESIDE_PANEL });
            render(viewer({ assetKey: "models/part.usdc" }));
            await finishLoading();

            expect(getInfoBox()).toBeVisible();
            expect(getPanel()).toBeVisible();
        });

        it("is drawn while the panel is hidden, and left out again when the panel is shown", async () => {
            layOut({ info: INFO_OVER_PANEL });
            render(viewer({ assetKey: "models/part.usdc" }));
            await finishLoading();
            expect(getInfoBox()).not.toBeVisible();

            fireEvent.click(screen.getByTitle("Hide panel (Esc)"));
            expect(getInfoBox()).toBeVisible();

            fireEvent.click(screen.getByTitle("Show controls panel"));
            expect(getInfoBox()).not.toBeVisible();
        });

        it("is drawn again when the window widens and leaves room beside the panel", async () => {
            const layout = { info: INFO_OVER_PANEL };
            layOut(layout);
            render(viewer({ assetKey: "models/part.usdc" }));
            await finishLoading();
            expect(getInfoBox()).not.toBeVisible();

            layout.info = INFO_BESIDE_PANEL;
            act(() => {
                fireEvent(window, new Event("resize"));
            });
            expect(getInfoBox()).toBeVisible();
        });
    });
});

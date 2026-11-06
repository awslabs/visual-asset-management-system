/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the Three.js viewer draws over its scene is stacked by what it is: the panel the user works in
 * (and the button that shows it again) above the info box with the controls hint and scene stats, a
 * notice about files that failed to load above the panel, and the loading cover above all of them.
 * On a narrow window the info box overlaps the panel, and at the same z-index the info box, later in
 * the document, took the clicks on the panel's tabs and its Clear Selection button (#1094). The
 * viewer runs over a stand-in three.js with its real panel and scene graph, as in
 * ThreeJSViewerComponent.sceneTreeHeight.test.tsx.
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ThreeJSViewerComponent from "./ThreeJSViewerComponent";
import { ThreeJSDependencyManager } from "./dependencies";
import { loadFile } from "./utils/fileLoaders";

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ api: "https://api.example.test/" })) },
}));
jest.mock("../../../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(() => Promise.resolve("Bearer test")),
}));
jest.mock("./dependencies", () => ({
    ThreeJSDependencyManager: {
        loadThreeJS: jest.fn(() => Promise.resolve()),
        cleanup: jest.fn(),
    },
}));
jest.mock("./MouseControls", () => ({
    MouseControls: jest.fn().mockImplementation(() => ({
        hasMoved: false,
        setTarget: jest.fn(),
        dispose: jest.fn(),
    })),
}));
jest.mock("./components/MaterialLibrary", () => () => "Material library tab");
jest.mock("./components/Controls", () => () => "Controls tab");
jest.mock("./utils/gltfDependencyLoader", () => ({
    preloadGLTFDependencies: jest.fn(),
    cleanupBlobUrls: jest.fn(),
}));
jest.mock("./utils/sceneLighting", () => ({
    applySceneLighting: jest.fn(),
    applySceneEnvironment: jest.fn(() => ({ dispose: jest.fn() })),
}));
jest.mock("./utils/fileLoaders", () => ({
    ...jest.requireActual("./utils/fileLoaders"),
    loadFile: jest.fn(),
}));

const mockedLoadFile = loadFile as jest.MockedFunction<typeof loadFile>;

const HINT = "Mouse: Rotate | Wheel: Zoom | Right-click: Pan";

let nextUuid = 1;

class FakeVector3 {
    x = 1;
    y = 1;
    z = 1;
}

class FakeBox3 {
    union() {
        return this;
    }
    setFromObject() {
        return this;
    }
    isEmpty() {
        return true;
    }
    intersectsBox() {
        return false;
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
    position: any = { set: jest.fn() };
    add(child: any) {
        this.children.push(child);
    }
    traverse(callback: (obj: any) => void) {
        callback(this);
        this.children.forEach((child) => child.traverse(callback));
    }
}

function installFakeThree(): void {
    (window as any).THREE = {
        Scene: class extends FakeObject3D {
            type = "Scene";
            background: any = null;
        },
        Group: FakeObject3D,
        Color: class {},
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
            forceContextLoss = jest.fn();
        },
    };
}

const modelNamed = (name: string) => {
    const model = new FakeObject3D();
    model.type = "Mesh";
    model.name = name;
    // Without a position the details leave out the world transforms, which need real three.js
    model.position = undefined;
    return model;
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

describe("ThreeJSViewerComponent stacking", () => {
    const originalFetch = global.fetch;

    beforeAll(() => {
        Element.prototype.scrollIntoView = jest.fn();
    });

    beforeEach(() => {
        installFakeThree();
        (ThreeJSDependencyManager.loadThreeJS as jest.Mock).mockImplementation(() => {
            if (!(window as any).THREE) installFakeThree();
            return Promise.resolve();
        });
        mockedLoadFile.mockReset();
        mockedLoadFile.mockImplementation(async () => ({ object: modelNamed("Cube") as any }));
        global.fetch = jest.fn(() =>
            Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) })
        ) as any;
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => {
        global.fetch = originalFetch;
        delete (window as any).THREE;
        jest.restoreAllMocks();
    });

    it("draws the panel, with its tabs and Clear Selection, above the info box", async () => {
        render(
            <ThreeJSViewerComponent
                assetId="asset-1"
                databaseId="db-1"
                assetKey="models/part.glb"
                viewerMode="collapse"
                onViewerModeChange={jest.fn()}
            />
        );
        await screen.findByTitle("Hide panel (Esc)");

        // Select the file in the scene graph, so Clear Selection is shown
        fireEvent.click(document.querySelector("[data-node-id]") as HTMLElement);
        const clearSelection = screen.getByTitle("Clear all selections");

        const panel = getPanel();
        const infoBox = getInfoBox();
        expect(panel).toContainElement(clearSelection);
        expect(panel).toContainElement(screen.getByTitle("Controls"));
        expect(panel).toContainElement(screen.getByRole("separator", { name: "Resize tree view" }));
        expect(isDrawnAbove(panel, infoBox)).toBe(true);
    });

    it("draws the button that shows the panel again above the info box", async () => {
        render(
            <ThreeJSViewerComponent
                assetId="asset-1"
                databaseId="db-1"
                assetKey="models/part.glb"
                viewerMode="collapse"
                onViewerModeChange={jest.fn()}
            />
        );
        fireEvent.click(await screen.findByTitle("Hide panel (Esc)"));

        const showPanel = screen.getByTitle("Show controls panel");
        expect(isDrawnAbove(showPanel, getInfoBox())).toBe(true);
    });

    it("draws a notice about files that failed to load above the panel and the info box", async () => {
        global.fetch = jest.fn((url: string) =>
            Promise.resolve(
                String(url).includes("broken.glb")
                    ? { ok: false, status: 404 }
                    : { ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) }
            )
        ) as any;
        render(
            <ThreeJSViewerComponent
                assetId="asset-1"
                databaseId="db-1"
                multiFileKeys={["models/part.glb", "models/broken.glb"]}
                viewerMode="collapse"
                onViewerModeChange={jest.fn()}
            />
        );
        await screen.findByTitle("Hide panel (Esc)");

        const notice = closestPositioned(screen.getByText(/Some files failed to load/), "absolute");
        expect(notice).toContainElement(screen.getByTitle("Dismiss warnings"));
        expect(isDrawnAbove(notice, getPanel())).toBe(true);
        expect(isDrawnAbove(notice, getInfoBox())).toBe(true);
    });

    it("stacks the loading cover above the panel", async () => {
        let finishLoad: () => void = () => undefined;
        mockedLoadFile.mockImplementationOnce(
            () =>
                new Promise((resolve) => {
                    finishLoad = () => resolve({ object: modelNamed("Cube") as any });
                })
        );
        render(
            <ThreeJSViewerComponent
                assetId="asset-1"
                databaseId="db-1"
                assetKey="models/part.glb"
                viewerMode="collapse"
                onViewerModeChange={jest.fn()}
            />
        );
        await waitFor(() => expect(mockedLoadFile).toHaveBeenCalled());
        // The spinner's root holds the spinning ring, the element with the spinner's class
        const ring = document.querySelector('[class^="loading-spinner-"]') as HTMLElement;
        const cover = closestPositioned(ring, "absolute");
        expect(cover).toHaveTextContent(/\S/);
        const coverZ = Number(cover.style.zIndex);

        await act(async () => finishLoad());
        await screen.findByTitle("Hide panel (Esc)");
        expect(coverZ).toBeGreaterThan(Number(getPanel().style.zIndex));
    });
});

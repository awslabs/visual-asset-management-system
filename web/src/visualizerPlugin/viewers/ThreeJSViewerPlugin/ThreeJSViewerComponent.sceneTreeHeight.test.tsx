/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The scene tree height the user picks in the viewer's panel stays while the panel switches to
 * another tab and back, and while the panel is hidden and shown, for as long as the viewer shows the
 * same file. The viewer runs over a stand-in three.js with its real panel and scene graph; the other
 * two tabs are stand-ins. The scene graph's column is laid out from the top down as in
 * SceneGraph.test.tsx: in a 1000px column with a 42px header and an 8px handle, a drag or End allows
 * a tree of 734px, and the tree takes 887px while nothing is selected.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
jest.mock("../../components/LoadingSpinner", () => () => null);
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

const COLUMN_HEIGHT = 1000;
const HEADER_HEIGHT = 42;
const HANDLE_HEIGHT = 8;
// The tree's padding above and below its style height
const TREE_PADDING = 16;
const layout = { column: COLUMN_HEIGHT };

let nextUuid = 1;
// The model the stand-in loader returned last, and what a click on the 3D view hits
let loadedModel: any = null;
let canvasHits: Array<{ object: any }> = [];

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
    position = { set: jest.fn() };
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
            intersectObjects = jest.fn(() => canvasHits);
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

const viewer = (assetKey: string) => (
    <ThreeJSViewerComponent
        assetId="asset-1"
        databaseId="db-1"
        assetKey={assetKey}
        viewerMode="wide"
        onViewerModeChange={jest.fn()}
    />
);

const findHandle = () => screen.findByRole("separator", { name: "Resize tree view" });
const getHandle = () => screen.getByRole("separator", { name: "Resize tree view" });

const getTree = (handle: HTMLElement) =>
    document.getElementById(handle.getAttribute("aria-controls") ?? "") as HTMLElement;

const expectTreeHeight = (handle: HTMLElement, height: number) => {
    expect(getTree(handle).style.height).toBe(`${height}px`);
    expect(handle).toHaveAttribute("aria-valuenow", String(height));
};

// Home, then three ArrowDown presses: a 210px tree
const pickHeight = (handle: HTMLElement) => {
    fireEvent.keyDown(handle, { key: "Home" });
    for (let press = 0; press < 3; press++) {
        fireEvent.keyDown(handle, { key: "ArrowDown" });
    }
    expectTreeHeight(handle, 210);
};

const switchTabAway = () => {
    fireEvent.click(screen.getByTitle("Material Library"));
    expect(screen.queryByRole("separator")).toBeNull();
    expect(screen.getByText("Material library tab")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("Controls"));
    expect(screen.getByText("Controls tab")).toBeInTheDocument();
};

const switchTabBack = () => fireEvent.click(screen.getByTitle("Scene Graph"));

const switchTabAwayAndBack = () => {
    switchTabAway();
    switchTabBack();
};

// Clicks the 3D view on the loaded model, which selects it, or clears the selection when it is the
// only selected object
const clickModelInView = () => {
    canvasHits = [{ object: loadedModel }];
    fireEvent.click(document.querySelector("canvas") as HTMLCanvasElement);
};

// The handle reads only the top, bottom and height of a box
const rect = (top: number, height: number) => ({ top, height, bottom: top + height } as DOMRect);

// Lays the scene graph's column out from the top down: the header, the tree with its padding, the
// handle
function layOut(this: HTMLElement): DOMRect {
    const handle = document.querySelector<HTMLElement>('[role="separator"]');
    const tree = handle?.previousElementSibling as HTMLElement | null | undefined;
    const treeBox = parseFloat(tree?.style.height ?? "0") + TREE_PADDING;
    if (handle && this === handle.parentElement) return rect(0, layout.column);
    if (tree && this === tree) return rect(HEADER_HEIGHT, treeBox);
    if (handle && this === handle) return rect(HEADER_HEIGHT + treeBox, HANDLE_HEIGHT);
    return rect(0, 0);
}

describe("ThreeJSViewerComponent scene tree height", () => {
    const originalFetch = global.fetch;

    beforeAll(() => {
        Element.prototype.scrollIntoView = jest.fn();
    });

    beforeEach(() => {
        layout.column = COLUMN_HEIGHT;
        installFakeThree();
        // Cleanup removes the global, and the loader puts it back, as the real one does
        (ThreeJSDependencyManager.loadThreeJS as jest.Mock).mockImplementation(() => {
            if (!(window as any).THREE) installFakeThree();
            return Promise.resolve();
        });
        mockedLoadFile.mockReset();
        canvasHits = [];
        mockedLoadFile.mockImplementation(async () => {
            loadedModel = new FakeObject3D();
            loadedModel.type = "Mesh";
            loadedModel.name = "Cube";
            // Without a position the details leave out the world transforms, which need real three.js
            loadedModel.position = undefined;
            return { object: loadedModel };
        });
        global.fetch = jest.fn(() =>
            Promise.resolve({
                ok: true,
                arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)),
            })
        ) as any;
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(layOut);
    });

    afterEach(() => {
        global.fetch = originalFetch;
        delete (window as any).THREE;
        jest.restoreAllMocks();
    });

    it("keeps a height the user picked when the panel switches to another tab and back, through a selection change made meanwhile", async () => {
        render(viewer("models/part.glb"));
        expectTreeHeight(await findHandle(), 887);

        // Not resized: the tree follows a selection made in the 3D view while it was away.
        switchTabAway();
        clickModelInView();
        switchTabBack();
        expectTreeHeight(getHandle(), 300);

        pickHeight(getHandle());
        switchTabAway();
        clickModelInView();
        switchTabBack();
        expectTreeHeight(getHandle(), 210);

        // The kept height is the latest one the user picked.
        fireEvent.keyDown(getHandle(), { key: "ArrowDown" });
        switchTabAwayAndBack();
        expectTreeHeight(getHandle(), 230);
    });

    it("keeps a height the user picked when the panel is hidden and shown again", async () => {
        render(viewer("models/part.glb"));
        pickHeight(await findHandle());

        fireEvent.click(screen.getByTitle("Hide panel (Esc)"));
        expect(screen.queryByRole("separator")).toBeNull();
        fireEvent.click(screen.getByTitle("Show controls panel"));
        expectTreeHeight(getHandle(), 210);

        fireEvent.keyDown(window, { key: "Escape" });
        expect(screen.queryByRole("separator")).toBeNull();
        fireEvent.click(screen.getByTitle("Show controls panel"));
        expectTreeHeight(getHandle(), 210);
    });

    it("brings a kept height within the automatic height when the column shrank while the tree was away, and within the drag's limit on a selection", async () => {
        render(viewer("models/part.glb"));
        const handle = await findHandle();
        fireEvent.keyDown(handle, { key: "Home" });
        fireEvent.keyDown(handle, { key: "End" });
        fireEvent.keyDown(handle, { key: "ArrowUp" });
        expectTreeHeight(handle, 714);

        // Nothing is selected: the automatic height, which leaves the details 47px.
        fireEvent.click(screen.getByTitle("Controls"));
        layout.column = 700;
        fireEvent.click(screen.getByTitle("Scene Graph"));
        const returned = getHandle();
        expectTreeHeight(returned, 587);
        expect(layout.column - returned.getBoundingClientRect().bottom).toBe(47);

        clickModelInView();
        expectTreeHeight(getHandle(), 434);
        expect(layout.column - getHandle().getBoundingClientRect().bottom).toBe(200);
    });

    it("keeps a height picked above the drag's limit with nothing selected when the panel switches tabs or is hidden", async () => {
        render(viewer("models/part.glb"));
        fireEvent.keyDown(await findHandle(), { key: "ArrowUp" });
        expectTreeHeight(getHandle(), 867);

        switchTabAwayAndBack();
        expectTreeHeight(getHandle(), 867);
        fireEvent.click(screen.getByTitle("Hide panel (Esc)"));
        fireEvent.click(screen.getByTitle("Show controls panel"));
        expectTreeHeight(getHandle(), 867);

        // A selection brings it within the drag's limit.
        clickModelInView();
        expectTreeHeight(getHandle(), 734);
        expect(layout.column - getHandle().getBoundingClientRect().bottom).toBe(200);
    });

    it("starts a new file at the default height", async () => {
        const { rerender } = render(viewer("models/part.glb"));
        pickHeight(await findHandle());
        fireEvent.click(screen.getByTitle("Hide panel (Esc)"));
        fireEvent.click(screen.getByTitle("Show controls panel"));
        expectTreeHeight(getHandle(), 210);

        // The new file is still loading when the viewer renders again, as with a real download, so
        // the panel leaves while it loads.
        let finishLoad: () => void = () => undefined;
        mockedLoadFile.mockImplementationOnce(
            () =>
                new Promise((resolve) => {
                    finishLoad = () => resolve({ object: new FakeObject3D() as any });
                })
        );
        rerender(viewer("models/other.glb"));
        await waitFor(() => expect(mockedLoadFile).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(screen.queryByRole("separator")).toBeNull());
        finishLoad();
        expectTreeHeight(await findHandle(), 887);

        // The tree follows the selection again, also after a tab switch.
        switchTabAwayAndBack();
        expectTreeHeight(getHandle(), 887);
    });

    it("starts a viewer that is opened again at the default height", async () => {
        const { unmount } = render(viewer("models/part.glb"));
        pickHeight(await findHandle());
        switchTabAwayAndBack();
        expectTreeHeight(getHandle(), 210);
        unmount();

        render(viewer("models/part.glb"));
        expectTreeHeight(await findHandle(), 887);
    });
});

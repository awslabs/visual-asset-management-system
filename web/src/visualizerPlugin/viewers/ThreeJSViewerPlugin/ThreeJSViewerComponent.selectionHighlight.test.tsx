/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Selecting an object in the Three.js viewer's panel draws it with a highlight clone of its
 * material, and deselecting it puts the original material back (#1275). A lit mesh material
 * glows green through its emissive colour, as before. A point cloud's `PointsMaterial` has no
 * emissive colour, so it is tinted green with its vertex colours off; before the fix it was not
 * highlighted at all. The viewer runs over a stand-in three.js with its real panel and scene graph,
 * as in ThreeJSViewerComponent.sceneTreeHeight.test.tsx.
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
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

const GREEN = 0x4caf50;

let nextUuid = 1;
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

class FakeColor {
    isColor = true;
    constructor(public hex: number = 0xffffff) {}
    getHex() {
        return this.hex;
    }
    getHexString() {
        return this.hex.toString(16).padStart(6, "0");
    }
    clone() {
        return new FakeColor(this.hex);
    }
}

/** Copies its own properties on clone(), as three.js's Material.copy does for these fields. */
class FakeMaterial {
    uuid = `material-${nextUuid++}`;
    name = "";
    type = "Material";
    opacity = 1;
    transparent = false;
    needsUpdate = false;
    color = new FakeColor();
    dispose = jest.fn();
    clone(): any {
        const copy = new (this.constructor as any)();
        Object.keys(this).forEach((key) => {
            if (key === "uuid" || key === "dispose") return;
            const value = (this as any)[key];
            copy[key] = value?.isColor ? value.clone() : value;
        });
        return copy;
    }
}

/** No emissive and no wireframe, as in three.js. */
class FakePointsMaterial extends FakeMaterial {
    isPointsMaterial = true;
    type = "PointsMaterial";
    size = 0.01;
    vertexColors = true;
}

class FakeMeshStandardMaterial extends FakeMaterial {
    isMeshStandardMaterial = true;
    type = "MeshStandardMaterial";
    emissive = new FakeColor(0x000000);
    emissiveIntensity = 1;
    metalness = 0;
    roughness = 1;
    wireframe = false;
}

class FakeObject3D {
    uuid = `object-${nextUuid++}`;
    type = "Group";
    name = "";
    visible = true;
    userData: Record<string, any> = {};
    children: any[] = [];
    position: any = { set: jest.fn() };
    material: any = undefined;
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
        Color: FakeColor,
        MeshStandardMaterial: FakeMeshStandardMaterial,
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

/** A loaded object of `type` drawn with `material`, without a position (see sceneTreeHeight). */
function makeObject(type: string, name: string, material: any): any {
    const object = new FakeObject3D();
    object.type = type;
    object.name = name;
    object.position = undefined;
    object.material = material;
    return object;
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

/**
 * Clicks the object's row in the panel's scene graph, which selects it or, when it is the only
 * selection, clears it. The file's root row carries the file name too; the object's row is last.
 */
async function clickRow(name: string): Promise<void> {
    const labels = await screen.findAllByText(name);
    const rows = labels
        .map((label) => label.closest("[data-node-id]"))
        .filter((row): row is HTMLElement => !!row);
    expect(rows.length).toBeGreaterThan(0);
    const row = rows[rows.length - 1];
    fireEvent.click(row);
}

describe("ThreeJSViewerComponent selection highlight", () => {
    const originalFetch = global.fetch;
    let loaded: any;

    beforeAll(() => {
        Element.prototype.scrollIntoView = jest.fn();
    });

    beforeEach(() => {
        installFakeThree();
        (ThreeJSDependencyManager.loadThreeJS as jest.Mock).mockImplementation(() => {
            if (!(window as any).THREE) installFakeThree();
            return Promise.resolve();
        });
        canvasHits = [];
        mockedLoadFile.mockReset();
        mockedLoadFile.mockImplementation(async () => ({ object: loaded }));
        global.fetch = jest.fn(() =>
            Promise.resolve({
                ok: true,
                arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)),
            })
        ) as any;
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });

    afterEach(() => {
        global.fetch = originalFetch;
        delete (window as any).THREE;
        jest.restoreAllMocks();
    });

    it("tints a selected point cloud green with its vertex colours off, and puts the exact original material back on deselect", async () => {
        const original = new FakePointsMaterial();
        original.color = new FakeColor(0xffffff);
        loaded = makeObject("Points", "fragment.ply", original);

        render(viewer("clouds/fragment.ply"));
        await clickRow("fragment.ply");

        const highlight = loaded.material;
        expect(highlight).not.toBe(original);
        expect(highlight.isPointsMaterial).toBe(true);
        expect(highlight.color.getHex()).toBe(GREEN);
        expect(highlight.vertexColors).toBe(false);
        expect(highlight.needsUpdate).toBe(true);
        // The original is untouched while the clone is shown.
        expect(original.color.getHex()).toBe(0xffffff);
        expect(original.vertexColors).toBe(true);

        await clickRow("fragment.ply");
        expect(loaded.material).toBe(original);
        expect(highlight.dispose).toHaveBeenCalledTimes(1);
        expect(original.dispose).not.toHaveBeenCalled();
    });

    it("restores a point cloud selected in the 3D view when the selection moves to another object", async () => {
        const pointsMaterial = new FakePointsMaterial();
        const meshMaterial = new FakeMeshStandardMaterial();
        const cloud = makeObject("Points", "cloud.ply", pointsMaterial);
        const mesh = makeObject("Mesh", "Cube", meshMaterial);
        loaded = new FakeObject3D();
        loaded.name = "scene.glb";
        loaded.position = undefined;
        loaded.add(cloud);
        loaded.add(mesh);

        render(viewer("models/scene.glb"));
        await screen.findAllByText("scene.glb");

        canvasHits = [{ object: cloud }];
        fireEvent.click(document.querySelector("canvas") as HTMLCanvasElement);
        expect(cloud.material).not.toBe(pointsMaterial);
        expect(cloud.material.color.getHex()).toBe(GREEN);

        canvasHits = [{ object: mesh }];
        fireEvent.click(document.querySelector("canvas") as HTMLCanvasElement);
        expect(cloud.material).toBe(pointsMaterial);
        expect(mesh.material).not.toBe(meshMaterial);
    });

    it("still highlights a selected mesh through its emissive colour and leaves its own colour", async () => {
        const original = new FakeMeshStandardMaterial();
        original.color = new FakeColor(0x336699);
        loaded = makeObject("Mesh", "Cube", original);

        render(viewer("models/part.glb"));
        await clickRow("Cube");

        const highlight = loaded.material;
        expect(highlight).not.toBe(original);
        expect(highlight.emissive.getHex()).toBe(GREEN);
        expect(highlight.emissiveIntensity).toBe(0.5);
        expect(highlight.color.getHex()).toBe(0x336699);
        expect(original.emissive.getHex()).toBe(0x000000);

        await clickRow("Cube");
        expect(loaded.material).toBe(original);
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The viewer creates its WebGL renderer before any file loads. Each renderer holds a WebGL context,
 * and browsers drop the oldest context once a page holds too many. three.js's dispose() frees the
 * renderer's GPU resources but keeps the context until the canvas is garbage collected, so these
 * tests pin that unmounting releases the renderer, its scene environment, its canvas and its context
 * whatever the load outcome: a load that failed, one still in flight, and one that succeeded.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ThreeJSViewerComponent from "./ThreeJSViewerComponent";
import { loadFile } from "./utils/fileLoaders";
import { applySceneEnvironment } from "./utils/sceneLighting";
import { AssetDependencyResolver } from "./utils/assetDependencyResolver";
import { USD_NO_GEOMETRY_MESSAGE } from "./utils/dependencyLoading";

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
jest.mock("./ThreeJSPanel", () => () => null);
jest.mock("../../components/LoadingSpinner", () => () => null);
jest.mock("./utils/gltfDependencyLoader", () => ({
    preloadGLTFDependencies: jest.fn(),
    cleanupBlobUrls: jest.fn(),
}));
jest.mock("./utils/sceneLighting", () => ({
    applySceneLighting: jest.fn(),
    applySceneEnvironment: jest.fn(),
}));
jest.mock("./utils/fileLoaders", () => ({
    ...jest.requireActual("./utils/fileLoaders"),
    loadFile: jest.fn(),
}));

const mockedLoadFile = loadFile as jest.MockedFunction<typeof loadFile>;
const mockedApplySceneEnvironment = applySceneEnvironment as jest.MockedFunction<
    typeof applySceneEnvironment
>;

interface FakeRenderer {
    domElement: HTMLCanvasElement;
    render: jest.Mock;
    dispose: jest.Mock;
    forceContextLoss: jest.Mock;
}

/** Every renderer and scene environment the viewer creates, so a test can check each was released. */
let renderers: FakeRenderer[] = [];
let environments: Array<{ dispose: jest.Mock }> = [];

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
    type = "Group";
    name = "";
    userData: Record<string, any> = {};
    children: any[] = [];
    position = { set: jest.fn() };
    add(child: any) {
        this.children.push(child);
    }
    traverse(callback: (obj: any) => void) {
        callback(this);
        this.children.forEach((child) =>
            typeof child.traverse === "function" ? child.traverse(callback) : callback(child)
        );
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
            constructor() {
                renderers.push(this as unknown as FakeRenderer);
            }
        },
    };
}

function renderViewer(assetKey: string) {
    return render(
        <ThreeJSViewerComponent
            assetId="asset-1"
            databaseId="db-1"
            assetKey={assetKey}
            viewerMode="wide"
            onViewerModeChange={jest.fn()}
        />
    );
}

function expectReleased(renderer: FakeRenderer, environment: { dispose: jest.Mock }) {
    expect(renderer.dispose).toHaveBeenCalledTimes(1);
    expect(environment.dispose).toHaveBeenCalledTimes(1);
    expect(renderer.domElement.isConnected).toBe(false);
    // dispose() keeps the WebGL context until garbage collection; the context itself is released
    // afterwards, once dispose() has removed the renderer's context-lost listener.
    expect(renderer.forceContextLoss).toHaveBeenCalledTimes(1);
    expect(renderer.forceContextLoss.mock.invocationCallOrder[0]).toBeGreaterThan(
        renderer.dispose.mock.invocationCallOrder[0]
    );
}

describe("ThreeJSViewerComponent cleanup", () => {
    const originalFetch = global.fetch;

    beforeEach(() => {
        renderers = [];
        environments = [];
        installFakeThree();
        mockedLoadFile.mockReset();
        mockedApplySceneEnvironment.mockImplementation(() => {
            const environment = { dispose: jest.fn() };
            environments.push(environment);
            return environment as any;
        });
        global.fetch = jest.fn(() =>
            Promise.resolve({
                ok: true,
                arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)),
            })
        ) as any;
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => {
        global.fetch = originalFetch;
        delete (window as any).THREE;
        jest.restoreAllMocks();
    });

    it("releases the renderer when every file failed to load", async () => {
        mockedLoadFile.mockRejectedValue(new Error("Failed to parse file"));

        const view = renderViewer("models/broken.glb");
        await screen.findByText(/Failed to parse file/);
        expect(renderers).toHaveLength(1);
        expect(renderers[0].dispose).not.toHaveBeenCalled();

        view.unmount();

        expectReleased(renderers[0], environments[0]);
    });

    it("releases the renderer when the viewer closes while a file is still loading", async () => {
        mockedLoadFile.mockReturnValue(new Promise(() => undefined));

        const view = renderViewer("models/part.glb");
        await waitFor(() => expect(mockedLoadFile).toHaveBeenCalled());
        expect(renderers).toHaveLength(1);

        view.unmount();

        expectReleased(renderers[0], environments[0]);
    });

    it("revokes referenced-file blob URLs when the viewer closes after a failed load", async () => {
        const revokeAll = jest.spyOn(AssetDependencyResolver.prototype, "revokeAll");
        mockedLoadFile.mockRejectedValue(new Error("Failed to parse file"));

        const view = renderViewer("models/broken.usda");
        await screen.findByText(/Failed to parse file/);
        expect(revokeAll).not.toHaveBeenCalled();

        view.unmount();

        expect(revokeAll).toHaveBeenCalledTimes(1);
    });

    it("lists referenced files that were not loaded, ten by name", async () => {
        const model = new FakeObject3D();
        model.type = "Mesh";
        (model as any).isMesh = true;
        const warnings = Array.from({ length: 12 }, (_, i) => `tex${i}.png (not found)`);
        mockedLoadFile.mockResolvedValue({ object: model, warnings });

        const view = renderViewer("models/scene.usda");

        expect(await screen.findByText("12 referenced files were not loaded")).toBeTruthy();
        expect(screen.getByText("tex0.png (not found)")).toBeTruthy();
        expect(screen.queryByText("tex10.png (not found)")).toBeNull();
        expect(screen.getByText("and 2 more")).toBeTruthy();
        expect(
            screen.getByRole("region", { name: "Referenced files that were not loaded" }).tabIndex
        ).toBe(0);

        view.unmount();
    });

    it("names the notice's dismiss button and lets the keyboard reach it", async () => {
        const model = new FakeObject3D();
        model.type = "Mesh";
        (model as any).isMesh = true;
        mockedLoadFile.mockResolvedValue({ object: model, warnings: ["x.png (not found)"] });

        const view = renderViewer("models/scene.usda");
        await screen.findByText("1 referenced file was not loaded");

        const dismiss = screen.getByRole("button", { name: "Dismiss referenced files notice" });
        dismiss.focus();
        expect(document.activeElement).toBe(dismiss);
        fireEvent.click(dismiss);
        await waitFor(() =>
            expect(screen.queryByText("1 referenced file was not loaded")).toBeNull()
        );

        view.unmount();
    });

    it("explains a USD scene with nothing to draw instead of showing a blank canvas", async () => {
        mockedLoadFile.mockResolvedValue({ object: new FakeObject3D() });

        const view = renderViewer("models/Teapot.usd");

        expect(await screen.findByText(/found nothing in it to display/)).toBeTruthy();
        expect(screen.getByText(/such as sublayers, variants or payloads/)).toBeTruthy();
        expect(screen.queryByText(/Please ensure the file is a supported 3D format/)).toBeNull();

        view.unmount();
    });

    it("keeps the referenced files notice over the no-geometry message", async () => {
        const failure = Object.assign(new Error(USD_NO_GEOMETRY_MESSAGE), {
            warnings: ["layers/geo.usda (not shown by this viewer (USD sublayers))"],
        });
        mockedLoadFile.mockRejectedValue(failure);

        const view = renderViewer("models/scene.usda");

        expect(await screen.findByText(/found nothing in it to display/)).toBeTruthy();
        expect(screen.getByText("1 referenced file was not loaded")).toBeTruthy();
        expect(
            screen.getByText("layers/geo.usda (not shown by this viewer (USD sublayers))")
        ).toBeTruthy();

        view.unmount();
    });

    it("releases the renderer and its context exactly once after a successful load", async () => {
        const model = new FakeObject3D();
        model.type = "Mesh";
        (model as any).isMesh = true;
        mockedLoadFile.mockResolvedValue({ object: model });

        const view = renderViewer("models/part.glb");
        await waitFor(() => expect(renderers[0]?.render).toHaveBeenCalled());
        expect(renderers[0].domElement.isConnected).toBe(true);

        view.unmount();

        expectReleased(renderers[0], environments[0]);
    });
});

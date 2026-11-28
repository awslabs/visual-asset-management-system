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
 *
 * Cleanup also aborts the run's downloads. That abort is a cancellation, not a failure: the load
 * stops without logging or listing the file, while any other error is still logged and shown (#1643).
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ThreeJSViewerComponent from "./ThreeJSViewerComponent";
import { ThreeJSDependencyManager } from "./dependencies";
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
jest.mock("../../../services/APIService", () => ({
    downloadAsset: jest.fn(async ({ key }: { key: string }) => [
        true,
        `https://bucket.s3.example.test/${key}?X-Amz-Signature=sig`,
    ]),
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

function viewerElement(assetKey: string) {
    return (
        <ThreeJSViewerComponent
            assetId="asset-1"
            databaseId="db-1"
            assetKey={assetKey}
            viewerMode="wide"
            onViewerModeChange={jest.fn()}
        />
    );
}

function renderViewer(assetKey: string) {
    return render(viewerElement(assetKey));
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

    it("explains a CAD file with no drawable geometry and releases the renderer (#1734)", async () => {
        mockedLoadFile.mockRejectedValue(
            new Error(
                "The viewer could not build any geometry from this STEP file. Convert it to a " +
                    "format such as GLB, for example with the 3D Basic Conversion pipeline, to view it."
            )
        );

        const view = renderViewer("models/rgbmatrix.step");

        expect(
            await screen.findByText(/could not build any geometry from this STEP file/)
        ).toBeTruthy();
        expect(screen.queryByText(/Please ensure the file is a supported 3D format/)).toBeNull();

        view.unmount();
        expectReleased(renderers[0], environments[0]);
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

/**
 * A fetch that holds the download of `heldKey` until its signal aborts, then rejects it with an
 * AbortError as a browser does, and answers every other file at once.
 */
function fetchHoldingUntilAborted(heldKey: string) {
    return jest.fn((url: string, init?: RequestInit) => {
        if (String(url).includes(heldKey)) {
            return new Promise((_resolve, reject) => {
                init?.signal?.addEventListener("abort", () =>
                    reject(new DOMException("The operation was aborted.", "AbortError"))
                );
            });
        }
        return Promise.resolve({
            ok: true,
            arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)),
        });
    });
}

/** The scene-level failure line, logged once when no file could be shown. */
const SCENE_ERROR_LINE = "Error loading ThreeJS assets:";

/** The viewer's per-file failure lines ("Error loading <key>:"). */
function loadErrorLines(): unknown[][] {
    return (console.error as jest.Mock).mock.calls.filter(
        (args) => /^Error loading /.test(String(args[0])) && args[0] !== SCENE_ERROR_LINE
    );
}

/** Whether the scene-level failure line was logged. */
function sceneErrorLogged(): boolean {
    return (console.error as jest.Mock).mock.calls.some((args) => args[0] === SCENE_ERROR_LINE);
}

function mesh(): FakeObject3D {
    const model = new FakeObject3D();
    model.type = "Mesh";
    (model as any).isMesh = true;
    return model;
}

describe("ThreeJSViewerComponent load cancellation (#1643)", () => {
    const originalFetch = global.fetch;
    const mockedLoadThreeJS = ThreeJSDependencyManager.loadThreeJS as jest.Mock;

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
        // Cleanup deletes window.THREE and the next run loads it again, as the real loader does.
        mockedLoadThreeJS.mockImplementation(() => {
            if (!(window as any).THREE) installFakeThree();
            return Promise.resolve();
        });
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => {
        global.fetch = originalFetch;
        delete (window as any).THREE;
        mockedLoadThreeJS.mockImplementation(() => Promise.resolve());
        jest.restoreAllMocks();
    });

    it("cancels a download its own cleanup aborted, logging and listing nothing for it", async () => {
        const fetchMock = fetchHoldingUntilAborted("models/first.glb");
        global.fetch = fetchMock as any;
        mockedLoadFile.mockResolvedValue({
            object: mesh(),
            warnings: ["second-tex.png (not found)"],
        });

        const view = renderViewer("models/first.glb");
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

        // Other files mid-download: the load effect re-runs and its cleanup aborts the download.
        view.rerender(viewerElement("models/second.glb"));

        // The second load completes and lists only its own referenced file.
        expect(await screen.findByText("1 referenced file was not loaded")).toBeTruthy();
        expect(screen.getByText("second-tex.png (not found)")).toBeTruthy();
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(loadErrorLines()).toEqual([]);
        expect(sceneErrorLogged()).toBe(false);
        expect(screen.queryByText(/failed to load/i)).toBeNull();
        // The aborted file was never parsed: the only parse is the second file's.
        expect(mockedLoadFile).toHaveBeenCalledTimes(1);

        view.unmount();
    });

    it("still logs and shows a download that failed", async () => {
        global.fetch = jest.fn(() => Promise.resolve({ ok: false, status: 500 })) as any;

        const view = renderViewer("models/broken.glb");

        expect(await screen.findByText(/broken\.glb \(500\)/)).toBeTruthy();
        expect(loadErrorLines()).toHaveLength(1);
        expect(loadErrorLines()[0][0]).toBe("Error loading models/broken.glb:");
        expect(sceneErrorLogged()).toBe(true);
        expect(mockedLoadFile).not.toHaveBeenCalled();

        view.unmount();
    });

    it("still logs and shows an AbortError the viewer's own cleanup did not cause", async () => {
        global.fetch = jest.fn(() =>
            Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) })
        ) as any;
        mockedLoadFile.mockRejectedValue(
            new DOMException("The loader stopped reading.", "AbortError")
        );

        const view = renderViewer("models/part.glb");

        expect(await screen.findByText(/The loader stopped reading/)).toBeTruthy();
        expect(loadErrorLines()).toHaveLength(1);
        expect(loadErrorLines()[0][0]).toBe("Error loading models/part.glb:");
        expect(sceneErrorLogged()).toBe(true);

        view.unmount();
    });
});

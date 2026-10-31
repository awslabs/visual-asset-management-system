/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The viewer appends the WebGL renderer's canvas to its scene container outside React. When every
 * file fails, the component switches to its error panel; these tests pin that the canvas does not
 * end up inside that panel (where it pushed the message out of view), that the message keeps its
 * paragraph breaks, and that the generic "supported 3D format" hint is shown only for a file
 * failure, not under a CAD setup message.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import ThreeJSViewerComponent from "./ThreeJSViewerComponent";
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
jest.mock("./MouseControls", () => ({ MouseControls: jest.fn() }));
jest.mock("./ThreeJSPanel", () => () => null);
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

const CSP_MESSAGE =
    "CAD Format Support Not Available.\n\n" +
    "CAD format viewing is blocked by the Content Security Policy. The OpenCascade loader used " +
    "for STEP, IGES and BREP files needs app.webUi.allowUnsafeEvalFeatures set to true in the " +
    "deployment configuration.\n\n" +
    "Please contact your administrator to enable CAD format support for this viewer.";

const HINT = /Please ensure the file is a supported 3D format/;

/** Every canvas the fake renderer creates, so a test can find where it ended up. */
let createdCanvases: HTMLCanvasElement[] = [];

function installFakeThree(): void {
    (window as any).THREE = {
        Scene: class {
            background: any = null;
            add = jest.fn();
        },
        Color: class {},
        PerspectiveCamera: class {
            position = { set: jest.fn() };
        },
        WebGLRenderer: class {
            domElement = document.createElement("canvas");
            setSize = jest.fn();
            setPixelRatio = jest.fn();
            dispose = jest.fn();
            constructor() {
                createdCanvases.push(this.domElement);
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

describe("ThreeJSViewerComponent error panel", () => {
    const originalFetch = global.fetch;

    beforeEach(() => {
        createdCanvases = [];
        installFakeThree();
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

    it("keeps the renderer canvas out of the panel and shows the whole CAD message", async () => {
        mockedLoadFile.mockRejectedValue(new Error(CSP_MESSAGE));

        renderViewer("models/part.step");

        const panel = await screen.findByText(/CAD Format Support Not Available/);
        // The scene was built before the load failed, so there is a canvas to misplace.
        expect(createdCanvases).toHaveLength(1);
        expect(panel.contains(createdCanvases[0])).toBe(false);
        expect(createdCanvases[0].isConnected).toBe(false);

        expect(panel).toHaveTextContent("app.webUi.allowUnsafeEvalFeatures");
        expect(panel).toHaveTextContent(
            "Please contact your administrator to enable CAD format support for this viewer."
        );
        // The message separates its paragraphs with blank lines.
        expect(panel).toHaveStyle("white-space: pre-line");
        expect(screen.queryByText(HINT)).not.toBeInTheDocument();
    });

    it("still shows the supported-format hint when a file fails to load", async () => {
        mockedLoadFile.mockRejectedValue(new Error("Failed to parse file"));

        renderViewer("models/broken.glb");

        const panel = await screen.findByText(/Failed to parse file/);
        expect(createdCanvases).toHaveLength(1);
        expect(panel.contains(createdCanvases[0])).toBe(false);
        expect(screen.getByText(HINT)).toBeInTheDocument();
    });
});

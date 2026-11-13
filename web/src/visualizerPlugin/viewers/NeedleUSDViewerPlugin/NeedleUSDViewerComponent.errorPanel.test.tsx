/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The viewer appends the WebGL renderer's canvas to its scene container outside React. When the
 * load fails, the component switches to its error panel; these tests pin that the canvas does not
 * end up inside that panel, where it pushed the message under the panel's edge and only its first
 * words showed (D176), for every load error, and that the message scrolls within the panel instead
 * of being clipped. The Three.js viewer's error panel follows the same pattern.
 *
 * The viewer runs over a stand-in USD module and three.js, as in its refusal tests.
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import NeedleUSDViewerComponent from "./NeedleUSDViewerComponent";
import {
    QUARANTINE_REFUSAL_MESSAGE,
    quarantineRefusalResponse,
} from "../../core/quarantineRefusal.testdata";

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

class FakeObject3D {
    children: any[] = [];
    userData: Record<string, any> = {};
    add(child: any) {
        this.children.push(child);
    }
    traverse(callback: (obj: any) => void) {
        callback(this);
    }
}

/** Every canvas the fake renderer creates, so a test can find where it ended up. */
let createdCanvases: HTMLCanvasElement[] = [];

const fakeThree = {
    Scene: class extends FakeObject3D {
        background: any = null;
    },
    Group: FakeObject3D,
    Color: class {},
    AmbientLight: FakeObject3D,
    DirectionalLight: class extends FakeObject3D {
        position = { set: jest.fn() };
    },
    Vector2: class {
        x = 0;
        y = 0;
    },
    Raycaster: class {},
    PerspectiveCamera: class {
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
        constructor() {
            createdCanvases.push(this.domElement);
        }
    },
};

const fakeUsdModule = {
    FS_createPath: jest.fn(),
    FS_createDataFile: jest.fn(),
    FS_analyzePath: jest.fn(() => ({ exists: false })),
    FS_readdir: jest.fn(() => []),
    FS_unlink: jest.fn(),
    FS_rmdir: jest.fn(),
};

const viewer = () => (
    <NeedleUSDViewerComponent
        assetId="asset-1"
        databaseId="db-1"
        assetKey="models/scene.usda"
        viewerMode="collapse"
        onViewerModeChange={jest.fn()}
    />
);

const finishLoading = async () => {
    for (let step = 0; step < 6; step++) {
        await act(async () => {
            await jest.advanceTimersByTimeAsync(1000);
        });
    }
};

const LOAD_ERRORS: Array<[string, () => void, RegExp | string]> = [
    [
        "the API's refusal of a quarantined asset's file",
        () => {
            global.fetch = jest.fn(() => Promise.resolve(quarantineRefusalResponse())) as any;
        },
        QUARANTINE_REFUSAL_MESSAGE,
    ],
    [
        "a download that failed otherwise",
        () => {
            global.fetch = jest.fn(() =>
                Promise.resolve({ ok: false, status: 500, json: async () => ({}) })
            ) as any;
        },
        /No files loaded successfully/,
    ],
    [
        "a USD module that failed to start",
        () => {
            (global as any).__fakeUsdBundle.getUsdModule = jest.fn(() =>
                Promise.reject(new Error("USD WASM module failed to start"))
            );
        },
        /USD WASM module failed to start/,
    ],
];

describe("NeedleUSDViewerComponent error panel", () => {
    const originalFetch = global.fetch;
    const originalSharedArrayBuffer = (global as any).SharedArrayBuffer;

    beforeEach(() => {
        jest.useFakeTimers();
        createdCanvases = [];
        (global as any).__fakeUsdBundle = {
            THREE: fakeThree,
            ThreeRenderDelegateInterface: class {},
            getUsdModule: jest.fn(() => Promise.resolve(fakeUsdModule)),
        };
        (global as any).SharedArrayBuffer = originalSharedArrayBuffer ?? ArrayBuffer;
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

    it.each(LOAD_ERRORS)(
        "keeps the renderer canvas out of the panel for %s",
        async (_name, failLoad, message) => {
            failLoad();

            render(viewer());
            await finishLoading();

            const panel = screen.getByText(message);
            // The scene was built before the load failed, so there is a canvas to misplace.
            expect(createdCanvases).toHaveLength(1);
            expect(panel.contains(createdCanvases[0])).toBe(false);
            expect(createdCanvases[0].isConnected).toBe(false);
        }
    );

    it.each(LOAD_ERRORS)(
        "lets the whole message scroll within the panel for %s",
        async (_name, failLoad, message) => {
            failLoad();

            render(viewer());
            await finishLoading();

            const panel = screen.getByText(message);
            expect(panel).toHaveStyle({ maxHeight: "100%", overflowY: "auto" });
            expect(panel).not.toHaveStyle({ position: "absolute" });
        }
    );
});

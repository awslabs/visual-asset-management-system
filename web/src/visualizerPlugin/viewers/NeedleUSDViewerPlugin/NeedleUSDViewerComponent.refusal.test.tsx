/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A USD file the API refuses with a reason of its own (a quarantined asset while
 * `app.compliance.quarantineBlocksDownload` is on) shows that reason: the API's real answer, from
 * the shared test data the backend handler tests pin (D169). The viewer used to collect the
 * refusal as a file error and then show only "No files loaded successfully" (D172). Any other
 * failure keeps the viewer's own text.
 *
 * The viewer runs over a stand-in USD module and three.js, as in its stacking tests; the refused
 * download ends the load before either draws anything.
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

const FORMAT_HINT = /Please ensure the file is a valid USD format/;

const viewer = (assetKey: string) => (
    <NeedleUSDViewerComponent
        assetId="asset-1"
        databaseId="db-1"
        assetKey={assetKey}
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

describe("NeedleUSDViewerComponent: a file the API refused", () => {
    const originalFetch = global.fetch;
    const originalSharedArrayBuffer = (global as any).SharedArrayBuffer;

    beforeEach(() => {
        jest.useFakeTimers();
        fakeUsdModule.FS_createDataFile.mockClear();
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

    it("shows the API's reason for a quarantined asset's file (D172)", async () => {
        global.fetch = jest.fn(() => Promise.resolve(quarantineRefusalResponse())) as any;

        render(viewer("models/scene.usda"));
        await finishLoading();

        expect(screen.getByText(QUARANTINE_REFUSAL_MESSAGE)).toBeInTheDocument();
        expect(screen.queryByText(/No files loaded successfully/)).not.toBeInTheDocument();
        expect(fakeUsdModule.FS_createDataFile).not.toHaveBeenCalled();
    });

    it("adds no format hint under the API's reason (D170)", async () => {
        global.fetch = jest.fn(() => Promise.resolve(quarantineRefusalResponse())) as any;

        render(viewer("models/scene.usda"));
        await finishLoading();

        expect(screen.getByText(QUARANTINE_REFUSAL_MESSAGE)).toBeInTheDocument();
        expect(screen.queryByText(FORMAT_HINT)).not.toBeInTheDocument();
    });

    it("[control] keeps its own text when the download failed otherwise", async () => {
        global.fetch = jest.fn(() =>
            Promise.resolve({ ok: false, status: 500, json: async () => ({}) })
        ) as any;

        render(viewer("models/scene.usda"));
        await finishLoading();

        expect(screen.getByText(/No files loaded successfully/)).toBeInTheDocument();
        expect(screen.getByText(FORMAT_HINT)).toBeInTheDocument();
    });
});

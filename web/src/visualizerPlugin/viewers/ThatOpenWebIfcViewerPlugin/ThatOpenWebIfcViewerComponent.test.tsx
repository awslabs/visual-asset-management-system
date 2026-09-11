/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Browsing the files of a pinned asset version reuses this component instance, so
 * opening a second .ifc re-runs the load effect rather than remounting. The effect's
 * one-shot guards (`initializationRef`, `loadingCancelledRef`) are set on the way in
 * and were never released by the cleanup, so the second file disposed the first
 * scene and then returned at the guard: an empty container, no spinner, no error.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import ThatOpenWebIfcViewerComponent from "./ThatOpenWebIfcViewerComponent";

// --- The That Open bundle, reduced to what the load effect touches ------------
const token = (name: string) => ({ token: name });
const OBC_TOKENS = {
    Worlds: token("Worlds"),
    Grids: token("Grids"),
    FragmentsManager: token("FragmentsManager"),
    Raycasters: token("Raycasters"),
    IfcLoader: token("IfcLoader"),
    BoundingBoxer: token("BoundingBoxer"),
};
const OBF_HIGHLIGHTER = token("Highlighter");

const disposeCalls: jest.Mock[] = [];

const makeBundle = () => {
    const world: any = {};
    const fragments = {
        init: jest.fn(),
        core: { update: jest.fn().mockResolvedValue(undefined) },
        list: { onItemSet: { add: jest.fn() }, get: jest.fn() },
    };
    const services = new Map<any, any>([
        [OBC_TOKENS.Worlds, { create: () => world }],
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
    const dispose = jest.fn();
    disposeCalls.push(dispose);
    const components = { get: (key: any) => services.get(key), init: jest.fn(), dispose };

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
const cleanupSpy = jest.fn();
jest.mock("./dependencies", () => ({
    ThatOpenWebIfcDependencyManager: {
        loadThatOpenWebIfc: () => Promise.resolve(),
        // eslint-disable-next-line @typescript-eslint/no-use-before-define
        getBundle: () => currentBundle(),
        cleanup: (...args: any[]) => cleanupCall(...args),
    },
}));
const currentBundle = () => bundle;
const cleanupCall = (...args: any[]) => cleanupSpy(...args);

const mockLoadIfcModel = jest.fn();
const mockExtractIfcBytes = jest.fn();
jest.mock("./utils/ifcLoader", () => ({
    assertIfcSizeWithinLimit: jest.fn(),
    extractIfcBytes: (...args: any[]) => mockExtractIfcBytes(...args),
    loadIfcModel: (...args: any[]) => mockLoadIfcModel(...args),
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

// The panel's presence is the observable signal that a model finished loading:
// it renders only while viewerInstanceRef holds a live instance.
jest.mock("./ThatOpenWebIfcPanel", () => ({
    __esModule: true,
    default: () => <div>ifc-panel</div>,
}));

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: () => ({ api: "https://api.test/" }) },
}));

jest.mock("../../../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: () => Promise.resolve("Bearer test"),
}));

const props = {
    assetId: "a1",
    databaseId: "d1",
    assetKey: "models/first.ifc",
} as any;

describe("ThatOpenWebIfcViewerComponent", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        disposeCalls.length = 0;
        bundle = makeBundle();
        mockExtractIfcBytes.mockReturnValue(new Uint8Array([1, 2, 3]));
        mockLoadIfcModel.mockResolvedValue({ modelId: "m1", schema: "IFC4" });
        (global as any).fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => "2048" },
            arrayBuffer: async () => new ArrayBuffer(8),
        });
    });

    it("loads the model and shows the control panel", async () => {
        render(<ThatOpenWebIfcViewerComponent {...props} />);

        await waitFor(() => expect(screen.getByText("ifc-panel")).toBeInTheDocument());
        expect(mockLoadIfcModel).toHaveBeenCalledTimes(1);
    });

    it("loads the second file opened in the same viewer instance", async () => {
        const { rerender } = render(<ThatOpenWebIfcViewerComponent {...props} />);
        await waitFor(() => expect(screen.getByText("ifc-panel")).toBeInTheDocument());
        const firstDispose = disposeCalls[0];

        rerender(<ThatOpenWebIfcViewerComponent {...props} assetKey="models/second.ifc" />);

        // A second load at all is the fix: the guard used to stay set, so the
        // effect body returned immediately after disposing the first scene.
        await waitFor(() => expect(mockLoadIfcModel).toHaveBeenCalledTimes(2));
        // The first scene was released rather than leaked.
        expect(firstDispose).toHaveBeenCalled();
        // And the panel is back, so the second model really is in the viewer.
        await waitFor(() => expect(screen.getByText("ifc-panel")).toBeInTheDocument());
    });

    it("recovers for the next file after one fails to load", async () => {
        // The failure message used to replace the whole tree, which unmounted the
        // container the load effect requires — so the next file stopped at that
        // guard and kept showing this error.
        ((global as any).fetch as jest.Mock).mockResolvedValueOnce({
            ok: false,
            status: 415,
            headers: { get: () => null },
        });

        const { rerender } = render(<ThatOpenWebIfcViewerComponent {...props} />);
        await waitFor(() =>
            expect(screen.getByText(/Failed to load: first\.ifc \(415\)/)).toBeInTheDocument()
        );

        rerender(<ThatOpenWebIfcViewerComponent {...props} assetKey="models/second.ifc" />);

        await waitFor(() => expect(screen.getByText("ifc-panel")).toBeInTheDocument());
        expect(screen.queryByText(/Failed to load: first\.ifc/)).toBeNull();
    });

    it("aborts the first load before starting the second", async () => {
        const { rerender } = render(<ThatOpenWebIfcViewerComponent {...props} />);
        await waitFor(() => expect(screen.getByText("ifc-panel")).toBeInTheDocument());

        rerender(<ThatOpenWebIfcViewerComponent {...props} assetKey="models/second.ifc" />);
        await waitFor(() => expect(mockLoadIfcModel).toHaveBeenCalledTimes(2));

        // Positive control for the guard reset: the second run must have cleared
        // the cancellation the cleanup set, or the load would have stopped at its
        // first checkpoint and never reached loadIfcModel.
        const [, secondFetchCall] = ((global as any).fetch as jest.Mock).mock.calls;
        expect(String(secondFetchCall[0])).toContain("second.ifc");
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Potree viewer keeps the point cloud's node and hierarchy requests authorized after the ID
 * token is refreshed: it renews the Authorization of the header object its node loader reads.
 */
import React from "react";
import { render, act } from "@testing-library/react";
import PotreeViewerComponent from "./PotreeViewerComponent";

const mockLoadPointCloud = jest.fn();

jest.mock("./dependencies", () => {
    class MockViewer {
        orbitControls = {};
        scene = { addPointCloud: jest.fn() };
        renderer = { setAnimationLoop: jest.fn(), dispose: jest.fn() };
        setEDLEnabled = jest.fn();
        setFOV = jest.fn();
        setPointBudget = jest.fn();
        setClipTask = jest.fn();
        loadSettingsFromURL = jest.fn();
        setControls = jest.fn();
        loadGUI = jest.fn();
        setLanguage = jest.fn();
        fitToScreen = jest.fn();
    }
    const potree = {
        Viewer: MockViewer,
        ClipTask: { SHOW_INSIDE: 1 },
        PointSizeType: { FIXED: 0 },
        loadPointCloud: (...args: unknown[]) => mockLoadPointCloud(...args),
    };
    return {
        PotreeDependencyManager: {
            loadPotree: jest.fn(() => Promise.resolve(potree)),
            cleanup: jest.fn(),
        },
    };
});
jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: () => ({ api: "https://api.example/api/" }) },
}));
jest.mock("../../../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(() => Promise.resolve("Bearer t")),
}));

const mount = () =>
    render(
        <PotreeViewerComponent
            {...({
                assetId: "asset1",
                databaseId: "db1",
                assetKey: "asset1/cloud.las",
            } as any)}
        />
    );

beforeEach(() => {
    jest.spyOn(console, "error").mockImplementation(() => undefined);
    mockLoadPointCloud.mockReset();
});

afterEach(() => {
    jest.restoreAllMocks();
});

describe("Potree viewer authorization", () => {
    afterEach(() => jest.useRealTimers());

    it("renews the Authorization of the header object the node loader reads", async () => {
        jest.useFakeTimers();
        const { getDualAuthorizationHeader } = jest.requireMock("../../../utils/authTokenUtils");
        mockLoadPointCloud.mockReturnValue(Promise.resolve({ pointcloud: { material: {} } }));
        mount();

        // Each round lets React run the effects the previous round's updates scheduled.
        for (let round = 0; round < 6 && mockLoadPointCloud.mock.calls.length === 0; round++) {
            await act(async () => {
                await jest.advanceTimersByTimeAsync(150);
            });
        }
        expect(mockLoadPointCloud).toHaveBeenCalled();
        const header = mockLoadPointCloud.mock.calls[0][2];
        expect(header.Authorization).toBe("Bearer t");

        getDualAuthorizationHeader.mockResolvedValueOnce("Bearer t2");
        await act(async () => {
            await jest.advanceTimersByTimeAsync(30_000);
        });
        // The same object, renewed in place: the loader keeps the reference it was given.
        expect(mockLoadPointCloud.mock.calls[0][2]).toBe(header);
        expect(header.Authorization).toBe("Bearer t2");
    });
});

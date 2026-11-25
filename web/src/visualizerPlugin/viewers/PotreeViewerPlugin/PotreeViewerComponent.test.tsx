/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the Potree viewer shows when the point cloud preview fails to load. Potree's loader rejects
 * without the HTTP status, so the viewer probes the preview URL and names the cause; only a missing
 * preview (404) carries the hint to run the Potree pipeline, which cannot help with a block, a
 * permission denial or a throttle.
 */
import React from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
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

// The file is named by its full key, as the host gives it (#1855)
const PREVIEW_URL =
    "https://api.example/api/database/db1/assets/asset1/auxiliaryPreviewAssets/stream/asset1/cloud.las/preview/PotreeViewer/metadata.json";
const PIPELINE_HINT = /Please run the Point Cloud Potree Viewer Pipeline/;
const MISSING = "Potree Viewer Auxiliary Preview files not currently available";

let fetchMock: jest.Mock;

const respond = (status: number, body?: unknown) =>
    fetchMock.mockResolvedValue({
        ok: status >= 200 && status < 300,
        status,
        json: () =>
            body === undefined ? Promise.reject(new SyntaxError("x")) : Promise.resolve(body),
    });

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
    mockLoadPointCloud.mockImplementation(() => Promise.reject(new Error("load failed")));
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
});

afterEach(() => {
    delete (global as any).fetch;
    jest.restoreAllMocks();
});

describe("Potree viewer load failure", () => {
    it("probes the preview URL Potree failed on, with the viewer's authorization", async () => {
        respond(404, { message: "File not found" });
        mount();
        await waitFor(() => expect(fetchMock).toHaveBeenCalled());
        expect(mockLoadPointCloud).toHaveBeenCalledWith(PREVIEW_URL, "Point Cloud", {
            Authorization: "Bearer t",
        });
        expect(fetchMock).toHaveBeenCalledWith(PREVIEW_URL, {
            headers: { Authorization: "Bearer t" },
        });
    });

    it("encodes each segment of the file key in the stream URL, keeping the slashes", async () => {
        respond(404, { message: "File not found" });
        render(
            <PotreeViewerComponent
                {...({
                    assetId: "asset1",
                    databaseId: "db1",
                    assetKey: "asset1/scans #2/50% cloud?.las",
                } as any)}
            />
        );
        await waitFor(() => expect(mockLoadPointCloud).toHaveBeenCalled());
        expect(mockLoadPointCloud.mock.calls[0][0]).toBe(
            "https://api.example/api/database/db1/assets/asset1/auxiliaryPreviewAssets/stream/" +
                "asset1/scans%20%232/50%25%20cloud%3F.las/preview/PotreeViewer/metadata.json"
        );
    });

    it("keeps the leading slash of a path in the asset from search results (D1)", async () => {
        respond(404, { message: "File not found" });
        render(
            <PotreeViewerComponent
                {...({
                    assetId: "asset1",
                    databaseId: "db1",
                    assetKey: "/asset1/cloud.las",
                } as any)}
            />
        );
        await waitFor(() => expect(mockLoadPointCloud).toHaveBeenCalled());
        expect(mockLoadPointCloud.mock.calls[0][0]).toBe(
            "https://api.example/api/database/db1/assets/asset1/auxiliaryPreviewAssets/stream/" +
                "/asset1/cloud.las/preview/PotreeViewer/metadata.json"
        );
    });

    it.each([
        ["a bucket with a base prefix", "vams/asset1/scans/cloud.las"],
        ["an asset on an existing folder", "custom/path/cloud.las"],
        ["a subfolder named like the asset", "asset1/asset1/cloud.las"],
    ])("sends the full key of %s as the host gives it", async (_case, assetKey) => {
        respond(404, { message: "File not found" });
        render(
            <PotreeViewerComponent
                {...({ assetId: "asset1", databaseId: "db1", assetKey } as any)}
            />
        );
        await waitFor(() => expect(mockLoadPointCloud).toHaveBeenCalled());
        expect(mockLoadPointCloud.mock.calls[0][0]).toBe(
            "https://api.example/api/database/db1/assets/asset1/auxiliaryPreviewAssets/stream/" +
                `${assetKey}/preview/PotreeViewer/metadata.json`
        );
    });

    it("keeps the missing-preview message and the pipeline hint for a 404", async () => {
        respond(404, { message: "File not found" });
        mount();
        expect(await screen.findByText(MISSING)).toBeInTheDocument();
        expect(screen.getByText(PIPELINE_HINT)).toBeInTheDocument();
    });

    it("names a WAF block with its request ID, without the pipeline hint", async () => {
        respond(403, {
            message: "Request blocked by the deployment's AWS WAF web ACL",
            blockedBy: "waf",
            requestId: "r-1",
        });
        mount();
        expect(
            await screen.findByText(
                "Unable to load the point cloud preview: Request blocked by the deployment's AWS WAF web ACL (request ID r-1)."
            )
        ).toBeInTheDocument();
        expect(screen.queryByText(PIPELINE_HINT)).not.toBeInTheDocument();
        expect(screen.queryByText(MISSING)).not.toBeInTheDocument();
    });

    it("names a permission denial, without the pipeline hint", async () => {
        respond(403, { message: "Not Authorized" });
        mount();
        expect(
            await screen.findByText(
                "Unable to load the point cloud preview: you don't have permission to view it."
            )
        ).toBeInTheDocument();
        expect(screen.queryByText(PIPELINE_HINT)).not.toBeInTheDocument();
    });

    it("names a throttle, without the pipeline hint", async () => {
        respond(429, { message: "Rate limit exceeded. Please retry shortly." });
        mount();
        expect(
            await screen.findByText(
                "Unable to load the point cloud preview: too many requests. Try again shortly."
            )
        ).toBeInTheDocument();
        expect(screen.queryByText(PIPELINE_HINT)).not.toBeInTheDocument();
    });

    it("names an unreachable preview, without the pipeline hint", async () => {
        fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
        mount();
        expect(
            await screen.findByText(/^Unable to reach the point cloud preview\./)
        ).toBeInTheDocument();
        expect(screen.queryByText(PIPELINE_HINT)).not.toBeInTheDocument();
    });

    it("shows no failure while the point cloud loads", async () => {
        // Control for the probes above: a successful load makes no probe and shows no message.
        mockLoadPointCloud.mockReturnValue(Promise.resolve({ pointcloud: { material: {} } }));
        mount();
        await waitFor(() => expect(mockLoadPointCloud).toHaveBeenCalled());
        await new Promise((resolve) => setTimeout(resolve, 50));
        expect(fetchMock).not.toHaveBeenCalled();
        expect(screen.queryByText(MISSING)).not.toBeInTheDocument();
        expect(screen.queryByText(/^Unable to/)).not.toBeInTheDocument();
    });
});

describe("Potree viewer messages", () => {
    it("draws the failure message on a theme panel", async () => {
        respond(404, { message: "File not found" });
        mount();
        const message = await screen.findByText(MISSING);
        expect(message.style.background).toBe("var(--vams-bg-primary)");
        expect(message.style.color).toBe("var(--vams-color-error)");
        expect((screen.getByText(PIPELINE_HINT) as HTMLElement).style.color).toBe(
            "var(--vams-color-error)"
        );
    });

    it("draws the loading message on a theme panel", () => {
        const { PotreeDependencyManager } = jest.requireMock("./dependencies");
        PotreeDependencyManager.loadPotree.mockReturnValueOnce(new Promise(() => undefined));
        mount();
        const message = screen.getByText("Loading Potree viewer...");
        expect(message.style.background).toBe("var(--vams-bg-primary)");
        expect(message.style.color).toBe("var(--vams-text-secondary)");
    });
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

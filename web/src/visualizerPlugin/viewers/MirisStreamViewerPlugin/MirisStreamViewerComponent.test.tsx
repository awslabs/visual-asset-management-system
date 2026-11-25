/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import React from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import "@testing-library/jest-dom";
import MirisStreamViewerComponent from "./MirisStreamViewerComponent";
import { MirisStreamDependencyManager } from "./dependencies";
import viewerConfig from "../../config/viewerConfig.json";
import addonViewerConfig from "../../config/addonViewerConfig.json";
import {
    QUARANTINE_REFUSAL_MESSAGE,
    QUARANTINE_REFUSAL_STATUS,
} from "../../core/quarantineRefusal.testdata";

const VALID_UUID = "063393b4-4b0d-4196-a6d4-ebcde70c56c2";

// jsdom cannot load the SDK (WASM, WebGPU), so the custom elements stay undefined and render as
// plain elements whose attributes and events the tests drive directly.
jest.mock("./dependencies", () => ({
    MirisStreamDependencyManager: {
        loadMirisStream: jest.fn().mockResolvedValue(undefined),
        isDeviceSupported: jest.fn().mockResolvedValue(true),
        abandonPendingLoad: jest.fn(),
        cleanup: jest.fn(),
    },
}));
const mockLoadMirisStream = MirisStreamDependencyManager.loadMirisStream as jest.Mock;
const mockAbandonPendingLoad = MirisStreamDependencyManager.abandonPendingLoad as jest.Mock;
const mockIsDeviceSupported = MirisStreamDependencyManager.isDeviceSupported as jest.Mock;

const mockGetMirisStreamStatus = jest.fn();
const mockFetchFileInfo = jest.fn();
const mockDownloadAsset = jest.fn();
const mockMintMirisViewerKey = jest.fn();
const mockStartMirisUpload = jest.fn();
const mockFindRunningMirisUpload = jest.fn();
const mockGetMirisUploadRun = jest.fn();
jest.mock("../../../services/APIService", () => ({
    fetchFileInfo: (...args: unknown[]) => mockFetchFileInfo(...args),
    downloadAsset: (...args: unknown[]) => mockDownloadAsset(...args),
}));
jest.mock("../../../services/addons/mirisAPIService", () => ({
    ...jest.requireActual("../../../services/addons/mirisAPIService"),
    getMirisStreamStatus: (...args: unknown[]) => mockGetMirisStreamStatus(...args),
    mintMirisViewerKey: (...args: unknown[]) => mockMintMirisViewerKey(...args),
    startMirisUpload: (...args: unknown[]) => mockStartMirisUpload(...args),
    findRunningMirisUpload: (...args: unknown[]) => mockFindRunningMirisUpload(...args),
    getMirisUploadRun: (...args: unknown[]) => mockGetMirisUploadRun(...args),
}));

const EXECUTE_ROUTE = "/workflows/{workflowDatabaseId}/{workflowId}/execute";
const EXECUTION_DETAILS_ROUTE = "/workflows/executions/{executionId}/details";
const ASSET_EXECUTIONS_ROUTE = "/database/{databaseId}/assets/{assetId}/workflows/executions";
const mockCan = jest.fn();
jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: false,
        can: (...args: unknown[]) => mockCan(...args),
    }),
}));
const allowRoutes = (...allowed: string[]) =>
    mockCan.mockImplementation((_method: string, path: string) => allowed.includes(path));

const mockAppCacheGet = jest.fn();
const mockAppCacheSet = jest.fn();
jest.mock("../../../services/appCache", () => ({
    appCache: {
        getItem: (key: string) => mockAppCacheGet(key),
        setItem: (...args: unknown[]) => mockAppCacheSet(...args),
    },
}));
const withUploadPipeline = () =>
    mockAppCacheGet.mockReturnValue({ featuresEnabled: ["MIRIS_ADDON", "MIRIS_UPLOAD"] });

const status = (overrides: Record<string, unknown> = {}) => [
    true,
    {
        filePath: "/folder/scene.usd",
        mirisAssetUuid: VALID_UUID,
        sourceVersionId: "v1",
        uploadedAt: "2026-10-14T12:00:00Z",
        displayName: "Scene stream",
        state: "streamable",
        ...overrides,
    },
];
const NOT_STREAMED = status({
    mirisAssetUuid: null,
    sourceVersionId: null,
    uploadedAt: null,
    displayName: null,
    state: "notStreamed",
});

const VIEWER_KEY = "minted-viewer-key";
const mintedKey = (overrides: Record<string, unknown> = {}) => [
    true,
    {
        filePath: "/asset-1/folder/scene.usd",
        mirisAssetUuid: VALID_UUID,
        viewerKey: VIEWER_KEY,
        expiresAt: "2026-10-15T19:04:28Z",
        ...overrides,
    },
];

beforeEach(() => {
    jest.clearAllMocks();
    mockAppCacheGet.mockReturnValue({ featuresEnabled: [] });
    mockGetMirisStreamStatus.mockResolvedValue(status());
    mockMintMirisViewerKey.mockResolvedValue(mintedKey());
    mockCan.mockReturnValue(false);
    mockFindRunningMirisUpload.mockResolvedValue([true, null]);
    mockGetMirisUploadRun.mockResolvedValue([true, { finished: false }]);
    mockFetchFileInfo.mockResolvedValue([
        true,
        { versions: [{ versionId: "v1", isLatest: true }] },
    ]);
});

afterEach(() => {
    jest.useRealTimers();
});

const baseProps = {
    assetId: "asset-1",
    databaseId: "db-1",
    assetKey: "asset-1/folder/scene.usd",
    versionId: "v1",
    viewerMode: "wide",
    onViewerModeChange: jest.fn(),
};
const renderViewer = (props: Record<string, unknown> = {}) =>
    render(<MirisStreamViewerComponent {...baseProps} {...props} />);

const STALE_TEXT = /has changed since it was streamed/i;
const NOT_STREAMED_TEXT = /not yet streamed with miris/i;
const RUNNING_TEXT = /a miris upload for this asset is running/i;
const STATUS_ERROR_TEXT = /could not load the miris stream status/i;
const KEY_ERROR_TEXT = /Could not get access to stream/i;
const NOT_BOUND_TEXT = /not linked to this VAMS asset.*vams-asset-asset-1 and vams-database-db-1/i;
const PERMISSION_TEXT = "You do not have permission to stream this asset.";
const BLOCKED_MESSAGE = "Request blocked (request ID abc-123)";
const BLOCKED = [false, BLOCKED_MESSAGE, undefined, "requestBlocked"];

const viewerEntry = (id: string) =>
    [
        ...(viewerConfig as { viewers: Array<Record<string, any>> }).viewers,
        ...(addonViewerConfig as { viewers: Array<Record<string, any>> }).viewers,
    ].find((viewer) => viewer.id === id) as Record<string, any>;

const retryButton = () => screen.getByRole("button", { name: "Retry" });
const clickRetry = () => act(() => retryButton().click());
const streamButton = () => screen.queryByRole("button", { name: "Stream with Miris" });
const clickStreamButton = () =>
    act(() => screen.getByRole("button", { name: "Stream with Miris" }).click());
const waitForText = (text: string | RegExp) =>
    waitFor(() => expect(screen.getByText(text)).toBeInTheDocument());
const waitForGuidance = () => waitForText(NOT_STREAMED_TEXT);
const advance = (ms: number) =>
    act(async () => {
        jest.advanceTimersByTime(ms);
    });

const sceneElement = () => document.querySelector("miris-scene");
const streamElement = () => document.querySelector("miris-stream");
const waitForScene = async (): Promise<Element> => {
    await waitFor(() => expect(sceneElement()).not.toBeNull());
    return sceneElement() as Element;
};

const deferred = <T,>() => {
    let resolve: (value: T) => void = () => undefined;
    const promise = new Promise<T>((settle) => {
        resolve = settle;
    });
    return { promise, resolve };
};

describe("MirisStreamViewerComponent", () => {
    describe("streaming", () => {
        it("asks for the file's status and key, then streams exactly the returned UUID", async () => {
            renderViewer();
            const scene = await waitForScene();
            const request = {
                databaseId: "db-1",
                assetId: "asset-1",
                filePath: "/asset-1/folder/scene.usd",
            };
            expect(mockGetMirisStreamStatus).toHaveBeenCalledWith(request);
            expect(mockMintMirisViewerKey).toHaveBeenCalledTimes(1);
            expect(mockMintMirisViewerKey).toHaveBeenCalledWith(request);
            expect(scene).toHaveAttribute("viewer-key", VIEWER_KEY);
            expect(scene).toHaveAttribute("camera-fit", "contain");
            expect(scene.querySelectorAll("miris-camera")).toHaveLength(1);
            expect(scene.querySelectorAll("miris-stream")).toHaveLength(1);
            expect(streamElement()).toHaveAttribute("uuid", VALID_UUID);
            expect(screen.getByText("Scene stream")).toBeInTheDocument();
            expect(mockDownloadAsset).not.toHaveBeenCalled();
        });

        it("orbits the camera and leaves the asset fixed", async () => {
            renderViewer();
            const camera = (await waitForScene()).querySelector("miris-camera");
            // The elements treat any present `controls` attribute as enabled; stream controls
            // would rotate the asset on the same drag that orbits the camera.
            expect(camera).toHaveAttribute("controls");
            expect(camera?.getAttribute("controls")).not.toBe("false");
            expect(streamElement()).not.toHaveAttribute("controls");
        });

        it("registers the SDK elements before rendering the scene", async () => {
            const load = deferred<void>();
            mockLoadMirisStream.mockReturnValueOnce(load.promise);
            renderViewer();
            await waitFor(() => expect(mockLoadMirisStream).toHaveBeenCalledTimes(1));
            expect(sceneElement()).toBeNull();
            await act(async () => load.resolve());
            await waitForScene();
        });

        it.each([
            ["the SDK fails to load", mockLoadMirisStream],
            ["the status request throws", mockGetMirisStreamStatus],
        ])("shows the load error when %s", async (_label, mock) => {
            mock.mockRejectedValueOnce(new Error("network"));
            const consoleError = jest.spyOn(console, "error").mockImplementation(() => undefined);
            renderViewer();
            await waitForText(/miris viewer failed to load/i);
            expect(sceneElement()).toBeNull();
            consoleError.mockRestore();
        });

        it("streams when Miris could not be queried (state unknown)", async () => {
            mockGetMirisStreamStatus.mockResolvedValue(status({ state: "unknown" }));
            renderViewer();
            await waitForScene();
            expect(streamElement()).toHaveAttribute("uuid", VALID_UUID);
            expect(screen.queryByText(/preparing this asset/i)).not.toBeInTheDocument();
        });

        it("removes the scene on unmount and leaves no timers", async () => {
            jest.useFakeTimers();
            const { unmount } = renderViewer();
            await waitForScene();
            await act(async () => unmount());
            expect(sceneElement()).toBeNull();
            expect(jest.getTimerCount()).toBe(0);
        });
    });

    describe("load events", () => {
        it("times out when the stream sends no data and mounts a new scene on Retry", async () => {
            jest.useFakeTimers();
            renderViewer();
            const first = await waitForScene();
            await advance(60_000);
            expect(screen.getByText(/timed out waiting for miris/i)).toBeInTheDocument();
            expect(screen.getByText(new RegExp(VALID_UUID))).toBeInTheDocument();
            expect(sceneElement()).toBeNull();

            clickRetry();
            expect(await waitForScene()).not.toBe(first);
            expect(screen.queryByText(/timed out waiting for miris/i)).not.toBeInTheDocument();
        });

        it("clears the timeout when the stream reports streamloaded", async () => {
            jest.useFakeTimers();
            renderViewer();
            await waitForScene();
            act(() => {
                streamElement()?.dispatchEvent(new CustomEvent("streamloaded"));
            });
            await advance(120_000);
            expect(screen.queryByText(/timed out waiting for miris/i)).not.toBeInTheDocument();
            expect(sceneElement()).not.toBeNull();
        });

        it("pauses the timeout while the tab is hidden and restarts it when visible", async () => {
            jest.useFakeTimers();
            let visibility: DocumentVisibilityState = "hidden";
            const visibilityState = jest
                .spyOn(document, "visibilityState", "get")
                .mockImplementation(() => visibility);
            const setVisibility = (state: DocumentVisibilityState) =>
                act(() => {
                    visibility = state;
                    document.dispatchEvent(new Event("visibilitychange"));
                });
            renderViewer();
            await waitForScene();
            await advance(120_000);
            setVisibility("visible");
            await advance(59_000);
            setVisibility("hidden");
            setVisibility("visible");
            await advance(59_000);
            expect(screen.queryByText(/timed out waiting for miris/i)).not.toBeInTheDocument();
            await advance(1_000);
            expect(screen.getByText(/timed out waiting for miris/i)).toBeInTheDocument();
            visibilityState.mockRestore();
        });

        it("fails before asking for a viewer key when the device has no WebGPU", async () => {
            mockIsDeviceSupported.mockResolvedValueOnce(false);
            renderViewer();
            await waitForText(/needs WebGPU/i);
            expect(
                screen.getByText(/Chrome or Edge 113 or later, or Safari 26/)
            ).toBeInTheDocument();
            expect(mockMintMirisViewerKey).not.toHaveBeenCalled();
            expect(mockLoadMirisStream).not.toHaveBeenCalled();
            expect(sceneElement()).toBeNull();
        });
    });

    describe("SDK load", () => {
        const neverSettles = () =>
            mockLoadMirisStream.mockReturnValueOnce(new Promise<void>(() => undefined));
        const waitForSdkLoad = (calls: number) =>
            waitFor(() => expect(mockLoadMirisStream).toHaveBeenCalledTimes(calls));

        it("shows the load error and mounts no scene when the SDK load never settles", async () => {
            jest.useFakeTimers();
            neverSettles();
            renderViewer();
            await waitForSdkLoad(1);
            expect(screen.getByText("Loading…")).toBeInTheDocument();
            await advance(60_000);
            expect(screen.getByText(/miris viewer failed to load/i)).toBeInTheDocument();
            expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
            expect(sceneElement()).toBeNull();
            expect(mockAbandonPendingLoad).toHaveBeenCalledTimes(1);
        });

        it("abandons nothing when unmounted during the wait", async () => {
            jest.useFakeTimers();
            neverSettles();
            const { unmount } = renderViewer();
            await waitForSdkLoad(1);
            unmount();
            await advance(120_000);
            expect(mockAbandonPendingLoad).not.toHaveBeenCalled();
            expect(sceneElement()).toBeNull();
        });

        it("ignores the timeout of an open the file change replaced", async () => {
            jest.useFakeTimers();
            neverSettles();
            const { rerender } = renderViewer();
            await waitForSdkLoad(1);
            rerender(<MirisStreamViewerComponent {...baseProps} assetKey="asset-1/other.usd" />);
            await waitForScene();
            await advance(120_000);
            expect(screen.queryByText(/miris viewer failed to load/i)).not.toBeInTheDocument();
            expect(mockAbandonPendingLoad).not.toHaveBeenCalled();
        });

        it("calls into the loader again on a later open after a timed-out load", async () => {
            jest.useFakeTimers();
            neverSettles();
            const first = renderViewer();
            await waitForSdkLoad(1);
            await advance(60_000);
            first.unmount();
            expect(mockAbandonPendingLoad).toHaveBeenCalledTimes(1);

            renderViewer();
            await waitForScene();
            expect(mockLoadMirisStream).toHaveBeenCalledTimes(2);
        });
    });

    describe("file cleared", () => {
        it.each([
            ["the stream", status(), /Scene stream/],
            ["a status error", [false, "Not Authorized"], STATUS_ERROR_TEXT],
            ["the not-streamed guidance", NOT_STREAMED, NOT_STREAMED_TEXT],
            ["the processing overlay", status({ state: "preview" }), /preparing this asset/i],
        ])("removes %s when the asset key becomes empty", async (_label, result, text) => {
            mockGetMirisStreamStatus.mockResolvedValue(result);
            const { rerender } = renderViewer();
            await waitForText(text);
            rerender(<MirisStreamViewerComponent {...baseProps} assetKey="" />);
            await waitFor(() => expect(screen.queryByText(text)).not.toBeInTheDocument());
            expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
            expect(sceneElement()).toBeNull();
        });
    });

    describe("not streamed", () => {
        it("shows the guidance and asks for no key and no SDK", async () => {
            mockGetMirisStreamStatus.mockResolvedValue(NOT_STREAMED);
            renderViewer();
            await waitForGuidance();
            expect(screen.getByText(/has not been uploaded to miris/i)).toBeInTheDocument();
            expect(screen.getByText("Miris Spatial Streaming Upload")).toBeInTheDocument();
            expect(screen.queryByRole("button")).not.toBeInTheDocument();
            expect(mockMintMirisViewerKey).not.toHaveBeenCalled();
            expect(mockLoadMirisStream).not.toHaveBeenCalled();
            expect(sceneElement()).toBeNull();
        });

        it("is not preceded by a registry-driven SDK load", () => {
            // With a dependencyManagerMethod the registry would load the SDK, unbounded, even
            // for a file that is not streamed.
            const entry = viewerEntry("miris-stream-viewer");
            expect(entry.dependencyManagerMethod).toBeUndefined();
            expect(entry.dependencyCleanupMethod).toBe("cleanup");
        });
    });

    describe("stream with miris", () => {
        beforeEach(() => {
            withUploadPipeline();
            mockGetMirisStreamStatus.mockResolvedValue(NOT_STREAMED);
            allowRoutes(EXECUTE_ROUTE, EXECUTION_DETAILS_ROUTE);
            mockStartMirisUpload.mockResolvedValue([true, { executionId: "exec-1" }]);
        });

        it("offers the button to a permitted user and starts the upload by the file's asset-relative path", async () => {
            renderViewer();
            await waitForGuidance();
            expect(mockCan).toHaveBeenCalledWith("POST", EXECUTE_ROUTE);
            expect(screen.getByText("Automation")).toBeInTheDocument();
            clickStreamButton();
            await waitForText(RUNNING_TEXT);
            expect(mockStartMirisUpload).toHaveBeenCalledTimes(1);
            expect(mockStartMirisUpload).toHaveBeenCalledWith({
                databaseId: "db-1",
                assetId: "asset-1",
                filePath: "/folder/scene.usd",
            });
            expect(streamButton()).not.toBeInTheDocument();
            expect(screen.getByText(/this can take a few hours/i)).toBeInTheDocument();
            expect(screen.getByRole("link", { name: /view execution/i })).toHaveAttribute(
                "href",
                "#/executions/exec-1"
            );
        });

        it.each([
            [
                "the deployment lacks the upload pipeline",
                () => mockAppCacheGet.mockReturnValue({ featuresEnabled: ["MIRIS_ADDON"] }),
            ],
            ["secure-config carries no feature list", () => mockAppCacheGet.mockReturnValue({})],
            ["the user may not execute workflows", () => allowRoutes(EXECUTION_DETAILS_ROUTE)],
            [
                "the upload workflow does not accept the file type",
                () =>
                    mockGetMirisStreamStatus.mockResolvedValue(
                        status({ mirisAssetUuid: null, state: "notStreamed", filePath: "/a.glb" })
                    ),
            ],
        ])("hides the button when %s", async (_label, arrange) => {
            arrange();
            renderViewer();
            await waitForGuidance();
            expect(streamButton()).not.toBeInTheDocument();
            expect(screen.getByText("Automation")).toBeInTheDocument();
        });

        it("disables the button while the request is in flight", async () => {
            const start = deferred<unknown>();
            mockStartMirisUpload.mockReturnValue(start.promise);
            renderViewer();
            await waitForGuidance();
            clickStreamButton();
            await waitFor(() => expect(streamButton()).toHaveAttribute("aria-disabled", "true"));
            clickStreamButton();
            expect(mockStartMirisUpload).toHaveBeenCalledTimes(1);
            await act(async () => start.resolve([true, { executionId: "exec-1" }]));
            await waitForText(RUNNING_TEXT);
        });

        it("links no execution for a user who may not read execution details", async () => {
            allowRoutes(EXECUTE_ROUTE);
            renderViewer();
            await waitForGuidance();
            clickStreamButton();
            await waitForText(RUNNING_TEXT);
            expect(screen.queryByRole("link", { name: /view execution/i })).not.toBeInTheDocument();
        });

        it("reads the status again when the response names no run", async () => {
            mockStartMirisUpload.mockResolvedValue([true, {}]);
            renderViewer();
            await waitForGuidance();
            clickStreamButton();
            await waitFor(() => expect(mockGetMirisStreamStatus).toHaveBeenCalledTimes(2));
            await waitFor(() => expect(streamButton()).toBeInTheDocument());
        });

        const listedRun = (executionStatus: string, executionError = "") => ({
            workflowExecutionId: "exec-1",
            workflowId: "miris-upload",
            workflowDatabaseId: "GLOBAL",
            executionStatus,
            executionError,
            executionStopDate: executionStatus === "RUNNING" ? "" : "2026-11-19T10:00:00Z",
        });

        const startRun = async () => {
            renderViewer();
            await waitForGuidance();
            clickStreamButton();
            await waitForText(RUNNING_TEXT);
        };

        it.each<[string, string[], number, () => unknown, () => unknown]>([
            [
                "its execution details",
                [EXECUTE_ROUTE, EXECUTION_DETAILS_ROUTE, ASSET_EXECUTIONS_ROUTE],
                30_000,
                () =>
                    mockGetMirisUploadRun.mockResolvedValue([
                        true,
                        { finished: true, status: "SUCCEEDED", failure: null },
                    ]),
                () => undefined,
            ],
            [
                "the asset's execution list",
                [EXECUTE_ROUTE, ASSET_EXECUTIONS_ROUTE],
                30_000,
                () => mockFindRunningMirisUpload.mockResolvedValue([true, listedRun("SUCCEEDED")]),
                // Nothing runs before the button starts exec-1.
                () =>
                    mockFindRunningMirisUpload
                        .mockResolvedValueOnce([true, null])
                        .mockResolvedValue([true, { workflowExecutionId: "exec-1" }]),
            ],
            ["the file's stream status", [EXECUTE_ROUTE], 90_000, () => undefined, () => undefined],
        ])(
            "follows the run through %s and reads the status when it succeeds",
            async (_label, routes, interval, finish, arrange) => {
                jest.useFakeTimers();
                allowRoutes(...routes);
                arrange();
                await startRun();
                await advance(interval);
                expect(screen.getByText(RUNNING_TEXT)).toBeInTheDocument();

                finish();
                mockGetMirisStreamStatus.mockResolvedValue(status({ state: "preview" }));
                await advance(interval - 1);
                expect(screen.getByText(RUNNING_TEXT)).toBeInTheDocument();
                await advance(1);
                await waitForText(/preparing this asset/i);
                expect(screen.queryByText(RUNNING_TEXT)).not.toBeInTheDocument();
                expect(screen.getByText(/current state: preview/i)).toBeInTheDocument();
            }
        );

        it.each<[string, () => unknown, RegExp]>([
            [
                "a failed run with its reason",
                () =>
                    mockFindRunningMirisUpload.mockResolvedValue([
                        true,
                        listedRun("FAILED", "MirisUploadFailed: the root defines no geometry"),
                    ]),
                /The Miris upload failed: MirisUploadFailed: the root defines no geometry/,
            ],
            [
                "a skipped run",
                () => mockFindRunningMirisUpload.mockResolvedValue([true, listedRun("SKIPPED")]),
                /skipped because another upload was running/i,
            ],
        ])("reports %s from the asset's execution list", async (_label, finish, expected) => {
            jest.useFakeTimers();
            allowRoutes(EXECUTE_ROUTE, ASSET_EXECUTIONS_ROUTE);
            mockFindRunningMirisUpload
                .mockResolvedValueOnce([true, null])
                .mockResolvedValue([true, listedRun("RUNNING")]);
            await startRun();
            finish();
            await advance(30_000);
            await waitForText(expected);
            expect(screen.queryByText(RUNNING_TEXT)).not.toBeInTheDocument();
            expect(streamButton()).toBeInTheDocument();
            expect(mockFindRunningMirisUpload).toHaveBeenLastCalledWith({
                databaseId: expect.any(String),
                assetId: expect.any(String),
                executionId: "exec-1",
            });
        });

        it.each<[string, string[], number, () => unknown]>([
            ["the file's stream status", [EXECUTE_ROUTE], 90_000, () => undefined],
            [
                "an execution list that never shows the run",
                [EXECUTE_ROUTE, ASSET_EXECUTIONS_ROUTE],
                30_000,
                () => mockFindRunningMirisUpload.mockResolvedValue([true, null]),
            ],
        ])(
            "stops following a run %s cannot see after 90 minutes and offers the button again",
            async (_label, routes, interval, arrange) => {
                jest.useFakeTimers();
                allowRoutes(...routes);
                arrange();
                await startRun();
                await advance(90 * 60_000 - interval);
                expect(screen.getByText(RUNNING_TEXT)).toBeInTheDocument();
                await advance(interval);
                await waitForText(/did not report a result within 90 minutes/i);
                expect(screen.queryByText(RUNNING_TEXT)).not.toBeInTheDocument();
                expect(streamButton()).toBeInTheDocument();
            }
        );

        it("keeps following a run the execution list shows still running after 90 minutes", async () => {
            jest.useFakeTimers();
            allowRoutes(EXECUTE_ROUTE, ASSET_EXECUTIONS_ROUTE);
            mockFindRunningMirisUpload
                .mockResolvedValueOnce([true, null])
                .mockResolvedValue([true, listedRun("RUNNING")]);
            await startRun();
            await advance(100 * 60_000);
            expect(screen.getByText(RUNNING_TEXT)).toBeInTheDocument();
        });

        it.each<[string, string[], () => unknown, jest.Mock, number]>([
            [
                "execution details to the execution list",
                [EXECUTE_ROUTE, EXECUTION_DETAILS_ROUTE, ASSET_EXECUTIONS_ROUTE],
                () => mockGetMirisUploadRun.mockResolvedValue([false, "Service Unavailable"]),
                mockFindRunningMirisUpload,
                30_000,
            ],
            [
                "the execution list to the stream status",
                [EXECUTE_ROUTE, ASSET_EXECUTIONS_ROUTE],
                () =>
                    mockFindRunningMirisUpload
                        .mockResolvedValueOnce([true, null])
                        .mockResolvedValue([false, "Service Unavailable"]),
                mockGetMirisStreamStatus,
                90_000,
            ],
        ])(
            "falls from %s after two failed reads",
            async (_label, routes, arrange, next, interval) => {
                jest.useFakeTimers();
                allowRoutes(...routes);
                arrange();
                await startRun();
                // Each read is scheduled when the previous one settles.
                await advance(30_000);
                await advance(30_000);
                const before = next.mock.calls.length;
                await advance(interval - 1);
                expect(next).toHaveBeenCalledTimes(before);
                await advance(1);
                expect(next.mock.calls.length).toBeGreaterThan(before);
            }
        );

        it.each([
            [
                "shows why the run failed",
                { status: "FAILED", failure: "exit code 5: unresolved reference" },
                "The Miris upload failed: exit code 5: unresolved reference",
            ],
            [
                "says the workflow skipped the run",
                { status: "SKIPPED", failure: "The run ended with status SKIPPED." },
                "The Miris upload was skipped because another upload was running.",
            ],
        ])("%s, offers the button again and stops following it", async (_label, run, shown) => {
            jest.useFakeTimers();
            await startRun();
            mockGetMirisUploadRun.mockResolvedValue([true, { finished: true, ...run }]);
            await advance(30_000);
            await waitForText(shown);
            expect(screen.queryByText(/upload failed: The run ended/)).not.toBeInTheDocument();
            expect(mockGetMirisUploadRun).toHaveBeenCalledWith("exec-1");
            expect(streamButton()).toBeInTheDocument();
            await advance(300_000);
            expect(mockGetMirisUploadRun).toHaveBeenCalledTimes(1);
            expect(mockGetMirisStreamStatus).toHaveBeenCalledTimes(1);
        });

        it.each([
            ["the viewer unmounts", (view: ReturnType<typeof renderViewer>) => view.unmount()],
            [
                "the viewed file changes",
                (view: ReturnType<typeof renderViewer>) =>
                    view.rerender(
                        <MirisStreamViewerComponent {...baseProps} assetKey="asset-1/other.usd" />
                    ),
            ],
        ])("stops following the run when %s", async (_label, change) => {
            jest.useFakeTimers();
            const view = renderViewer();
            await waitForGuidance();
            clickStreamButton();
            await waitForText(RUNNING_TEXT);
            const timersBefore = jest.getTimerCount();
            await act(async () => change(view));
            expect(jest.getTimerCount()).toBeLessThan(timersBefore);
            await advance(300_000);
            expect(mockGetMirisUploadRun).not.toHaveBeenCalled();
        });

        it.each([
            ["a 403", [false, "Forbidden", 403], /permission to run the Miris upload/i, null],
            [
                "any other failure",
                [false, "Already running.", 400],
                "Already running.",
                /permission to run/i,
            ],
            ["a WAF-blocked request", BLOCKED, BLOCKED_MESSAGE, /permission to run/i],
        ])(
            "shows a message for %s and starts again on Retry",
            async (_label, result, shown, hidden) => {
                mockStartMirisUpload.mockResolvedValueOnce(result);
                renderViewer();
                await waitForGuidance();
                clickStreamButton();
                await waitForText(shown);
                if (hidden) expect(screen.queryByText(hidden)).not.toBeInTheDocument();
                expect(streamButton()).toBeInTheDocument();
                clickRetry();
                await waitForText(RUNNING_TEXT);
                expect(mockStartMirisUpload).toHaveBeenCalledTimes(2);
            }
        );
    });

    describe("upload already running", () => {
        const running = () => [
            true,
            {
                workflowExecutionId: "exec-running",
                workflowId: "miris-upload",
                workflowDatabaseId: "GLOBAL",
                executionStatus: "RUNNING",
            },
        ];
        const waitForButtonEnabled = () =>
            waitFor(() => expect(streamButton()).not.toHaveAttribute("aria-disabled", "true"));

        beforeEach(() => {
            withUploadPipeline();
            mockGetMirisStreamStatus.mockResolvedValue(NOT_STREAMED);
            allowRoutes(EXECUTE_ROUTE, EXECUTION_DETAILS_ROUTE, ASSET_EXECUTIONS_ROUTE);
        });

        it("hides the button and links the run while an upload on the asset is in progress", async () => {
            mockFindRunningMirisUpload.mockResolvedValue(running());
            renderViewer();
            await waitForText(RUNNING_TEXT);
            expect(mockFindRunningMirisUpload).toHaveBeenCalledWith({
                databaseId: "db-1",
                assetId: "asset-1",
            });
            expect(mockCan).toHaveBeenCalledWith("GET", ASSET_EXECUTIONS_ROUTE);
            expect(streamButton()).not.toBeInTheDocument();
            expect(screen.getByText("Uploading to Miris")).toBeInTheDocument();
            expect(screen.queryByText(/has not been uploaded to miris/i)).not.toBeInTheDocument();
            expect(screen.getByRole("link", { name: /view execution/i })).toHaveAttribute(
                "href",
                "#/executions/exec-running"
            );
        });

        it("links no execution for a user who may not read execution details", async () => {
            allowRoutes(EXECUTE_ROUTE, ASSET_EXECUTIONS_ROUTE);
            mockFindRunningMirisUpload.mockResolvedValue(running());
            renderViewer();
            await waitForText(RUNNING_TEXT);
            expect(screen.queryByRole("link", { name: /view execution/i })).not.toBeInTheDocument();
        });

        it("disables the button while the lookup is in flight", async () => {
            const lookup = deferred<unknown>();
            mockFindRunningMirisUpload.mockReturnValue(lookup.promise);
            renderViewer();
            await waitForGuidance();
            await waitFor(() => expect(streamButton()).toHaveAttribute("aria-disabled", "true"));
            await act(async () => lookup.resolve([true, null]));
            await waitForButtonEnabled();
        });

        it("offers the button when the lookup fails", async () => {
            mockFindRunningMirisUpload.mockResolvedValue([false, "Request failed"]);
            renderViewer();
            await waitForGuidance();
            await waitFor(() => expect(mockFindRunningMirisUpload).toHaveBeenCalledTimes(1));
            await waitForButtonEnabled();
            expect(screen.queryByText(RUNNING_TEXT)).not.toBeInTheDocument();
        });

        it.each([
            ["may not list the asset's executions", [EXECUTE_ROUTE, EXECUTION_DETAILS_ROUTE], true],
            [
                "would not be offered the button",
                [EXECUTION_DETAILS_ROUTE, ASSET_EXECUTIONS_ROUTE],
                false,
            ],
        ])("skips the lookup for a user who %s", async (_label, routes, offered) => {
            allowRoutes(...routes);
            mockFindRunningMirisUpload.mockResolvedValue(running());
            renderViewer();
            await waitForGuidance();
            expect(!!streamButton()).toBe(offered);
            expect(mockFindRunningMirisUpload).not.toHaveBeenCalled();
        });

        it("follows the run it found and offers the button again once it ends", async () => {
            jest.useFakeTimers();
            mockFindRunningMirisUpload.mockResolvedValue(running());
            renderViewer();
            await waitForText(RUNNING_TEXT);
            mockGetMirisUploadRun.mockResolvedValue([true, { finished: true, failure: null }]);
            mockFindRunningMirisUpload.mockResolvedValue([true, null]);
            await advance(30_000);
            await waitFor(() => expect(streamButton()).toBeInTheDocument());
            expect(mockGetMirisUploadRun).toHaveBeenCalledWith("exec-running");
            expect(screen.queryByText(RUNNING_TEXT)).not.toBeInTheDocument();
        });
    });

    describe("retry", () => {
        it("re-runs the status check and load from a status error", async () => {
            mockGetMirisStreamStatus.mockResolvedValueOnce([false, "Service Unavailable"]);
            renderViewer();
            await waitForText(STATUS_ERROR_TEXT);
            expect(streamElement()).toBeNull();
            expect(mockLoadMirisStream).not.toHaveBeenCalled();

            clickRetry();
            await waitForScene();
            expect(mockGetMirisStreamStatus).toHaveBeenCalledTimes(2);
            expect(mockLoadMirisStream).toHaveBeenCalledTimes(1);
            expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
        });
    });

    describe("errors", () => {
        const DENIED = [false, "Forbidden", 403, undefined];
        const INVALID_UUID = status({ mirisAssetUuid: "x", state: "invalidUuid" });
        const NOT_CONFIGURED = /Miris streaming is not configured/i;

        it.each([
            ["status", "an invalid UUID", INVALID_UUID, /not a valid UUID/i, null],
            ["status", "a failure", status({ state: "failed" }), /processing failed/i, null],
            ["status", "an unbound asset", status({ state: "notBound" }), NOT_BOUND_TEXT, null],
            ["status", "a denied request", DENIED, PERMISSION_TEXT, STATUS_ERROR_TEXT],
            ["status", "a WAF-blocked request", BLOCKED, BLOCKED_MESSAGE, PERMISSION_TEXT],
            [
                "key",
                "an unbound asset",
                [false, "", 403, "notBound"],
                NOT_BOUND_TEXT,
                PERMISSION_TEXT,
            ],
            ["key", "a denied request", DENIED, PERMISSION_TEXT, KEY_ERROR_TEXT],
            ["key", "a WAF-blocked request", BLOCKED, BLOCKED_MESSAGE, PERMISSION_TEXT],
            ["key", "a 503", [false, "", 503, undefined], NOT_CONFIGURED, null],
            ["key", "no stream", [false, "", 404, "notStreamed"], NOT_STREAMED_TEXT, null],
            [
                "key",
                "a quarantined asset",
                [false, QUARANTINE_REFUSAL_MESSAGE, QUARANTINE_REFUSAL_STATUS, undefined],
                QUARANTINE_REFUSAL_MESSAGE,
                KEY_ERROR_TEXT,
            ],
            [
                "key",
                "an asset that is not distributable",
                [false, "Asset not distributable", 400, undefined],
                "Asset not distributable",
                KEY_ERROR_TEXT,
            ],
        ])(
            "shows the %s route's answer for %s without loading the SDK",
            async (route, _label, result, shown, hidden) => {
                const mock = route === "status" ? mockGetMirisStreamStatus : mockMintMirisViewerKey;
                mock.mockResolvedValue(result);
                renderViewer();
                await waitForText(shown);
                if (hidden) expect(screen.queryByText(hidden)).not.toBeInTheDocument();
                expect(mockMintMirisViewerKey).toHaveBeenCalledTimes(route === "key" ? 1 : 0);
                expect(mockLoadMirisStream).not.toHaveBeenCalled();
                expect(sceneElement()).toBeNull();
            }
        );
    });

    describe("processing", () => {
        it("shows the processing overlay while Miris is still preparing the asset", async () => {
            mockGetMirisStreamStatus.mockResolvedValue(status({ state: "preview" }));
            renderViewer();
            await waitForText(/preparing this asset/i);
            expect(screen.getByText(/current state: preview/i)).toBeInTheDocument();
            expect(sceneElement()).toBeNull();
        });

        it("starts streaming once the poll reports the asset streamable", async () => {
            jest.useFakeTimers();
            mockGetMirisStreamStatus
                .mockResolvedValueOnce(status({ state: "preview" }))
                .mockResolvedValue(status({ state: "streamable" }));
            renderViewer();
            await waitForText(/preparing this asset/i);
            await advance(90_000);
            await waitFor(() => expect(streamElement()).toHaveAttribute("uuid", VALID_UUID));
            expect(screen.queryByText(/preparing this asset/i)).not.toBeInTheDocument();
        });
    });

    describe("stale banner", () => {
        it("shows the banner when the stream was built from a different version", async () => {
            mockGetMirisStreamStatus.mockResolvedValue(status({ sourceVersionId: "v0" }));
            renderViewer();
            await waitForText(STALE_TEXT);
            expect(streamElement()).toHaveAttribute("uuid", VALID_UUID);
            expect(mockFetchFileInfo).not.toHaveBeenCalled();
        });

        it("compares against the file's current version when no versionId is passed", async () => {
            mockFetchFileInfo.mockResolvedValue([
                true,
                {
                    versions: [
                        { versionId: "v1", isLatest: false },
                        { versionId: "v2", isLatest: true },
                    ],
                },
            ]);
            renderViewer({ versionId: undefined });
            await waitForText(STALE_TEXT);
            expect(mockFetchFileInfo).toHaveBeenCalledWith({
                databaseId: "db-1",
                assetId: "asset-1",
                fileKey: "asset-1/folder/scene.usd",
                includeVersions: true,
            });
        });

        it.each([
            ["reports a changed dependency", true, "v1", true],
            ["reports no change", false, "v0", false],
        ])(
            "uses the status's manifest check when it %s",
            async (_label, stale, sourceVersionId, shown) => {
                mockGetMirisStreamStatus.mockResolvedValue(status({ stale, sourceVersionId }));
                renderViewer({ versionId: undefined });
                await waitForScene();
                if (shown) await waitForText(STALE_TEXT);
                else expect(screen.queryByText(STALE_TEXT)).not.toBeInTheDocument();
                expect(mockFetchFileInfo).not.toHaveBeenCalled();
            }
        );

        it.each([
            ["the passed version matches", "v1", "v1", undefined],
            ["the current version matches", "v1", undefined, undefined],
            ["the file info lookup fails", "v0", undefined, [false, "Not Authorized"]],
        ])("shows no banner when %s", async (_label, sourceVersionId, versionId, fileInfo) => {
            mockGetMirisStreamStatus.mockResolvedValue(status({ sourceVersionId }));
            if (fileInfo) mockFetchFileInfo.mockResolvedValue(fileInfo);
            renderViewer({ versionId });
            await waitForScene();
            await waitFor(() => expect(mockFetchFileInfo).toHaveBeenCalledTimes(versionId ? 0 : 1));
            expect(screen.queryByText(STALE_TEXT)).not.toBeInTheDocument();
        });
    });

    describe("viewer key", () => {
        it("loads the SDK and renders the scene only after the viewer key exists", async () => {
            const key = deferred<unknown>();
            mockMintMirisViewerKey.mockReturnValue(key.promise);
            renderViewer();
            await waitFor(() => expect(mockMintMirisViewerKey).toHaveBeenCalledTimes(1));
            expect(mockLoadMirisStream).not.toHaveBeenCalled();
            expect(sceneElement()).toBeNull();
            await act(async () => key.resolve(mintedKey()));
            expect(await waitForScene()).toHaveAttribute("viewer-key", VIEWER_KEY);
            expect(mockLoadMirisStream).toHaveBeenCalledTimes(1);
        });

        it("never persists or logs the key", async () => {
            const localSet = jest.spyOn(Storage.prototype, "setItem");
            const logSpies = (["log", "warn", "error"] as const).map((level) =>
                jest.spyOn(console, level).mockImplementation(() => undefined)
            );
            renderViewer();
            await waitForScene();
            expect(localSet).not.toHaveBeenCalled();
            expect(mockAppCacheSet).not.toHaveBeenCalled();
            for (const spy of logSpies) {
                expect(JSON.stringify(spy.mock.calls)).not.toContain(VIEWER_KEY);
            }
            localSet.mockRestore();
            logSpies.forEach((spy) => spy.mockRestore());
        });

        it.each([
            ["a 502", [false, "bad gateway", 502, undefined]],
            ["an unknown status", [false, "no response", undefined, undefined]],
            ["a different asset UUID", mintedKey({ mirisAssetUuid: "other-uuid" })],
        ])("shows a retryable error on %s", async (_label, result) => {
            mockMintMirisViewerKey.mockResolvedValue(result);
            renderViewer();
            await waitForText(KEY_ERROR_TEXT);
            expect(retryButton()).toBeInTheDocument();
            expect(mockLoadMirisStream).not.toHaveBeenCalled();
            expect(sceneElement()).toBeNull();
        });

        it("builds no scene when unmounted before the key arrives", async () => {
            const key = deferred<unknown>();
            mockMintMirisViewerKey.mockReturnValue(key.promise);
            const view = renderViewer();
            await waitFor(() => expect(mockMintMirisViewerKey).toHaveBeenCalledTimes(1));
            view.unmount();
            await act(async () => key.resolve(mintedKey()));
            expect(sceneElement()).toBeNull();
            expect(mockLoadMirisStream).not.toHaveBeenCalled();
        });

        it("keeps the scene and its key when only the viewed file version changes", async () => {
            const view = renderViewer({ versionId: "v1" });
            const first = await waitForScene();
            view.rerender(<MirisStreamViewerComponent {...baseProps} versionId="v2" />);
            await waitForText(STALE_TEXT);
            expect(mockMintMirisViewerKey).toHaveBeenCalledTimes(1);
            expect(mockGetMirisStreamStatus).toHaveBeenCalledTimes(1);
            expect(sceneElement()).toBe(first);
        });

        it("asks for a key again on a remount", async () => {
            const first = renderViewer();
            await waitForScene();
            first.unmount();
            renderViewer();
            await waitFor(() => expect(mockMintMirisViewerKey).toHaveBeenCalledTimes(2));
            await waitForScene();
        });

        it("mounts a new scene element when the viewer key changes", async () => {
            mockMintMirisViewerKey
                .mockResolvedValueOnce(mintedKey({ viewerKey: "first-key" }))
                .mockResolvedValueOnce(mintedKey({ viewerKey: "second-key" }));
            jest.useFakeTimers();
            renderViewer();
            const first = await waitForScene();
            expect(first).toHaveAttribute("viewer-key", "first-key");

            await advance(60_000);
            clickRetry();
            await waitFor(() => expect(sceneElement()).toHaveAttribute("viewer-key", "second-key"));
            expect(sceneElement()).not.toBe(first);
            expect(first.isConnected).toBe(false);
        });
    });
});

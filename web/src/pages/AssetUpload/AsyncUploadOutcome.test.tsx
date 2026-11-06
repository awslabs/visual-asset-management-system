/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import AsyncUploadOutcome, { isFinalStatusError } from "./AsyncUploadOutcome";
import { ApiError } from "../../services/apiClient";
import type { UploadStatusResponse } from "../../services/AssetUploadService";

jest.mock("../../services/AssetUploadService", () => ({
    __esModule: true,
    default: { getUploadStatus: jest.fn() },
}));

const status = (
    over: Partial<UploadStatusResponse> & Pick<UploadStatusResponse, "status">
): UploadStatusResponse => ({
    uploadId: "u1",
    databaseId: "db",
    assetId: "a1",
    asyncFiles: [],
    ...over,
});

const pending = status({
    status: "processing_async",
    asyncFiles: [{ relativeKey: "/big.e57", status: "PENDING" }],
});

/** Let the poll's awaited fetch settle. */
const flush = async () => {
    await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
    });
};

describe("AsyncUploadOutcome", () => {
    beforeEach(() => jest.useFakeTimers());
    afterEach(() => jest.useRealTimers());

    it("polls until each file has an outcome, then stops", async () => {
        const fetchStatus = jest
            .fn()
            .mockResolvedValueOnce(pending)
            .mockResolvedValueOnce(
                status({
                    status: "completed",
                    asyncFiles: [{ relativeKey: "/big.e57", status: "SUCCEEDED" }],
                })
            );
        render(
            <AsyncUploadOutcome
                uploadIds={["u1"]}
                assetId="a1"
                databaseId="db"
                pollIntervalMs={1000}
                fetchStatus={fetchStatus}
            />
        );
        await flush();
        expect(fetchStatus).toHaveBeenCalledWith("u1", "a1", "db");
        expect(screen.getByText("Processing")).toBeInTheDocument();
        expect(screen.getAllByText("Large files still processing")).toHaveLength(1);

        await act(async () => {
            jest.advanceTimersByTime(1000);
        });
        await flush();
        expect(screen.getByText("In the asset")).toBeInTheDocument();
        expect(screen.queryByText("Large files still processing")).not.toBeInTheDocument();
        // Nothing is scheduled once every file has an outcome.
        expect(jest.getTimerCount()).toBe(0);

        await act(async () => {
            jest.advanceTimersByTime(10_000);
        });
        expect(fetchStatus).toHaveBeenCalledTimes(2);
    });

    it("names each failed file with its reason", async () => {
        const fetchStatus = jest.fn().mockResolvedValue(
            status({
                status: "completed_with_errors",
                asyncFiles: [
                    { relativeKey: "/a.e57", status: "SUCCEEDED" },
                    {
                        relativeKey: "/b.e57",
                        status: "FAILED",
                        error: "The file could not be processed into the asset.",
                    },
                ],
            })
        );
        render(
            <AsyncUploadOutcome
                uploadIds={["u1"]}
                assetId="a1"
                databaseId="db"
                fetchStatus={fetchStatus}
            />
        );
        await flush();
        expect(screen.getByText("Large files that did not reach the asset")).toBeInTheDocument();
        expect(
            screen.getByText("/b.e57: The file could not be processed into the asset.")
        ).toBeInTheDocument();
        expect(screen.getByText("Failed")).toBeInTheDocument();
    });

    it.each([502, 503, 500])(
        "retries a transient %s and still reports the outcome",
        async (code) => {
            // L5: one transient error during an hour-long wait no longer ends it as "not available".
            const fetchStatus = jest
                .fn()
                .mockRejectedValueOnce(new ApiError(`HTTP ${code}`, code))
                .mockResolvedValueOnce(pending)
                .mockRejectedValueOnce(new TypeError("Failed to fetch"))
                .mockResolvedValueOnce(
                    status({
                        status: "completed",
                        asyncFiles: [{ relativeKey: "/big.e57", status: "SUCCEEDED" }],
                    })
                );
            render(
                <AsyncUploadOutcome
                    uploadIds={["u1"]}
                    assetId="a1"
                    databaseId="db"
                    pollIntervalMs={1000}
                    fetchStatus={fetchStatus}
                />
            );
            for (let i = 0; i < 3; i++) {
                await flush();
                expect(
                    screen.queryByText(/The status of some large files is not available here/)
                ).not.toBeInTheDocument();
                await act(async () => {
                    jest.advanceTimersByTime(1000);
                });
            }
            await flush();
            expect(fetchStatus).toHaveBeenCalledTimes(4);
            expect(screen.getByText("In the asset")).toBeInTheDocument();
            expect(jest.getTimerCount()).toBe(0);
        }
    );

    it.each([400, 403, 404])("treats a %s as final", (code) => {
        expect(isFinalStatusError(new ApiError(`HTTP ${code}`, code))).toBe(true);
        expect(isFinalStatusError(new ApiError("HTTP 503", 503))).toBe(false);
        expect(isFinalStatusError(new TypeError("Failed to fetch"))).toBe(false);
    });

    it("stops with a neutral note when the status cannot be read", async () => {
        const fetchStatus = jest.fn().mockRejectedValue(new ApiError("Upload not found", 404));
        render(
            <AsyncUploadOutcome
                uploadIds={["u1"]}
                assetId="a1"
                databaseId="db"
                pollIntervalMs={1000}
                fetchStatus={fetchStatus}
            />
        );
        await flush();
        expect(
            screen.getByText(/The status of some large files is not available here/)
        ).toBeInTheDocument();
        await act(async () => {
            jest.advanceTimersByTime(5000);
        });
        expect(fetchStatus).toHaveBeenCalledTimes(1);
    });

    it("stops after the wait limit and says so", async () => {
        const fetchStatus = jest.fn().mockResolvedValue(pending);
        const now = jest.spyOn(Date, "now");
        now.mockReturnValue(0);
        render(
            <AsyncUploadOutcome
                uploadIds={["u1"]}
                assetId="a1"
                databaseId="db"
                pollIntervalMs={1000}
                pollLimitMs={2000}
                fetchStatus={fetchStatus}
            />
        );
        await flush();
        now.mockReturnValue(2500);
        await act(async () => {
            jest.advanceTimersByTime(1000);
        });
        await flush();
        expect(screen.getByText(/stopped checking after an hour/)).toBeInTheDocument();
        const calls = fetchStatus.mock.calls.length;
        await act(async () => {
            jest.advanceTimersByTime(5000);
        });
        expect(fetchStatus).toHaveBeenCalledTimes(calls);
        now.mockRestore();
    });

    it("stops polling on unmount", async () => {
        const fetchStatus = jest.fn().mockResolvedValue(pending);
        const { unmount } = render(
            <AsyncUploadOutcome
                uploadIds={["u1"]}
                assetId="a1"
                databaseId="db"
                pollIntervalMs={1000}
                fetchStatus={fetchStatus}
            />
        );
        await flush();
        unmount();
        await act(async () => {
            jest.advanceTimersByTime(5000);
        });
        expect(fetchStatus).toHaveBeenCalledTimes(1);
    });

    it("reads the route through the upload service by default", async () => {
        const service = require("../../services/AssetUploadService").default;
        service.getUploadStatus.mockResolvedValue(
            status({
                status: "completed",
                asyncFiles: [{ relativeKey: "/x.las", status: "SUCCEEDED" }],
            })
        );
        render(<AsyncUploadOutcome uploadIds={["u9"]} assetId="a9" databaseId="db9" />);
        await flush();
        expect(service.getUploadStatus).toHaveBeenCalledWith("u9", "a9", "db9");
        expect(screen.getByText("/x.las")).toBeInTheDocument();
    });
});

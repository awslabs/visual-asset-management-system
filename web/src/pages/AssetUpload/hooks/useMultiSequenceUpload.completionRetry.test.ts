/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Upload completion retries transient server errors (429, 500, 502, 503) and reports a 504 as an
 * unverified outcome. A 503 is never taken as success: a completion that keeps failing marks the
 * sequence failed.
 */

import { renderHook, act } from "@testing-library/react";

import { useMultiSequenceUpload } from "./useMultiSequenceUpload";
import AssetUploadService from "../../../services/AssetUploadService";

jest.mock("../../../services/AssetUploadService", () => ({
    __esModule: true,
    default: { completeUpload: jest.fn() },
}));

const completeUploadMock = AssetUploadService.completeUpload as jest.Mock;

const SEQUENCE_ID = 1;
const FILE_KEY = "/model.glb";

function httpError(status: number) {
    return Object.assign(new Error(`HTTP ${status}`), { status });
}

function buildArgs() {
    const sequence = { sequenceId: SEQUENCE_ID, files: [{ index: 0, relativePath: FILE_KEY }] };
    const fileParts = [
        {
            fileIndex: 0,
            partNumber: 1,
            start: 0,
            end: 1,
            uploadUrl: "u",
            status: "completed",
            etag: "e1",
            retryCount: 0,
            sequenceId: SEQUENCE_ID,
        },
    ];
    const initResponse = {
        files: [{ relativeKey: FILE_KEY, uploadIdS3: "s3-1", numParts: 1, partUploadUrls: [] }],
    };
    return [sequence, "upload-1", "asset-1", "db-1", fileParts, [], initResponse] as any[];
}

/** Run completeSequence to the end under fake timers, so retry backoffs do not wait for real. */
async function runCompletion() {
    const { result } = renderHook(() => useMultiSequenceUpload());
    let outcome: { value?: any; error?: any } = {};
    await act(async () => {
        const pending = (result.current.completeSequence as any)(...buildArgs()).then(
            (value: any) => (outcome = { value }),
            (error: any) => (outcome = { error })
        );
        await jest.advanceTimersByTimeAsync(10 * 60 * 1000);
        await pending;
    });
    return { result, outcome };
}

describe("useMultiSequenceUpload — completion retry", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.useFakeTimers();
        jest.spyOn(console, "error").mockImplementation(() => {});
        jest.spyOn(console, "log").mockImplementation(() => {});
    });

    afterEach(() => {
        jest.useRealTimers();
        jest.restoreAllMocks();
    });

    it.each([429, 500, 502, 503])("retries a %i and completes", async (status) => {
        completeUploadMock
            .mockRejectedValueOnce(httpError(status))
            .mockResolvedValue({ overallSuccess: true, fileResults: [] });

        const { result, outcome } = await runCompletion();

        expect(completeUploadMock).toHaveBeenCalledTimes(2);
        expect(outcome.value).toEqual({ overallSuccess: true, fileResults: [] });
        expect(result.current.sequenceCompleteStatuses.get(SEQUENCE_ID)).toBe("completed");
    });

    it("marks the sequence failed when 503 persists", async () => {
        completeUploadMock.mockRejectedValue(httpError(503));

        const { result, outcome } = await runCompletion();

        expect(completeUploadMock).toHaveBeenCalledTimes(4);
        expect(outcome.error).toBeDefined();
        expect(result.current.sequenceCompleteStatuses.get(SEQUENCE_ID)).toBe("failed");
    });

    it("reports a 504 as an unverified outcome without retrying", async () => {
        completeUploadMock.mockRejectedValue(httpError(504));

        const { result, outcome } = await runCompletion();

        expect(completeUploadMock).toHaveBeenCalledTimes(1);
        expect(outcome.value).toMatchObject({ outcomeUnverified: true, sequenceId: SEQUENCE_ID });
        expect(result.current.sequenceCompleteStatuses.get(SEQUENCE_ID)).toBe("completed");
    });

    it("does not retry a 400 at completion", async () => {
        completeUploadMock.mockRejectedValue(httpError(400));

        const { result, outcome } = await runCompletion();

        expect(completeUploadMock).toHaveBeenCalledTimes(1);
        expect(outcome.error).toBeDefined();
        expect(result.current.sequenceCompleteStatuses.get(SEQUENCE_ID)).toBe("failed");
    });
});

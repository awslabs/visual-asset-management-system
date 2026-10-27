/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Upload initialization (POST /uploads) retries transient server errors (429, 500, 502, 503). It
 * does not retry a 504: init is not idempotent, and a 504 can follow a request that created the
 * upload, so a retry would create a second one.
 */

import { renderHook, act } from "@testing-library/react";

import { useMultiSequenceUpload } from "./useMultiSequenceUpload";
import AssetUploadService from "../../../services/AssetUploadService";

jest.mock("../../../services/AssetUploadService", () => ({
    __esModule: true,
    default: { initializeUpload: jest.fn() },
}));

const initializeUploadMock = AssetUploadService.initializeUpload as jest.Mock;

const SEQUENCE_ID = 1;
const FILE_KEY = "/model.glb";
const INIT_RESPONSE = { uploadId: "upload-1", files: [] };

function httpError(status: number) {
    return Object.assign(new Error(`HTTP ${status}`), { status });
}

function buildArgs() {
    const sequence = {
        sequenceId: SEQUENCE_ID,
        files: [{ index: 0, relativePath: FILE_KEY }],
        fileParts: new Map(),
    };
    const fileUploadItems = [
        {
            index: 0,
            name: "model.glb",
            relativePath: FILE_KEY,
            handle: new File(["abc"], "model.glb"),
        },
    ];
    return [sequence, "asset-1", "db-1", fileUploadItems] as any[];
}

/** Run initializeSequence to the end under fake timers, so retry backoffs do not wait for real. */
async function runInit() {
    const { result } = renderHook(() => useMultiSequenceUpload());
    let outcome: { value?: any; error?: any } = {};
    await act(async () => {
        const pending = (result.current.initializeSequence as any)(...buildArgs()).then(
            (value: any) => (outcome = { value }),
            (error: any) => (outcome = { error })
        );
        await jest.advanceTimersByTimeAsync(10 * 60 * 1000);
        await pending;
    });
    return { result, outcome };
}

describe("useMultiSequenceUpload — init retry", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.useFakeTimers();
        jest.spyOn(console, "error").mockImplementation(() => undefined);
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });

    afterEach(() => {
        jest.useRealTimers();
        jest.restoreAllMocks();
    });

    it.each([429, 500, 502, 503])("retries a %i and initializes", async (status) => {
        initializeUploadMock
            .mockRejectedValueOnce(httpError(status))
            .mockResolvedValue(INIT_RESPONSE);

        const { result, outcome } = await runInit();

        expect(initializeUploadMock).toHaveBeenCalledTimes(2);
        expect(outcome.value).toMatchObject({ sequenceId: SEQUENCE_ID, uploadId: "upload-1" });
        expect(result.current.sequenceInitStatuses.get(SEQUENCE_ID)).toBe("completed");
    });

    it("does not retry a 504 at init", async () => {
        initializeUploadMock.mockRejectedValueOnce(httpError(504)).mockResolvedValue(INIT_RESPONSE);

        const { result, outcome } = await runInit();

        expect(initializeUploadMock).toHaveBeenCalledTimes(1);
        expect(outcome.error).toBeDefined();
        expect(result.current.sequenceInitStatuses.get(SEQUENCE_ID)).toBe("failed");
    });
});

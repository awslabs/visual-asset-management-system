/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The parts a completion request carries for each file.
 *
 * An empty `parts` list tells the backend to abandon the file. That is the intended signal for a
 * file the user cancelled, and correct for a zero-byte file, but for any other file it drops content
 * that was uploaded while the page reports the file stored. So a completion for a non-empty file that
 * was not cancelled must carry every part it was given an upload URL for, or not be sent at all.
 *
 * Each arm asserts both what is sent and what is not, because a guard that refuses everything, or
 * nothing, would satisfy half of them.
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
const KEY = "/model.glb";

function sequence() {
    return { sequenceId: SEQUENCE_ID, files: [{ index: 0, relativePath: KEY }] } as any;
}

function initResponse(urlCount: number) {
    return {
        uploadId: "upload-1",
        message: "",
        files: [
            {
                relativeKey: KEY,
                uploadIdS3: urlCount === 0 ? "zero-byte" : "s3-1",
                numParts: urlCount,
                partUploadUrls: Array.from({ length: urlCount }, (_, i) => ({
                    PartNumber: i + 1,
                    UploadUrl: `https://parts.example/${i + 1}`,
                })),
            },
        ],
    } as any;
}

function fileItems(size: number) {
    return [{ index: 0, name: "model.glb", relativePath: KEY, size, total: size }] as any;
}

function part(partNumber: number, status: string, etag?: string) {
    return {
        fileIndex: 0,
        partNumber,
        start: 0,
        end: 1,
        uploadUrl: `https://parts.example/${partNumber}`,
        status,
        etag,
        retryCount: 0,
        sequenceId: SEQUENCE_ID,
    };
}

async function complete(options: {
    urlCount: number;
    size: number;
    parts: any[];
    cancelled?: Set<number>;
}) {
    const { result } = renderHook(() => useMultiSequenceUpload());
    let error: any;
    await act(async () => {
        try {
            await result.current.completeSequence(
                sequence(),
                "upload-1",
                "asset-1",
                "db-1",
                options.parts as any,
                fileItems(options.size),
                initResponse(options.urlCount),
                undefined,
                options.cancelled
            );
        } catch (e) {
            error = e;
        }
    });
    return { result, error };
}

function sentParts() {
    expect(completeUploadMock).toHaveBeenCalledTimes(1);
    return completeUploadMock.mock.calls[0][1].files[0].parts;
}

describe("useMultiSequenceUpload — parts sent at completion", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
        completeUploadMock.mockResolvedValue({ fileResults: [] });
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("sends every recorded part of a non-empty file", async () => {
        const { result, error } = await complete({
            urlCount: 2,
            size: 4,
            parts: [part(1, "completed", "e1"), part(2, "completed", "e2")],
        });

        expect(error).toBeUndefined();
        expect(sentParts()).toEqual([
            { PartNumber: 1, ETag: "e1" },
            { PartNumber: 2, ETag: "e2" },
        ]);
        expect(result.current.sequenceCompleteStatuses.get(SEQUENCE_ID)).toBe("completed");
    });

    it("fails the sequence instead of sending a non-empty file with no recorded parts", async () => {
        // The part finished uploading but its ETag is not recorded in these parts — the state the
        // completion was built from when it read the parts too early.
        const { result, error } = await complete({
            urlCount: 1,
            size: 4,
            parts: [part(1, "in-progress")],
        });

        expect(completeUploadMock).not.toHaveBeenCalled();
        expect(result.current.sequenceCompleteStatuses.get(SEQUENCE_ID)).toBe("failed");
        expect(error?.message).toContain(`${KEY} has 0 of 1 uploaded parts recorded`);
        expect(error?.incompleteFiles).toEqual([
            { fileIndex: 0, relativeKey: KEY, expectedParts: 1, recordedParts: 0 },
        ]);
    });

    it("fails the sequence when only some of a file's parts are recorded", async () => {
        const { result, error } = await complete({
            urlCount: 2,
            size: 4,
            parts: [part(1, "completed", "e1"), part(2, "completed")],
        });

        expect(completeUploadMock).not.toHaveBeenCalled();
        expect(result.current.sequenceCompleteStatuses.get(SEQUENCE_ID)).toBe("failed");
        expect(error?.incompleteFiles).toEqual([
            { fileIndex: 0, relativeKey: KEY, expectedParts: 2, recordedParts: 1 },
        ]);
    });

    it("does not send `parts: []` for a non-empty file even when it was given no upload URLs", async () => {
        const { error } = await complete({ urlCount: 0, size: 4, parts: [] });

        expect(completeUploadMock).not.toHaveBeenCalled();
        expect(error?.incompleteFiles).toHaveLength(1);
    });

    it("still sends `parts: []` for a cancelled file, which abandons it", async () => {
        const { result, error } = await complete({
            urlCount: 2,
            size: 4,
            parts: [part(1, "completed", "e1"), part(2, "cancelled")],
            cancelled: new Set([0]),
        });

        expect(error).toBeUndefined();
        expect(sentParts()).toEqual([]);
        expect(result.current.sequenceCompleteStatuses.get(SEQUENCE_ID)).toBe("completed");
    });

    it("still completes a zero-byte file with no parts", async () => {
        const { result, error } = await complete({ urlCount: 0, size: 0, parts: [] });

        expect(error).toBeUndefined();
        expect(sentParts()).toEqual([]);
        expect(result.current.sequenceCompleteStatuses.get(SEQUENCE_ID)).toBe("completed");
    });
});

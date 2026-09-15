/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The upload summary must follow the SERVER's per-file verdict, not just the client's part uploads.
 *
 * A file whose parts uploaded cleanly has a client status of "Completed" even when completion then
 * refuses it — the server validates at that point and DELETES what it rejects. Building the failure
 * summary from client statuses alone therefore reported "Upload completed successfully" for an upload
 * that stored nothing for those files. Measured live on a `.previewFile.` companion whose completion
 * returned `success: false, error: "Error verifying base file for preview file"`.
 *
 * Both directions are asserted, because either alone is satisfied by a broken implementation: a
 * refused file must be recorded, and an accepted file (or a response predating the field) must NOT be,
 * or every upload reports failure.
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
const BASE_KEY = "/base.glb";
const COMPANION_KEY = "/base.glb.previewFile.png";

/** The two files of one sequence: a base file and its preview companion. */
function buildSequence() {
    return {
        sequenceId: SEQUENCE_ID,
        files: [
            { index: 0, relativePath: BASE_KEY },
            { index: 1, relativePath: COMPANION_KEY },
        ],
    } as any;
}

function buildInitResponse() {
    return {
        files: [
            { relativeKey: BASE_KEY, uploadIdS3: "s3-1", numParts: 1, partUploadUrls: [] },
            { relativeKey: COMPANION_KEY, uploadIdS3: "s3-2", numParts: 1, partUploadUrls: [] },
        ],
    } as any;
}

/** Parts that already finished — which is what makes each file a success candidate. */
function buildFileParts() {
    return [
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
        {
            fileIndex: 1,
            partNumber: 1,
            start: 0,
            end: 1,
            uploadUrl: "u",
            status: "completed",
            etag: "e2",
            retryCount: 0,
            sequenceId: SEQUENCE_ID,
        },
    ] as any;
}

async function runCompletion(response: any) {
    completeUploadMock.mockResolvedValue(response);
    const { result } = renderHook(() => useMultiSequenceUpload());

    await act(async () => {
        await result.current.completeSequence(
            buildSequence(),
            "upload-1",
            "asset-1",
            "db-1",
            buildFileParts(),
            [] as any,
            buildInitResponse()
        );
    });

    return result;
}

describe("useMultiSequenceUpload — server rejection at completion", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("records a file the server refused, keyed by its relativeKey", async () => {
        const result = await runCompletion({
            overallSuccess: false,
            fileResults: [
                { relativeKey: BASE_KEY, uploadIdS3: "s3-1", success: true },
                {
                    relativeKey: COMPANION_KEY,
                    uploadIdS3: "s3-2",
                    success: false,
                    error: "Error verifying base file for preview file",
                },
            ],
        });

        expect(Array.from(result.current.serverRejectedFiles.keys())).toEqual([COMPANION_KEY]);
        expect(result.current.serverRejectedFiles.get(COMPANION_KEY)?.error).toContain(
            "Error verifying base file"
        );
    });

    it("records nothing when every file was accepted", async () => {
        // Paired arm: without it, recording every file would satisfy the test above.
        const result = await runCompletion({
            overallSuccess: true,
            fileResults: [
                { relativeKey: BASE_KEY, uploadIdS3: "s3-1", success: true },
                { relativeKey: COMPANION_KEY, uploadIdS3: "s3-2", success: true },
            ],
        });

        expect(result.current.serverRejectedFiles.size).toBe(0);
    });

    it("records nothing when the response carries no per-file results", async () => {
        // An older deployment, or an endpoint reporting no per-file detail, must not fail every file.
        const result = await runCompletion({ message: "Upload completed" });

        expect(result.current.serverRejectedFiles.size).toBe(0);
    });

    it("does not treat a result omitting `success` as a failure", async () => {
        // Only an EXPLICIT false demotes. A missing key is not evidence of failure.
        const result = await runCompletion({
            fileResults: [
                { relativeKey: BASE_KEY, uploadIdS3: "s3-1" },
                { relativeKey: COMPANION_KEY, uploadIdS3: "s3-2" },
            ],
        });

        expect(result.current.serverRejectedFiles.size).toBe(0);
    });

    it("has the rejection recorded by the time the sequence reads as completed", async () => {
        // The ordering the summary depends on, and the reason this arm exists rather than being
        // assumed. UploadManager fires its final-summary effect when the completed-sequence count
        // reaches the sequence total, and that effect is one-shot — it sets `finalCompletionTriggered`
        // and never runs again. So if the "completed" status were to land in an EARLIER render than the
        // rejection map, the summary would be built from an empty map and the later update could never
        // correct it: the rejection would be dropped and the upload would report success again, which
        // is the original defect.
        //
        // Asserted on the two values being visible together rather than on statement order in the
        // source, so a refactor that splits them across renders fails here.
        const result = await runCompletion({
            overallSuccess: false,
            fileResults: [
                {
                    relativeKey: COMPANION_KEY,
                    uploadIdS3: "s3-2",
                    success: false,
                    error: "Error verifying base file for preview file",
                },
            ],
        });

        expect(result.current.sequenceCompleteStatuses.get(SEQUENCE_ID)).toBe("completed");
        expect(result.current.serverRejectedFiles.has(COMPANION_KEY)).toBe(true);
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * When the upload page offers a part re-upload.
 *
 * The file table's "Retry N failed Items" and a row's retry re-queue the parts whose status is
 * "failed". A file the server refused at completion is Failed with every part uploaded, so for it
 * those retries had nothing to send: pressing them sent no request and changed nothing. They are
 * offered only when some part failed.
 *
 * The real UploadManager runs here against a mocked service layer.
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import UploadManager from "./UploadManager";
import AssetUploadService from "../../services/AssetUploadService";

jest.mock("../../services/AssetUploadService", () => ({
    __esModule: true,
    default: {
        initializeUpload: jest.fn(),
        uploadPart: jest.fn(),
        completeUpload: jest.fn(),
    },
}));
// The asset already exists in every test, so asset creation, metadata and links are never called.
jest.mock("./hooks/useAssetOperations", () => ({
    useAssetOperations: () => ({
        createAsset: jest.fn(),
        addMetadata: jest.fn(),
        createAssetLinks: jest.fn(),
    }),
}));
// One file per sequence, so one file can be refused at completion while another has a failed
// part; and no automatic part retries, so a failed part fails at once instead of after backoff.
jest.mock("../../constants/uploadLimits", () => ({
    ...jest.requireActual("../../constants/uploadLimits"),
    MAX_FILES_PER_REQUEST: 1,
    MAX_RETRY_ATTEMPTS: 0,
}));

const service = AssetUploadService as unknown as {
    initializeUpload: jest.Mock;
    uploadPart: jest.Mock;
    completeUpload: jest.Mock;
};

const REFUSED_KEY = "/model.glb";
const OTHER_KEY = "/notes.txt";
const REFUSAL = "Upload cancelled: no parts were provided; nothing was stored";

function fileItem(index: number, key: string) {
    const name = key.slice(1);
    return {
        index,
        name,
        relativePath: key,
        size: 4,
        handle: new File(["abcd"], name),
        status: "Queued",
        progress: 0,
        loaded: 0,
        total: 4,
    } as any;
}

/** Init answers every requested file with one part URL; the upload id names the file. */
function initFor(request: { files: { relativeKey: string }[] }) {
    return {
        uploadId: `upload${request.files[0].relativeKey}`,
        message: "",
        files: request.files.map((f) => ({
            relativeKey: f.relativeKey,
            uploadIdS3: `s3${f.relativeKey}`,
            numParts: 1,
            partUploadUrls: [{ PartNumber: 1, UploadUrl: `https://parts.example${f.relativeKey}` }],
        })),
    };
}

/** Completion refuses REFUSED_KEY and stores everything else. */
function completionFor(uploadId: string, request: { files: { relativeKey: string }[] }) {
    const fileResults = request.files.map((f) =>
        f.relativeKey === REFUSED_KEY
            ? { relativeKey: f.relativeKey, uploadIdS3: "", success: false, error: REFUSAL }
            : { relativeKey: f.relativeKey, uploadIdS3: "", success: true }
    );
    return {
        message: "done",
        uploadId,
        assetId: "asset-1",
        overallSuccess: fileResults.every((r) => r.success),
        fileResults,
    };
}

function mount(keys: string[]) {
    const onUploadComplete = jest.fn();
    render(
        <UploadManager
            assetDetail={{ assetId: "asset-1", databaseId: "db-1" } as any}
            metadata={{}}
            fileItems={keys.map((key, index) => fileItem(index, key))}
            onUploadComplete={onUploadComplete}
            onError={jest.fn()}
            isExistingAsset={true}
        />
    );
    return onUploadComplete;
}

describe("UploadManager part retry", () => {
    beforeEach(() => {
        jest.resetAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "warn").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
        service.initializeUpload.mockImplementation(async (request) => initFor(request));
        service.completeUpload.mockImplementation(async (uploadId, request) =>
            completionFor(uploadId, request)
        );
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("offers no retry for a file the server refused at completion", async () => {
        service.uploadPart.mockResolvedValue("etag-1");

        const onUploadComplete = mount([REFUSED_KEY]);
        await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));

        // The row keeps its Failed state and the server's reason ...
        expect(screen.getByText(`Failed: ${REFUSAL}`)).toBeInTheDocument();
        // ... and every part uploaded, so there is nothing a re-upload could send.
        expect(screen.queryByRole("button", { name: /failed Items/ })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Retry model.glb" })).not.toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Retry Failed Uploads" })
        ).not.toBeInTheDocument();
    });

    it("still offers the retry when a part failed, and the retry re-uploads that part", async () => {
        // The refused file's part uploads; the other file's part fails once, then uploads.
        let otherPartAttempts = 0;
        service.uploadPart.mockImplementation(async (url: string) => {
            if (url.endsWith(OTHER_KEY)) {
                otherPartAttempts++;
                if (otherPartAttempts === 1) {
                    throw new Error("Network Error");
                }
                return "etag-other";
            }
            return "etag-refused";
        });

        mount([REFUSED_KEY, OTHER_KEY]);

        await waitFor(() => expect(screen.getByText(`Failed: ${REFUSAL}`)).toBeInTheDocument());
        // Both rows are Failed: the refused file with the server's reason, the other file with its
        // part's error.
        expect(screen.getByText("Failed: Network Error")).toBeInTheDocument();
        const retry = await screen.findByRole("button", { name: "Retry 2 failed Items" });
        expect(otherPartAttempts).toBe(1);

        fireEvent.click(retry);

        // The failed part is sent again, and its file then completes with that part.
        await waitFor(() =>
            expect(service.completeUpload).toHaveBeenCalledWith(
                `upload${OTHER_KEY}`,
                expect.objectContaining({
                    files: [
                        expect.objectContaining({
                            relativeKey: OTHER_KEY,
                            parts: [{ PartNumber: 1, ETag: "etag-other" }],
                        }),
                    ],
                })
            )
        );
        expect(otherPartAttempts).toBe(2);
    });
});

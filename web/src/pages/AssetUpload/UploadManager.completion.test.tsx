/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the upload page sends at completion, and what it shows afterwards.
 *
 * A sequence is completed the moment its last part records its ETag. The completion was built from
 * the parts as the LAST RENDER saw them, so when the queue's completion check ran before React had
 * rendered that ETag update, the part was still "in progress" there, it was left out, and the
 * request carried `parts: []` — which the backend reads as "abandon this file". The page then showed
 * the file Completed. A file the server refused at completion also kept its Completed row.
 *
 * The real UploadManager runs here against a mocked service layer, so these tests exercise the
 * page's own state flow rather than a copy of it.
 */

import React from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
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

const service = AssetUploadService as unknown as {
    initializeUpload: jest.Mock;
    uploadPart: jest.Mock;
    completeUpload: jest.Mock;
};

const KEY = "/model.glb";
const REFUSAL = "Error verifying base file for preview file";

function fileItem() {
    return {
        index: 0,
        name: "model.glb",
        relativePath: KEY,
        size: 4,
        handle: new File(["abcd"], "model.glb"),
        status: "Queued",
        progress: 0,
        loaded: 0,
        total: 4,
    } as any;
}

/** The init response for the one file, with `urlCount` part upload URLs. */
function initResponse(urlCount = 1) {
    return {
        uploadId: "upload-1",
        message: "",
        files: [
            {
                relativeKey: KEY,
                uploadIdS3: "s3-1",
                numParts: urlCount,
                partUploadUrls: Array.from({ length: urlCount }, (_, i) => ({
                    PartNumber: i + 1,
                    UploadUrl: `https://parts.example/${i + 1}`,
                })),
            },
        ],
    };
}

function completion(fileResult: Record<string, unknown>) {
    return {
        message: "done",
        uploadId: "upload-1",
        assetId: "asset-1",
        overallSuccess: fileResult.success !== false,
        fileResults: [{ relativeKey: KEY, uploadIdS3: "s3-1", ...fileResult }],
    };
}

function mount() {
    const onUploadComplete = jest.fn();
    render(
        <UploadManager
            assetDetail={{ assetId: "asset-1", databaseId: "db-1" } as any}
            metadata={{}}
            fileItems={[fileItem()]}
            onUploadComplete={onUploadComplete}
            onError={jest.fn()}
            isExistingAsset={true}
        />
    );
    return onUploadComplete;
}

describe("UploadManager completion", () => {
    beforeEach(() => {
        jest.resetAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "warn").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
        service.initializeUpload.mockResolvedValue(initResponse());
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("lists the last part with its ETag when the sequence completes before React renders it", async () => {
        let finishPart: (etag: string) => void = () => undefined;
        service.uploadPart.mockImplementation(
            () =>
                new Promise<string>((resolve) => {
                    finishPart = resolve;
                })
        );
        service.completeUpload.mockResolvedValue(completion({ success: true }));

        mount();
        await waitFor(() => expect(service.uploadPart).toHaveBeenCalledTimes(1));

        // `act` holds React's rendering until its callback settles. Inside it the part records its
        // ETag and the upload queue makes its next completion check (it polls every 100 ms): the
        // ETag update is queued and its render has not run, which is the window in which the
        // completion was built without the part.
        await act(async () => {
            finishPart("etag-1");
            await new Promise((resolve) => setTimeout(resolve, 300));
        });

        await waitFor(() => expect(service.completeUpload).toHaveBeenCalledTimes(1));
        const [uploadId, request] = service.completeUpload.mock.calls[0];
        expect(uploadId).toBe("upload-1");
        expect(request.files).toEqual([
            { relativeKey: KEY, uploadIdS3: "s3-1", parts: [{ PartNumber: 1, ETag: "etag-1" }] },
        ]);
    });

    it("shows an accepted file Completed and reports the upload Completed", async () => {
        // Paired arm for the refusal below: an accepted file must not be demoted.
        service.uploadPart.mockResolvedValue("etag-1");
        service.completeUpload.mockResolvedValue(completion({ success: true }));

        const onUploadComplete = mount();
        await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));

        expect(onUploadComplete.mock.calls[0][0]).toMatchObject({
            overallSuccess: true,
            message: "Upload completed successfully",
        });
        expect(screen.queryByText(/Failed/)).not.toBeInTheDocument();
        expect(screen.queryByText("Completed with Errors")).not.toBeInTheDocument();
    });

    it("shows a file the server refused at completion as Failed, with the reason", async () => {
        service.uploadPart.mockResolvedValue("etag-1");
        service.completeUpload.mockResolvedValue(completion({ success: false, error: REFUSAL }));

        const onUploadComplete = mount();
        await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));

        // The file's row, not only the summary, says it was not stored and why.
        expect(screen.getByText(`Failed: ${REFUSAL}`)).toBeInTheDocument();
        // The completion step is not reported as plain Completed, and neither is the result.
        expect(screen.getByText("Completed with Errors")).toBeInTheDocument();
        const finalResponse = onUploadComplete.mock.calls[0][0];
        expect(finalResponse.overallSuccess).toBe(false);
        expect(finalResponse.message).not.toBe("Upload completed successfully");
        expect(finalResponse.fileResults).toEqual([
            expect.objectContaining({ relativeKey: KEY, success: false, error: REFUSAL }),
        ]);
    });

    it("fails the file instead of sending a completion that leaves out an uploaded part", async () => {
        // The init response promises two parts for a file the page split into one, so only one part
        // is ever uploaded and recorded. Sending that would complete the file without part 2.
        service.initializeUpload.mockResolvedValue(initResponse(2));
        service.uploadPart.mockResolvedValue("etag-1");

        const onUploadComplete = mount();

        await waitFor(() =>
            expect(
                screen.getByText("Failed: Not completed: 1 of 2 uploaded parts recorded")
            ).toBeInTheDocument()
        );
        expect(service.completeUpload).not.toHaveBeenCalled();
        // The completion step fails visibly and offers its retry; nothing reports success.
        expect(screen.getByText("Retry Failed Completion")).toBeInTheDocument();
        expect(screen.getByText(/has 1 of 2 uploaded parts recorded/)).toBeInTheDocument();
        expect(onUploadComplete).not.toHaveBeenCalled();
    });
});

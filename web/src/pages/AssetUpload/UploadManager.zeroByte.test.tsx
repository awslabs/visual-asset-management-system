/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Zero-byte files at completion (#787).
 *
 * A zero-byte file has no part to upload, so the part upload never reports its sequence finished.
 * An upload whose files were all empty therefore never sent its completion request and stayed In
 * Progress: the page completed "every sequence" from the sequence list its upload callback had
 * closed over when the upload started, which was still empty. A sequence of empty files inside a
 * larger upload (here: an empty model beside a non-empty preview of it, which uploads in its own
 * sequence) had no part either and was never completed. The upload that continues after a skipped or
 * retried metadata or links step had the same stale list.
 *
 * The real UploadManager runs against a mocked service layer, as in UploadManager.completion.test.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import UploadManager from "./UploadManager";
import AssetUploadService from "../../services/AssetUploadService";

jest.mock("../../services/AssetUploadService", () => ({
    __esModule: true,
    default: {
        initializeUpload: jest.fn(),
        uploadPart: jest.fn(),
        completeUpload: jest.fn(),
        getUploadStatus: jest.fn(),
    },
}));
// Asset creation, metadata and links: used only by the test that creates a new asset.
const mockAssetOperations = {
    createAsset: jest.fn(),
    addMetadata: jest.fn(),
    createAssetLinks: jest.fn(),
};
jest.mock("./hooks/useAssetOperations", () => ({
    useAssetOperations: () => mockAssetOperations,
}));

const service = AssetUploadService as unknown as {
    initializeUpload: jest.Mock;
    uploadPart: jest.Mock;
    completeUpload: jest.Mock;
    getUploadStatus: jest.Mock;
};

function fileItem(index: number, relativePath: string, content: string) {
    const name = relativePath.split("/").pop() as string;
    return {
        index,
        name,
        relativePath,
        size: content.length,
        handle: new File([content], name),
        status: "Queued",
        progress: 0,
        loaded: 0,
        total: content.length,
    } as any;
}

/**
 * The backend's init answer: a zero-byte file gets no part URLs and the `zero-byte` S3 upload id,
 * any other file one part URL per requested part. Each init call is its own upload.
 */
function answerInit() {
    let calls = 0;
    service.initializeUpload.mockImplementation(async (request: any) => {
        calls += 1;
        return {
            uploadId: `upload-${calls}`,
            message: "",
            files: request.files.map((f: any) => ({
                relativeKey: f.relativeKey,
                uploadIdS3: f.num_parts === 0 ? "zero-byte" : `s3-${f.relativeKey}`,
                numParts: f.num_parts,
                partUploadUrls: Array.from({ length: f.num_parts }, (_, i) => ({
                    PartNumber: i + 1,
                    UploadUrl: `https://parts.example${f.relativeKey}/${i + 1}`,
                })),
            })),
        };
    });
}

/** The backend's completion answer: every file in the request stored. */
function answerCompletion() {
    service.completeUpload.mockImplementation(async (uploadId: string, request: any) => ({
        message: "done",
        uploadId,
        assetId: "asset-1",
        overallSuccess: true,
        fileResults: request.files.map((f: any) => ({
            relativeKey: f.relativeKey,
            uploadIdS3: f.uploadIdS3,
            success: true,
        })),
    }));
}

function mount(fileItems: any[]) {
    const onUploadComplete = jest.fn();
    render(
        <UploadManager
            assetDetail={{ assetId: "asset-1", databaseId: "db-1" } as any}
            metadata={{}}
            fileItems={fileItems}
            onUploadComplete={onUploadComplete}
            onError={jest.fn()}
            isExistingAsset={true}
        />
    );
    return onUploadComplete;
}

/** A new asset with metadata, so the page creates the asset and adds the metadata first. */
function mountNewAsset(fileItems: any[]) {
    const onUploadComplete = jest.fn();
    render(
        <UploadManager
            assetDetail={{ assetName: "new-asset", databaseId: "db-1" } as any}
            metadata={{ owner: { value: "team-a", type: "string" } } as any}
            fileItems={fileItems}
            onUploadComplete={onUploadComplete}
            onError={jest.fn()}
            isExistingAsset={false}
        />
    );
    return onUploadComplete;
}

/** Every file key the completion requests sent, with the parts each carried. */
function completedFiles() {
    return service.completeUpload.mock.calls.flatMap(([, request]: any[]) => request.files);
}

describe("UploadManager zero-byte files", () => {
    beforeEach(() => {
        jest.resetAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "warn").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
        answerInit();
        answerCompletion();
        service.uploadPart.mockResolvedValue("etag-1");
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("completes an upload of only zero-byte files", async () => {
        const onUploadComplete = mount([
            fileItem(0, "/empty-a.txt", ""),
            fileItem(1, "/folder/empty-b.txt", ""),
        ]);

        await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));
        expect(service.uploadPart).not.toHaveBeenCalled();
        expect(service.completeUpload).toHaveBeenCalledTimes(1);
        expect(service.completeUpload.mock.calls[0][0]).toBe("upload-1");
        expect(completedFiles()).toEqual([
            { relativeKey: "/empty-a.txt", uploadIdS3: "zero-byte", parts: [] },
            { relativeKey: "/folder/empty-b.txt", uploadIdS3: "zero-byte", parts: [] },
        ]);
        expect(onUploadComplete.mock.calls[0][0]).toMatchObject({
            overallSuccess: true,
            noFilesStored: false,
            message: "Upload completed successfully",
        });
    });

    it("completes zero-byte and non-empty files that share a sequence", async () => {
        const onUploadComplete = mount([
            fileItem(0, "/empty.txt", ""),
            fileItem(1, "/model.glb", "abcd"),
        ]);

        await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));
        expect(service.uploadPart).toHaveBeenCalledTimes(1);
        expect(service.completeUpload).toHaveBeenCalledTimes(1);
        expect(completedFiles()).toEqual([
            { relativeKey: "/empty.txt", uploadIdS3: "zero-byte", parts: [] },
            {
                relativeKey: "/model.glb",
                uploadIdS3: "s3-/model.glb",
                parts: [{ PartNumber: 1, ETag: "etag-1" }],
            },
        ]);
        expect(onUploadComplete.mock.calls[0][0]).toMatchObject({ overallSuccess: true });
    });

    it("completes a sequence of zero-byte files beside a sequence that has parts", async () => {
        // A preview file uploads in a sequence of its own, after the regular files' sequence. Here
        // the regular sequence holds only the empty model, so it has no part to upload.
        const onUploadComplete = mount([
            fileItem(0, "/model.glb", ""),
            fileItem(1, "/model.glb.previewFile.png", "png!"),
        ]);

        await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));
        expect(service.initializeUpload).toHaveBeenCalledTimes(2);
        expect(service.completeUpload).toHaveBeenCalledTimes(2);
        // The regular sequence completes before the preview sequence, which waits for it.
        expect(service.completeUpload.mock.calls.map(([uploadId]: any[]) => uploadId)).toEqual([
            "upload-1",
            "upload-2",
        ]);
        expect(completedFiles()).toEqual([
            { relativeKey: "/model.glb", uploadIdS3: "zero-byte", parts: [] },
            {
                relativeKey: "/model.glb.previewFile.png",
                uploadIdS3: "s3-/model.glb.previewFile.png",
                parts: [{ PartNumber: 1, ETag: "etag-1" }],
            },
        ]);
        expect(onUploadComplete.mock.calls[0][0]).toMatchObject({ overallSuccess: true });
    });

    it("completes an upload of only zero-byte files after skipping a failed metadata step", async () => {
        // Skipping (or retrying) a failed metadata or links step continues the upload through a
        // second code path (continueToFileUpload), which read the same stale sequence list: here
        // the empty initial one, because no upload had started.
        mockAssetOperations.createAsset.mockResolvedValue({ assetId: "asset-1" });
        mockAssetOperations.addMetadata.mockRejectedValue(new Error("Metadata refused"));

        const onUploadComplete = mountNewAsset([fileItem(0, "/empty.txt", "")]);

        fireEvent.click(await screen.findByRole("button", { name: "Skip and Continue" }));

        await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));
        expect(service.initializeUpload).toHaveBeenCalledTimes(1);
        expect(service.completeUpload).toHaveBeenCalledTimes(1);
        expect(completedFiles()).toEqual([
            { relativeKey: "/empty.txt", uploadIdS3: "zero-byte", parts: [] },
        ]);
        expect(onUploadComplete.mock.calls[0][0]).toMatchObject({
            assetId: "asset-1",
            overallSuccess: true,
        });
    });

    it("completes an upload of only non-empty files once", async () => {
        const onUploadComplete = mount([
            fileItem(0, "/a.glb", "abcd"),
            fileItem(1, "/folder/b.glb", "efgh"),
        ]);

        await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));
        expect(service.uploadPart).toHaveBeenCalledTimes(2);
        expect(service.completeUpload).toHaveBeenCalledTimes(1);
        expect(completedFiles()).toEqual([
            {
                relativeKey: "/a.glb",
                uploadIdS3: "s3-/a.glb",
                parts: [{ PartNumber: 1, ETag: "etag-1" }],
            },
            {
                relativeKey: "/folder/b.glb",
                uploadIdS3: "s3-/folder/b.glb",
                parts: [{ PartNumber: 1, ETag: "etag-1" }],
            },
        ]);
        expect(onUploadComplete.mock.calls[0][0]).toMatchObject({ overallSuccess: true });
    });
});

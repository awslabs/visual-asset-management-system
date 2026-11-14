/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import AssetIngestion from "./AssetIngestion";
import { ingestAsset as ingestAssetService } from "../../services/APIService";
import AssetUploadServiceDefault from "../../services/AssetUploadService";

jest.mock("../../services/APIService", () => ({
    ingestAsset: jest.fn(),
}));
jest.mock("../../services/AssetUploadService", () => ({
    __esModule: true,
    default: { uploadPart: jest.fn() },
}));
jest.mock("../../common/utils/utils", () => ({
    generateUUID: () => "asset-1",
}));

const ingestAsset = ingestAssetService as jest.Mock;
const AssetUploadService = AssetUploadServiceDefault as unknown as { uploadPart: jest.Mock };

const ASSET_ID = "iasset-1";
const JSON_BODY = JSON.stringify({
    databaseId: "db-1",
    assetName: "Model",
    description: "A model to ingest",
});

/** The initialize response for one file of `numParts` parts. */
function initializeResponse(fileName: string, numParts: number) {
    return {
        message: "Upload initialized successfully",
        uploadId: "y-upload-1",
        files: [
            {
                relativeKey: `${ASSET_ID}/${fileName}`,
                uploadIdS3: "s3-upload-1",
                numParts,
                partUploadUrls: Array.from({ length: numParts }, (_, i) => ({
                    PartNumber: i + 1,
                    UploadUrl: `https://bucket.example/part${i + 1}`,
                })),
            },
        ],
    };
}

function completeResponse(fileName: string, overrides: Record<string, unknown> = {}) {
    return {
        message: "Multipart upload and asset ingestion completed successfully.",
        uploadId: "y-upload-1",
        assetId: ASSET_ID,
        fileResults: [
            { relativeKey: `${ASSET_ID}/${fileName}`, uploadIdS3: "s3-upload-1", success: true },
        ],
        overallSuccess: true,
        largeFileAsynchronousHandling: false,
        ...overrides,
    };
}

/** Render the page, choose `file`, enter the asset JSON and press Upload. */
function submit(file: File) {
    const { container } = render(<AssetIngestion />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.change(container.querySelector("textarea") as HTMLTextAreaElement, {
        target: { value: JSON_BODY },
    });
    fireEvent.click(screen.getByRole("button", { name: /Upload Asset/ }));
}

describe("AssetIngestion", () => {
    beforeEach(() => jest.clearAllMocks());

    it("initializes, uploads every part and completes with the parts' ETags", async () => {
        const file = new File(["0123456789"], "model.glb");
        ingestAsset
            .mockResolvedValueOnce([true, initializeResponse("model.glb", 2)])
            .mockResolvedValueOnce([true, completeResponse("model.glb")]);
        AssetUploadService.uploadPart
            .mockResolvedValueOnce("etag-1")
            .mockResolvedValueOnce("etag-2");

        submit(file);

        expect(await screen.findByText("Asset uploaded successfully")).toBeInTheDocument();
        expect(
            screen.getByText("Multipart upload and asset ingestion completed successfully.")
        ).toBeInTheDocument();

        expect(ingestAsset).toHaveBeenCalledTimes(2);
        expect(ingestAsset.mock.calls[0][0]).toMatchObject({
            databaseId: "db-1",
            assetId: ASSET_ID,
            files: [{ relativeKey: `${ASSET_ID}/model.glb`, file_size: 10 }],
        });
        expect(AssetUploadService.uploadPart.mock.calls.map((c: any[]) => c[0])).toEqual([
            "https://bucket.example/part1",
            "https://bucket.example/part2",
        ]);
        expect(ingestAsset.mock.calls[1][0]).toMatchObject({
            databaseId: "db-1",
            assetId: ASSET_ID,
            uploadId: "y-upload-1",
            files: [
                {
                    relativeKey: `${ASSET_ID}/model.glb`,
                    uploadIdS3: "s3-upload-1",
                    parts: [
                        { PartNumber: 1, ETag: "etag-1" },
                        { PartNumber: 2, ETag: "etag-2" },
                    ],
                },
            ],
        });
        // The button is usable again for the next ingest.
        expect(screen.getByRole("button", { name: /Upload Asset/ })).not.toHaveAttribute(
            "aria-disabled",
            "true"
        );
    });

    it("reports a refused initialize with the server's message", async () => {
        ingestAsset.mockResolvedValueOnce([
            false,
            "VAMS General Error: Error initializing upload: VAMS General Error: Files contain an unsupported file extension",
        ]);

        submit(new File(["x"], "model.glb"));

        expect(await screen.findByText("Upload failed")).toBeInTheDocument();
        expect(
            screen.getByText(
                "The upload could not be started: Error initializing upload: Files contain an unsupported file extension"
            )
        ).toBeInTheDocument();
        expect(AssetUploadService.uploadPart).not.toHaveBeenCalled();
        expect(ingestAsset).toHaveBeenCalledTimes(1);
    });

    it("reports a failed part and does not complete", async () => {
        ingestAsset.mockResolvedValueOnce([true, initializeResponse("model.glb", 2)]);
        AssetUploadService.uploadPart
            .mockResolvedValueOnce("etag-1")
            .mockRejectedValueOnce(new Error("Failed to upload part: 403 Forbidden"));

        submit(new File(["0123456789"], "model.glb"));

        expect(
            await screen.findByText(
                "Part 2 of 2 could not be uploaded: Failed to upload part: 403 Forbidden"
            )
        ).toBeInTheDocument();
        expect(ingestAsset).toHaveBeenCalledTimes(1);
    });

    it("reports a refused completion with the server's message", async () => {
        ingestAsset
            .mockResolvedValueOnce([true, initializeResponse("model.glb", 1)])
            .mockResolvedValueOnce([
                false,
                "VAMS General Error: VAMS General Error: Error completing upload: Upload has expired",
            ]);
        AssetUploadService.uploadPart.mockResolvedValueOnce("etag-1");

        submit(new File(["x"], "model.glb"));

        expect(
            await screen.findByText(
                "The upload could not be completed: Error completing upload: Upload has expired"
            )
        ).toBeInTheDocument();
    });

    it("reports a file the completion rejected as not stored", async () => {
        ingestAsset
            .mockResolvedValueOnce([true, initializeResponse("model.exe", 1)])
            .mockResolvedValueOnce([
                true,
                completeResponse("model.exe", {
                    overallSuccess: false,
                    fileResults: [
                        {
                            relativeKey: `${ASSET_ID}/model.exe`,
                            success: false,
                            error: "File extension not allowed",
                        },
                    ],
                }),
            ]);
        AssetUploadService.uploadPart.mockResolvedValueOnce("etag-1");

        submit(new File(["x"], "model.exe"));

        expect(
            await screen.findByText(
                `Upload was rejected and is not stored — ${ASSET_ID}/model.exe: File extension not allowed`
            )
        ).toBeInTheDocument();
        expect(screen.queryByText("Asset uploaded successfully")).not.toBeInTheDocument();
    });

    it("reports an initialize response without the file's upload details", async () => {
        ingestAsset.mockResolvedValueOnce([true, { message: "Upload initialized successfully" }]);

        submit(new File(["x"], "model.glb"));

        expect(
            await screen.findByText("The server did not return the upload details for the file.")
        ).toBeInTheDocument();
        await waitFor(() => expect(ingestAsset).toHaveBeenCalledTimes(1));
    });
});

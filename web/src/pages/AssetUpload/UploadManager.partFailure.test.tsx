/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the file table shows for a file whose part failed every automatic retry.
 *
 * The part's failure reached the row only as a progress update, and a row's status was taken from
 * its progress alone, so the file read Queued (or In Progress at the share of its other parts)
 * instead of Failed. It then had no row retry, and the table's "Retry N failed Items" was not
 * offered. The row now reads Failed with the part's error, and either retry re-sends the part.
 *
 * A Failed row must not end the upload while its part can still be retried: the page concludes
 * only when every sequence is completed, and the file reads Completed again once the retried part
 * uploads.
 *
 * The real UploadManager runs here against a mocked service layer.
 */

import React from "react";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
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
// One file per sequence; a 2-byte part size, so a 4-byte file has two parts; and one automatic
// retry, so a part fails every automatic attempt after a single 1 s backoff.
jest.mock("../../constants/uploadLimits", () => ({
    ...jest.requireActual("../../constants/uploadLimits"),
    MAX_FILES_PER_REQUEST: 1,
    MAX_PART_SIZE: 2,
    MAX_RETRY_ATTEMPTS: 1,
}));

const service = AssetUploadService as unknown as {
    initializeUpload: jest.Mock;
    uploadPart: jest.Mock;
    completeUpload: jest.Mock;
};

const PART_ERROR = "Request failed with status code 503";
// The first try and the one automatic retry.
const AUTOMATIC_ATTEMPTS = 2;
// Long enough for the automatic retry's 1 s backoff; each test may wait for it more than once.
const AFTER_RETRIES = { timeout: 4000 };
const TEST_TIMEOUT = 20000;

function fileItem(index: number, name: string, size: number) {
    return {
        index,
        name,
        relativePath: `/${name}`,
        size,
        handle: new File(["a".repeat(size)], name),
        status: "Queued",
        progress: 0,
        loaded: 0,
        total: size,
    } as any;
}

/** Init answers each file with the part URLs it asked for; the upload id names the file. */
function initFor(request: { files: { relativeKey: string; num_parts: number }[] }) {
    return {
        uploadId: `upload${request.files[0].relativeKey}`,
        message: "",
        files: request.files.map((f) => {
            const numParts = f.num_parts;
            return {
                relativeKey: f.relativeKey,
                uploadIdS3: `s3${f.relativeKey}`,
                numParts,
                partUploadUrls: Array.from({ length: numParts }, (_, i) => ({
                    PartNumber: i + 1,
                    UploadUrl: `https://parts.example${f.relativeKey}/${i + 1}`,
                })),
            };
        }),
    };
}

/** Completion stores every file it is sent. */
function completionFor(uploadId: string, request: { files: { relativeKey: string }[] }) {
    return {
        message: "done",
        uploadId,
        assetId: "asset-1",
        overallSuccess: true,
        fileResults: request.files.map((f) => ({
            relativeKey: f.relativeKey,
            uploadIdS3: "",
            success: true,
        })),
    };
}

/**
 * Part uploads: the part at `failingUrl` fails its first `failures` attempts, then uploads; the
 * part at `heldUrl`, if given, uploads once `release()` is called; every other part uploads at
 * once. `failing` counts the attempts of the failing part.
 */
function partUploads(failingUrl: string, failures: number, heldUrl?: string) {
    const uploads = { failing: 0, release: () => undefined as void };
    const held = new Promise<void>((resolve) => {
        uploads.release = resolve;
    });
    service.uploadPart.mockImplementation(async (url: string) => {
        if (url === failingUrl) {
            uploads.failing++;
            if (uploads.failing <= failures) {
                throw new Error(PART_ERROR);
            }
        }
        if (url === heldUrl) {
            await held;
        }
        return `etag${url.slice("https://parts.example".length)}`;
    });
    return uploads;
}

function mount(items: any[]) {
    const onUploadComplete = jest.fn();
    render(
        <UploadManager
            assetDetail={{ assetId: "asset-1", databaseId: "db-1" } as any}
            metadata={{}}
            fileItems={items}
            onUploadComplete={onUploadComplete}
            onError={jest.fn()}
            isExistingAsset={true}
        />
    );
    return onUploadComplete;
}

/** The file table row of the named file; the table shows a file by its path. */
function row(name: string): HTMLElement {
    const cell = screen.getAllByText(`/${name}`)[0];
    const tableRow = cell.closest("tr");
    if (!tableRow) {
        throw new Error(`No table row for ${name}`);
    }
    return tableRow as HTMLElement;
}

/** Nothing on the page says the upload has ended. */
function expectNotConcluded(onUploadComplete: jest.Mock) {
    expect(onUploadComplete).not.toHaveBeenCalled();
    expect(screen.queryByText("Upload failed")).not.toBeInTheDocument();
    expect(screen.queryByText("Completed with Errors")).not.toBeInTheDocument();
}

describe("UploadManager part failure", () => {
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

    it(
        "shows the file Failed with the part's error, offers its retries, and keeps the upload open",
        async () => {
            const attempts = partUploads("https://parts.example/part.txt/1", AUTOMATIC_ATTEMPTS);

            const onUploadComplete = mount([fileItem(0, "part.txt", 1)]);

            await waitFor(
                () =>
                    expect(within(row("part.txt")).getByText(`Failed: ${PART_ERROR}`)).toBeTruthy(),
                AFTER_RETRIES
            );
            expect(attempts.failing).toBe(AUTOMATIC_ATTEMPTS);

            // Every retry the page has is offered for it.
            expect(
                await screen.findByRole("button", { name: "Retry part.txt" })
            ).toBeInTheDocument();
            expect(
                screen.getByRole("button", { name: "Retry 1 failed Items" })
            ).toBeInTheDocument();
            expect(
                screen.getByRole("button", { name: "Retry Failed Uploads" })
            ).toBeInTheDocument();

            // It was the only file and it failed, but its part can be retried: the upload is not
            // over, so nothing says it failed or was completed with errors.
            expect(service.completeUpload).not.toHaveBeenCalled();
            expectNotConcluded(onUploadComplete);

            // The page-level retry still re-sends the part and the upload completes.
            fireEvent.click(screen.getByRole("button", { name: "Retry Failed Uploads" }));
            await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));
            expect(attempts.failing).toBe(AUTOMATIC_ATTEMPTS + 1);
            expect(onUploadComplete.mock.calls[0][0]).toMatchObject({
                overallSuccess: true,
                noFilesStored: false,
                message: "Upload completed successfully",
                fileResults: [],
            });
            expect(within(row("part.txt")).getByText("Completed")).toBeTruthy();
        },
        TEST_TIMEOUT
    );

    it(
        "re-sends the failed part from the row retry and completes the file with every part",
        async () => {
            // Part 1 uploads; part 2 fails every automatic attempt, and its manual retry is held
            // until the test releases it.
            const PART_TWO = "https://parts.example/model.glb/2";
            const uploads = partUploads(PART_TWO, AUTOMATIC_ATTEMPTS, PART_TWO);

            const onUploadComplete = mount([fileItem(0, "model.glb", 4)]);

            // Half the file uploaded, but a part failed: the row reads Failed, not In Progress.
            await waitFor(
                () =>
                    expect(
                        within(row("model.glb")).getByText(`Failed: ${PART_ERROR}`)
                    ).toBeTruthy(),
                AFTER_RETRIES
            );
            expectNotConcluded(onUploadComplete);

            fireEvent.click(await screen.findByRole("button", { name: "Retry model.glb" }));

            // While the part is re-sent the row is back In Progress, with no error and no retry.
            await waitFor(() => expect(uploads.failing).toBe(AUTOMATIC_ATTEMPTS + 1));
            expect(within(row("model.glb")).getByText("In Progress")).toBeTruthy();
            expect(
                screen.queryByRole("button", { name: "Retry model.glb" })
            ).not.toBeInTheDocument();
            expectNotConcluded(onUploadComplete);

            uploads.release();

            // The completion lists both parts with their ETags, and the upload completed.
            await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));
            expect(service.completeUpload).toHaveBeenCalledTimes(1);
            expect(service.completeUpload.mock.calls[0][1].files).toEqual([
                expect.objectContaining({
                    relativeKey: "/model.glb",
                    parts: [
                        { PartNumber: 1, ETag: "etag/model.glb/1" },
                        { PartNumber: 2, ETag: "etag/model.glb/2" },
                    ],
                }),
            ]);
            expect(onUploadComplete.mock.calls[0][0]).toMatchObject({
                overallSuccess: true,
                message: "Upload completed successfully",
                fileResults: [],
            });
            expect(within(row("model.glb")).getByText("Completed")).toBeTruthy();
        },
        TEST_TIMEOUT
    );

    it(
        "keeps the upload open while another file completes, and the table retry finishes it",
        async () => {
            const attempts = partUploads("https://parts.example/part.txt/1", AUTOMATIC_ATTEMPTS);

            const onUploadComplete = mount([
                fileItem(0, "part.txt", 1),
                fileItem(1, "other.txt", 1),
            ]);

            await waitFor(
                () =>
                    expect(within(row("part.txt")).getByText(`Failed: ${PART_ERROR}`)).toBeTruthy(),
                AFTER_RETRIES
            );
            // The other file's sequence completed on its own ...
            await waitFor(() => expect(service.completeUpload).toHaveBeenCalledTimes(1));
            expect(service.completeUpload.mock.calls[0][0]).toBe("upload/other.txt");
            expect(within(row("other.txt")).getByText("Completed")).toBeTruthy();
            // ... but the failed file's part can still be retried, so the upload is not over.
            expectNotConcluded(onUploadComplete);

            fireEvent.click(await screen.findByRole("button", { name: "Retry 1 failed Items" }));

            await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));
            expect(attempts.failing).toBe(AUTOMATIC_ATTEMPTS + 1);
            expect(service.completeUpload).toHaveBeenCalledWith(
                "upload/part.txt",
                expect.objectContaining({
                    files: [
                        expect.objectContaining({
                            relativeKey: "/part.txt",
                            parts: [{ PartNumber: 1, ETag: "etag/part.txt/1" }],
                        }),
                    ],
                })
            );
            // The file that was Failed while its part waited for the retry is not reported failed.
            expect(onUploadComplete.mock.calls[0][0]).toMatchObject({
                overallSuccess: true,
                message: "Upload completed successfully",
                fileResults: [],
            });
            expect(screen.queryByText("Completed with Errors")).not.toBeInTheDocument();
        },
        TEST_TIMEOUT
    );

    it(
        "offers the part retry only once the other parts have finished uploading",
        async () => {
            // The failed file's part fails every automatic attempt while the other file's part
            // is still being sent. A re-upload started then would run a second queue over the
            // parts not yet sent.
            const uploads = partUploads(
                "https://parts.example/part.txt/1",
                AUTOMATIC_ATTEMPTS,
                "https://parts.example/other.txt/1"
            );

            mount([fileItem(0, "part.txt", 1), fileItem(1, "other.txt", 1)]);

            await waitFor(
                () =>
                    expect(within(row("part.txt")).getByText(`Failed: ${PART_ERROR}`)).toBeTruthy(),
                AFTER_RETRIES
            );
            expect(
                screen.queryByRole("button", { name: "Retry part.txt" })
            ).not.toBeInTheDocument();
            expect(screen.queryByRole("button", { name: /failed Items/ })).not.toBeInTheDocument();

            uploads.release();

            expect(
                await screen.findByRole("button", { name: "Retry part.txt" })
            ).toBeInTheDocument();
            expect(
                screen.getByRole("button", { name: "Retry 1 failed Items" })
            ).toBeInTheDocument();
        },
        TEST_TIMEOUT
    );
});

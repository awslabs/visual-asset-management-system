/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the upload page says when an upload stores no file.
 *
 * The server validates each file at completion and deletes what it refuses. When the only file
 * was refused, the Upload Completion step read "Completed with Errors" and the summary read
 * "Upload Completed with Some Failures" ("Some files have been added ..."), although nothing was
 * added. An upload that stores no file reads "Upload failed" on both; an upload that stores some
 * of its files keeps the "with errors" wording.
 *
 * The real AssetUploadWorkflow and UploadManager run here against a mocked service layer.
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import AssetUploadWorkflow from "./AssetUploadWorkflow";
import AssetUploadService from "../../services/AssetUploadService";

jest.mock("../../services/AssetUploadService", () => ({
    __esModule: true,
    default: {
        initializeUpload: jest.fn(),
        uploadPart: jest.fn(),
        completeUpload: jest.fn(),
    },
}));
const mockCreateAsset = jest.fn();
jest.mock("./hooks/useAssetOperations", () => ({
    useAssetOperations: () => ({
        createAsset: mockCreateAsset,
        addMetadata: jest.fn(),
        createAssetLinks: jest.fn(),
    }),
}));
// One file per sequence, so one file can fail while another is stored; and no automatic retries.
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

/** The server refuses this file at completion. */
const REFUSED_KEY = "/model.glb";
/** The server stores this file. */
const STORED_KEY = "/notes.txt";
/** Init promises two parts for this 4-byte file, so its completion is stopped before it is sent. */
const STOPPED_KEY = "/scan.e57";
const REFUSAL = "Error verifying base file for preview file";

const EXISTING_ASSET = { assetId: "asset-1", databaseId: "db-1" };
const NEW_ASSET = { databaseId: "db-1", assetName: "model" };

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

function initFor(request: { files: { relativeKey: string }[] }) {
    return {
        uploadId: `upload${request.files[0].relativeKey}`,
        message: "",
        files: request.files.map((f) => {
            const numParts = f.relativeKey === STOPPED_KEY ? 2 : 1;
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

function mount(keys: string[], isExistingAsset: boolean) {
    const { container } = render(
        <MemoryRouter>
            <AssetUploadWorkflow
                assetDetail={(isExistingAsset ? EXISTING_ASSET : NEW_ASSET) as any}
                metadata={{}}
                fileItems={keys.map((key, index) => fileItem(index, key))}
                onComplete={jest.fn()}
                onCancel={jest.fn()}
                isExistingAsset={isExistingAsset}
            />
        </MemoryRouter>
    );
    return createWrapper(container);
}

type PageWrapper = ReturnType<typeof createWrapper>;

/** The status indicator, step or file row, whose whole text is `text`. */
function indicator(wrapper: PageWrapper, text: string) {
    return wrapper
        .findAllStatusIndicators()
        .find((s) => s.getElement().textContent?.trim() === text);
}

function alertWithHeader(wrapper: PageWrapper, header: string) {
    return wrapper
        .findAllAlerts()
        .find((a) => a.findHeader()?.getElement().textContent?.trim() === header);
}

/** Waits for the summary alert with `header` and returns it. */
async function summaryAlert(wrapper: PageWrapper, header: string) {
    return waitFor(
        () => {
            const alert = alertWithHeader(wrapper, header);
            if (!alert) {
                throw new Error(`No alert with the header "${header}"`);
            }
            return alert;
        },
        { timeout: 3000 }
    );
}

function isErrorAlert(alert: { getElement(): Element }) {
    return alert.getElement().querySelector('[class*="type-error"]') !== null;
}

const NOTHING_STORED_BODY = {
    existing: "No files were added to asset asset-1. See the error details below.",
    new: "Asset asset-new was created, but none of its files were stored. See the error details below.",
};

describe("Upload outcome wording", () => {
    beforeEach(() => {
        jest.resetAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "warn").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
        mockCreateAsset.mockResolvedValue({ assetId: "asset-new" });
        service.initializeUpload.mockImplementation(async (request) => initFor(request));
        service.uploadPart.mockResolvedValue("etag-1");
        service.completeUpload.mockImplementation(async (uploadId, request) =>
            completionFor(uploadId, request)
        );
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it.each([
        ["an existing asset", true, NOTHING_STORED_BODY.existing],
        ["a new asset", false, NOTHING_STORED_BODY.new],
    ])(
        "reads Upload failed when the only file is refused at completion, for %s",
        async (_label, isExistingAsset, body) => {
            const wrapper = mount([REFUSED_KEY], isExistingAsset);

            const alert = await summaryAlert(wrapper, "Upload failed");
            expect(isErrorAlert(alert)).toBe(true);
            expect(alert.findContent().getElement().textContent?.trim()).toBe(body);

            const step = indicator(wrapper, "Upload failed");
            expect(step).toBeTruthy();
            expect(step?.getElement().className).toMatch(/status-error/);

            expect(screen.queryByText("Completed with Errors")).not.toBeInTheDocument();
            expect(
                screen.queryByText("Upload Completed with Some Failures")
            ).not.toBeInTheDocument();
            // Nothing can be retried: no completion sequence failed and every part uploaded.
            expect(
                screen.queryByRole("button", { name: "Retry Failed Completion" })
            ).not.toBeInTheDocument();
            expect(screen.queryByRole("button", { name: /failed Items/ })).not.toBeInTheDocument();
            // The file and the server's reason are still listed.
            expect(
                alertWithHeader(wrapper, "Failed File Details")?.getElement().textContent
            ).toContain(`${REFUSED_KEY}: ${REFUSAL}`);
        }
    );

    it.each([
        ["an existing asset", true],
        ["a new asset", false],
    ])(
        "keeps Completed with Errors when one file is refused and another is stored, for %s",
        async (_label, isExistingAsset) => {
            // Paired arm: some of the upload was stored, so it did not fail as a whole.
            const wrapper = mount([REFUSED_KEY, STORED_KEY], isExistingAsset);

            const alert = await summaryAlert(wrapper, "Upload Completed with Some Failures");
            expect(isErrorAlert(alert)).toBe(false);

            const step = indicator(wrapper, "Completed with Errors");
            expect(step).toBeTruthy();
            expect(step?.getElement().className).toMatch(/status-warning/);

            expect(screen.queryByText("Upload failed")).not.toBeInTheDocument();
            expect(
                alertWithHeader(wrapper, "Failed File Details")?.getElement().textContent
            ).toContain(`${REFUSED_KEY}: ${REFUSAL}`);
        }
    );

    it("reads Upload failed when the only completion sequence is skipped", async () => {
        const wrapper = mount([STOPPED_KEY], true);

        fireEvent.click(
            await screen.findByRole("button", { name: "Skip and Continue" }, { timeout: 3000 })
        );

        const alert = await summaryAlert(wrapper, "Upload failed");
        expect(isErrorAlert(alert)).toBe(true);
        expect(indicator(wrapper, "Upload failed")).toBeTruthy();
        expect(screen.queryByText("Completed with Errors")).not.toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Retry Failed Completion" })
        ).not.toBeInTheDocument();
        expect(service.completeUpload).not.toHaveBeenCalled();
    });

    it("keeps Completed with Errors when a skipped sequence's file fails and another is stored", async () => {
        // Paired arm for the skip above.
        const wrapper = mount([STOPPED_KEY, STORED_KEY], true);

        fireEvent.click(
            await screen.findByRole("button", { name: "Skip and Continue" }, { timeout: 3000 })
        );

        await summaryAlert(wrapper, "Upload Completed with Some Failures");
        expect(indicator(wrapper, "Completed with Errors")).toBeTruthy();
        expect(screen.queryByText("Upload failed")).not.toBeInTheDocument();
    });

    it("reads Upload failed in the summary when upload initialization is skipped", async () => {
        service.initializeUpload.mockRejectedValue(new Error("Forbidden"));
        const wrapper = mount([STORED_KEY], true);

        fireEvent.click(
            await screen.findByRole("button", { name: "Skip and Continue" }, { timeout: 3000 })
        );

        const alert = await summaryAlert(wrapper, "Upload failed");
        expect(isErrorAlert(alert)).toBe(true);
        expect(alert.findContent().getElement().textContent?.trim()).toBe(
            NOTHING_STORED_BODY.existing
        );
        expect(screen.queryByText("Upload Completed with Some Failures")).not.toBeInTheDocument();
        expect(service.completeUpload).not.toHaveBeenCalled();
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * D166: with `app.compliance.quarantineBlocksDownload` on, every file route answers a quarantined
 * asset `400` "Asset is quarantined and cannot be downloaded", and the file page said only "Unable to
 * load this image asset file." while the API's reason went to the console. Every surface that loads
 * file bytes for display now shows the API's own refusal message in place of its generic text,
 * decided by what the API answered alone (no compliance state is read in the browser); every other
 * failure keeps its text.
 */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import {
    apiRefusalMessage,
    describeStreamFailure,
    downloadFailureMessage,
    downloadRefusalReason,
    downloadStatusMessage,
    DOWNLOAD_FORBIDDEN_MESSAGE,
    streamResponseFailureMessage,
} from "./streamFailure";
import ImageViewerComponent from "../viewers/ImageViewerPlugin/ImageViewerComponent";
import AssetPreviewThumbnail from "../../components/filemanager/components/AssetPreviewThumbnail";
import FilePreviewThumbnail from "../../components/filemanager/components/FilePreviewThumbnail";
import { PreviewThumbnailCell } from "../../components/search/SearchPreviewThumbnail/PreviewThumbnailCell";
import {
    QUARANTINE_REFUSAL_BODY,
    QUARANTINE_REFUSAL_MESSAGE,
    QUARANTINE_REFUSED_DOWNLOAD,
    quarantineRefusalResponse,
} from "./quarantineRefusal.testdata";

const mockDownloadAsset = jest.fn();
jest.mock("../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
    fetchAsset: jest.fn(),
    fetchFileInfo: jest.fn(),
}));

/** The API's real refusal of a quarantined asset (D169: the backend tests pin this body). */
const QUARANTINED = QUARANTINE_REFUSAL_MESSAGE;
/** The tuple `downloadAsset` returns for it (APIService.quarantineRefusal.test.ts). */
const REFUSED = QUARANTINE_REFUSED_DOWNLOAD;

describe("the API's refusal reason", () => {
    it("is the API's message of a 4xx refusal the viewers do not word themselves", () => {
        expect(apiRefusalMessage(400, QUARANTINED)).toBe(QUARANTINED);
        expect(apiRefusalMessage(409, "  The file is being moved  ")).toBe(
            "The file is being moved"
        );
        expect(downloadRefusalReason(REFUSED)).toBe(QUARANTINED);
    });

    it("is nothing for any other failure, which keeps its own text", () => {
        expect(apiRefusalMessage(500, "Internal server error")).toBeUndefined();
        expect(apiRefusalMessage(undefined, QUARANTINED)).toBeUndefined();
        expect(apiRefusalMessage(400, "")).toBeUndefined();
        expect(apiRefusalMessage(400, undefined)).toBeUndefined();
        for (const status of [401, 403, 404, 410, 429]) {
            expect(apiRefusalMessage(status, "anything")).toBeUndefined();
        }
        expect(downloadRefusalReason([false, "HTTP 400: Bad Request", 400])).toBeNull();
        expect(downloadRefusalReason(false)).toBeNull();
    });

    it("replaces a viewer's generic sentence; the worded statuses keep theirs", () => {
        expect(downloadFailureMessage(REFUSED, "Failed to download file")).toBe(QUARANTINED);
        expect(
            downloadFailureMessage([false, "Not Authorized", 403, "Not Authorized"], "Failed")
        ).toBe(DOWNLOAD_FORBIDDEN_MESSAGE);
        expect(
            downloadFailureMessage([false, "boom", 500, "boom"], "Failed to download file")
        ).toBe("Failed to download file");
        expect(downloadFailureMessage([false, "denied"], "Failed to download file")).toBe(
            "Failed to download file"
        );
        expect(downloadStatusMessage(400, "Failed to load: a.glb (400)", QUARANTINED)).toBe(
            QUARANTINED
        );
        expect(downloadStatusMessage(400, "Failed to load: a.glb (400)")).toBe(
            "Failed to load: a.glb (400)"
        );
    });

    it("is read from a streamed response's body", async () => {
        // jsdom has no fetch Response; a stand-in with what the helper reads.
        const response = (status: number, json: () => Promise<any>) =>
            ({ status, clone: () => ({ json }) } as unknown as Response);
        expect(
            await streamResponseFailureMessage(
                quarantineRefusalResponse(),
                "Failed to load: a.glb (400)"
            )
        ).toBe(QUARANTINED);
        const failed = response(502, async () => {
            throw new SyntaxError("Unexpected token");
        });
        expect(await streamResponseFailureMessage(failed, "Failed to load: a.glb (502)")).toBe(
            "Failed to load: a.glb (502)"
        );
    });

    it("names a stream refusal as the stream viewers word it", () => {
        expect(
            describeStreamFailure("the point cloud preview", {
                status: 400,
                body: QUARANTINE_REFUSAL_BODY,
            })
        ).toBe(`Unable to load the point cloud preview: ${QUARANTINED}.`);
        expect(describeStreamFailure("the point cloud preview", { status: 400 })).toBe(
            "Unable to load the point cloud preview (HTTP 400)."
        );
    });
});

describe("the surfaces that load file bytes", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
    });
    afterEach(() => jest.restoreAllMocks());

    const viewer = { assetId: "a1", databaseId: "d1", assetKey: "/images/pic.svg" } as any;

    it("the Image Viewer shows the API's refusal, and its own sentence otherwise", async () => {
        mockDownloadAsset.mockResolvedValue(REFUSED);
        const { unmount } = render(<ImageViewerComponent {...viewer} />);
        expect(await screen.findByText(QUARANTINED)).toBeInTheDocument();
        expect(screen.queryByText(/Unable to load this image/)).not.toBeInTheDocument();
        unmount();

        mockDownloadAsset.mockResolvedValue([false, "Internal server error", 500]);
        render(<ImageViewerComponent {...viewer} />);
        expect(await screen.findByText(/Unable to load this image/)).toBeInTheDocument();
    });

    it("the asset and file preview thumbnails show the API's refusal", async () => {
        mockDownloadAsset.mockResolvedValue(REFUSED);
        render(
            <AssetPreviewThumbnail
                assetId="a1"
                databaseId="d1"
                previewKey="preview.png"
                onOpenFullPreview={() => undefined}
            />
        );
        expect(await screen.findByText(QUARANTINED)).toBeInTheDocument();
    });

    it("a preview thumbnail keeps its text for any other failure", async () => {
        mockDownloadAsset.mockResolvedValue([false, "Internal server error", 500]);
        render(
            <FilePreviewThumbnail
                assetId="a1"
                databaseId="d1"
                fileKey="/pic.png"
                onOpenFullPreview={() => undefined}
            />
        );
        expect(await screen.findByText("Preview not available")).toBeInTheDocument();
        expect(screen.queryByText(QUARANTINED)).not.toBeInTheDocument();
    });

    it("a search result's thumbnail shows the API's refusal, and nothing otherwise", async () => {
        mockDownloadAsset.mockResolvedValue(REFUSED);
        const { container, unmount } = render(
            <PreviewThumbnailCell
                assetId="a-refused"
                databaseId="d1"
                assetName="Refused"
                previewFileKey="preview.png"
                onOpenFullPreview={() => undefined}
            />
        );
        expect(await screen.findByText(QUARANTINED)).toBeInTheDocument();
        expect(container.querySelector(`[title="${QUARANTINED}"]`)).not.toBeNull();
        unmount();

        mockDownloadAsset.mockResolvedValue([false, "Internal server error", 500]);
        const other = render(
            <PreviewThumbnailCell
                assetId="a-failed"
                databaseId="d1"
                assetName="Failed"
                previewFileKey="preview2.png"
                onOpenFullPreview={() => undefined}
            />
        );
        await waitFor(() => expect(mockDownloadAsset).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(other.container.textContent).toBe(""));
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The sentence a viewer shows when its file download fails (#1211). It is decided only by the HTTP
 * status the API returned for the download (the `[false, message, status?]` tuple `downloadAsset`
 * resolves to, or the streaming route's response status): a refusal names the missing permission,
 * 410 and 404 keep their own sentences, and everything else keeps the viewer's generic wording.
 */
import {
    DOWNLOAD_ARCHIVED_MESSAGE,
    DOWNLOAD_FORBIDDEN_MESSAGE,
    DOWNLOAD_NOT_FOUND_MESSAGE,
    DOWNLOAD_UNAUTHENTICATED_MESSAGE,
    downloadFailureMessage,
    downloadResponseStatus,
    downloadStatusMessage,
} from "./streamFailure";

const GENERIC = "Failed to download PDF file";

describe("downloadFailureMessage", () => {
    it("names the missing permission for a 403 refusal", () => {
        expect(DOWNLOAD_FORBIDDEN_MESSAGE).toBe(
            "You do not have permission to view or download this file."
        );
        expect(downloadFailureMessage([false, "Not Authorized", 403], GENERIC)).toBe(
            DOWNLOAD_FORBIDDEN_MESSAGE
        );
    });

    it("tells a signed-out user to sign in again for a 401, not that permission is missing", () => {
        expect(DOWNLOAD_UNAUTHENTICATED_MESSAGE).toBe(
            "Your session has expired. Sign in again to view this file."
        );
        expect(downloadFailureMessage([false, "Unauthorized", 401], GENERIC)).toBe(
            DOWNLOAD_UNAUTHENTICATED_MESSAGE
        );
        expect(downloadFailureMessage([false, "Unauthorized", 401], GENERIC)).not.toBe(
            DOWNLOAD_FORBIDDEN_MESSAGE
        );
    });

    it("does not echo the API's denial reason", () => {
        const message = downloadFailureMessage(
            [false, "User is not authorized: constraint xyz", 403],
            GENERIC
        );
        expect(message).not.toContain("constraint");
    });

    it("keeps the archived and missing sentences", () => {
        expect(
            downloadFailureMessage(
                [false, "This file version has been archived and cannot be downloaded", 410],
                GENERIC
            )
        ).toBe(DOWNLOAD_ARCHIVED_MESSAGE);
        expect(downloadFailureMessage([false, "Not found", 404], GENERIC)).toBe(
            DOWNLOAD_NOT_FOUND_MESSAGE
        );
    });

    it("keeps the viewer's generic wording for anything else", () => {
        // No status: a request the WAF blocked, or a network error.
        expect(downloadFailureMessage([false, "Request blocked"], GENERIC)).toBe(GENERIC);
        expect(downloadFailureMessage([false, "Internal error", 500], GENERIC)).toBe(GENERIC);
        // `downloadAsset` resolves to `false` for a non-authorization client error.
        expect(downloadFailureMessage(false, GENERIC)).toBe(GENERIC);
        expect(downloadFailureMessage(undefined, GENERIC)).toBe(GENERIC);
    });

    it("[control] reads the status only from the tuple's third entry", () => {
        expect(downloadResponseStatus([false, "x", 403])).toBe(403);
        expect(downloadResponseStatus([false, "403"])).toBeUndefined();
        expect(downloadResponseStatus([true, "https://s3.example/x"])).toBeUndefined();
    });
});

describe("downloadStatusMessage", () => {
    it("classifies a streaming response's status the same way", () => {
        expect(downloadStatusMessage(403, "Failed to load: a.ifc (403)")).toBe(
            DOWNLOAD_FORBIDDEN_MESSAGE
        );
        expect(downloadStatusMessage(401, "x")).toBe(DOWNLOAD_UNAUTHENTICATED_MESSAGE);
        expect(downloadStatusMessage(404, "x")).toBe(DOWNLOAD_NOT_FOUND_MESSAGE);
        expect(downloadStatusMessage(500, "Failed to load: a.ifc (500)")).toBe(
            "Failed to load: a.ifc (500)"
        );
        expect(downloadStatusMessage(undefined, "x")).toBe("x");
    });
});

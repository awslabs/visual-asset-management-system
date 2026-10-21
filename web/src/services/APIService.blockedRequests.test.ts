/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

jest.mock("../utils/sessionManager", () => ({
    ensureValidSession: jest.fn(),
    logoutExpired: jest.fn(),
}));
jest.mock("../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(),
}));
jest.mock("./apiClient", () => ({
    ...jest.requireActual("./apiClient"),
    apiClient: { del: jest.fn() },
}));

import { apiClient, ApiError } from "./apiClient";
import { deleteAssetLink, deleteComment } from "./APIService";

/**
 * A 403 the deployment's AWS WAF web ACL answered (the WAF_FILTERED body) is not a permission
 * denial, so these calls hand back its message rather than the status their callers turn into a
 * permission sentence.
 */
const BLOCKED_MESSAGE = "Request blocked by the deployment's AWS WAF web ACL (request ID r-1)";
const blocked = () =>
    new ApiError(BLOCKED_MESSAGE, 403, {
        message: "Request blocked by the deployment's AWS WAF web ACL",
        blockedBy: "waf",
        requestId: "r-1",
    });
const denied = () => new ApiError("Not Authorized", 403, { message: "Not Authorized" });

describe("deleteAssetLink", () => {
    beforeEach(() => jest.clearAllMocks());

    it("returns a blocked request's message without the status", async () => {
        (apiClient.del as jest.Mock).mockRejectedValue(blocked());
        expect(await deleteAssetLink({ assetLinkId: "al-1" })).toStrictEqual([
            false,
            BLOCKED_MESSAGE,
            undefined,
        ]);
    });

    it("keeps the 403 of a permission denial", async () => {
        (apiClient.del as jest.Mock).mockRejectedValue(denied());
        expect(await deleteAssetLink({ assetLinkId: "al-1" })).toStrictEqual([
            false,
            "Not Authorized",
            403,
        ]);
    });
});

describe("deleteComment", () => {
    beforeEach(() => jest.clearAllMocks());

    it("returns a blocked request's message rather than the 403", async () => {
        (apiClient.del as jest.Mock).mockRejectedValue(blocked());
        expect(
            await deleteComment({ assetId: "a1", assetVersionIdAndCommentId: "v1:c1" })
        ).toStrictEqual([false, BLOCKED_MESSAGE]);
    });

    it("keeps the bare 403 of a permission denial", async () => {
        (apiClient.del as jest.Mock).mockRejectedValue(denied());
        expect(await deleteComment({ assetId: "a1", assetVersionIdAndCommentId: "v1:c1" })).toBe(
            403
        );
    });
});

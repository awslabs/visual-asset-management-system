/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DeleteAssetLinkModal } from "./DeleteAssetLinkModal";
import { apiClient, ApiError } from "../../../../../services/apiClient";
import Synonyms from "../../../../../synonyms";

const mockShowMessage = jest.fn();

jest.mock("../../../../../utils/sessionManager", () => ({
    ensureValidSession: jest.fn(),
    logoutExpired: jest.fn(),
}));
jest.mock("../../../../../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(),
}));
// The real deleteAssetLink runs over a stubbed client, so the modal reads what the service returns.
jest.mock("../../../../../services/apiClient", () => ({
    ...jest.requireActual("../../../../../services/apiClient"),
    apiClient: { del: jest.fn() },
}));
jest.mock("../../../../common/StatusMessage", () => ({
    ...jest.requireActual("../../../../common/StatusMessage"),
    useStatusMessage: () => ({ showMessage: mockShowMessage }),
}));

const BLOCKED_MESSAGE = "Request blocked by the deployment's AWS WAF web ACL (request ID r-1)";

const deleteLink = async () => {
    render(
        <DeleteAssetLinkModal
            visible={true}
            onDismiss={jest.fn()}
            assetLinkId="al-1"
            assetName="Bracket"
            relationshipType="related"
            onSuccess={jest.fn()}
        />
    );
    fireEvent.click(screen.getByText("Delete Link"));
    await waitFor(() => expect(mockShowMessage).toHaveBeenCalled());
    return mockShowMessage.mock.calls[0][0];
};

describe("DeleteAssetLinkModal refusal messages", () => {
    beforeEach(() => jest.clearAllMocks());

    it("shows a blocked request's message, not the permission sentence", async () => {
        (apiClient.del as jest.Mock).mockRejectedValue(
            new ApiError(BLOCKED_MESSAGE, 403, {
                message: "Request blocked by the deployment's AWS WAF web ACL",
                blockedBy: "waf",
                requestId: "r-1",
            })
        );
        expect(await deleteLink()).toMatchObject({ type: "error", message: BLOCKED_MESSAGE });
    });

    it("shows the permission sentence for a permission denial", async () => {
        (apiClient.del as jest.Mock).mockRejectedValue(
            new ApiError("Not Authorized", 403, { message: "Not Authorized" })
        );
        expect(await deleteLink()).toMatchObject({
            type: "error",
            message: `Not authorized to delete this ${Synonyms.asset} link`,
        });
    });
});

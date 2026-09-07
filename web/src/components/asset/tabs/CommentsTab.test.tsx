/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import CommentsTab from "./CommentsTab";
import { createComment } from "../../../services/APIService";

const mockShowMessage = jest.fn();

jest.mock("../../../services/APIService", () => ({
    fetchAllComments: jest.fn(() => Promise.resolve([])),
    fetchAsset: jest.fn(() =>
        Promise.resolve({ assetId: "a1", currentVersion: { S3Version: "v1" } })
    ),
    createComment: jest.fn(),
}));

jest.mock("../../common/StatusMessage", () => ({
    ...jest.requireActual("../../common/StatusMessage"),
    useStatusMessage: () => ({ showMessage: mockShowMessage }),
}));

// The comment editor is Jodit, which reports its content through onBlur. The stub keeps the
// component's own submit path intact while letting a test supply the body text.
jest.mock("jodit-react", () => ({
    __esModule: true,
    default: ({ onBlur }: any) => (
        <textarea data-testid="comment-editor" onChange={(event) => onBlur(event.target.value)} />
    ),
}));

jest.mock("./comments/PopulateComments", () => ({
    __esModule: true,
    default: () => <div data-testid="populate-comments" />,
}));

const submitComment = async (container: HTMLElement, body: string) => {
    fireEvent.change(screen.getByTestId("comment-editor"), { target: { value: body } });
    fireEvent.submit(container.querySelector("form") as HTMLFormElement);
    await waitFor(() => expect(createComment).toHaveBeenCalled());
};

describe("CommentsTab add-comment result handling", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    // createComment catches the API's rejection and resolves with [false, message], so the
    // component's catch block never runs and the result tuple is the only signal of failure.
    it("reports a body the API rejects as an error", async () => {
        (createComment as jest.Mock).mockResolvedValue([
            false,
            "commentBody must be lower than 16384 characters",
        ]);

        const { container } = render(<CommentsTab assetId="a1" databaseId="db1" isActive={true} />);
        await submitComment(container, "a body the API rejects");

        await waitFor(() =>
            expect(mockShowMessage).toHaveBeenCalledWith(
                expect.objectContaining({
                    type: "error",
                    message: expect.stringContaining("must be lower than 16384 characters"),
                })
            )
        );
        expect(mockShowMessage).not.toHaveBeenCalledWith(
            expect.objectContaining({ type: "success" })
        );
    });

    it("reports a body the API accepts as a success", async () => {
        (createComment as jest.Mock).mockResolvedValue([true, "Succeeded"]);

        const { container } = render(<CommentsTab assetId="a1" databaseId="db1" isActive={true} />);
        await submitComment(container, "a body the API accepts");

        await waitFor(() =>
            expect(mockShowMessage).toHaveBeenCalledWith(
                expect.objectContaining({ type: "success" })
            )
        );
        expect(mockShowMessage).not.toHaveBeenCalledWith(
            expect.objectContaining({ type: "error" })
        );
    });
});

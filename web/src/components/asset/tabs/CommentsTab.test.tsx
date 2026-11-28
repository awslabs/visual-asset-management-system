/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import CommentsTab from "./CommentsTab";
import { createComment, fetchAllComments } from "../../../services/APIService";

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
            "commentBody must be at most 16384 characters",
        ]);

        const { container } = render(<CommentsTab assetId="a1" databaseId="db1" isActive={true} />);
        await submitComment(container, "a body the API rejects");

        await waitFor(() =>
            expect(mockShowMessage).toHaveBeenCalledWith(
                expect.objectContaining({
                    type: "error",
                    message: expect.stringContaining("must be at most 16384 characters"),
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

describe("CommentsTab addresses the asset by its database and asset id", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("lists the asset's comments in its database", async () => {
        render(<CommentsTab assetId="a1" databaseId="db1" isActive={true} />);
        await waitFor(() => expect(fetchAllComments).toHaveBeenCalled());
        expect((fetchAllComments as jest.Mock).mock.calls[0][0]).toMatchObject({
            databaseId: "db1",
            assetId: "a1",
        });
    });

    it("creates a comment with the version in the body and no client-made comment id", async () => {
        (createComment as jest.Mock).mockResolvedValue([true, "Succeeded"]);
        const { container } = render(<CommentsTab assetId="a1" databaseId="db1" isActive={true} />);
        await submitComment(container, "a new remark");
        const request = (createComment as jest.Mock).mock.calls[0][0];
        expect(request).toMatchObject({ databaseId: "db1", assetId: "a1" });
        expect(request.body.commentBody).toBe("a new remark");
        expect(typeof request.body.assetVersionId).toBe("string");
        expect(request.body.assetVersionId).not.toBe("");
        expect(request).not.toHaveProperty("assetVersionIdAndCommentId");
    });
});

describe("CommentsTab loading spinner", () => {
    it("draws the spinner in the theme's text colour", () => {
        const { container } = render(<CommentsTab assetId="a1" databaseId="db1" isActive={true} />);
        const spinner = container.querySelector("svg.icon-loading");
        expect(spinner).not.toBeNull();
        expect(spinner!.querySelector("g")).toHaveAttribute("fill", "currentColor");
        expect((spinner!.parentElement as HTMLElement).style.color).toBe(
            "var(--vams-text-primary)"
        );
    });
});

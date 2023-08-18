/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { screen, fireEvent, waitFor, act, render } from "@testing-library/react";
import SingleComment from "./SingleComment";

describe("Edit and delete visibility", () => {
    test("buttons should be visible when hovering and user owns comment", async () => {
        render(
            <SingleComment
                userId="test-sub"
                comment={{
                    assetId: "test-asset-id",
                    assetVersionIdAndCommentId: "test-version-id:test-comment-id",
                    commentBody: "<p>Test comment body</p>",
                    commentOwnerID: "test-sub",
                    commentOwnerUsername: "test-email@amazon.com",
                    dateCreated: "2023-07-05T19:46:57.141660Z",
                }}
                setReload={() => {}}
                nestedCommentBool={false}
            />
        );
        var fullCommentDiv = screen.getByTestId("fullCommentDiv");
        await fireEvent.mouseEnter(fullCommentDiv);
        var editDiv = await waitFor(() => {
            return screen.getByTestId("editDeleteCommentDiv");
        });
        expect(editDiv.style).toHaveProperty("_values", { display: "block" });
    });

    test("buttons should NOT be visible when not hovering", async () => {
        render(
            <SingleComment
                userId="test-sub"
                comment={{
                    assetId: "test-asset-id",
                    assetVersionIdAndCommentId: "test-version-id:test-comment-id",
                    commentBody: "<p>Test comment body</p>",
                    commentOwnerId: "test_owner_id",
                    commentOwnerUsername: "test-email@amazon.com",
                    dateCreated: "2023-07-05T19:46:57.141660Z",
                }}
                setReload={() => {}}
                nestedCommentBool={false}
            />
        );
        var editDiv = await waitFor(() => {
            return screen.getByTestId("editDeleteCommentDiv");
        });
        expect(editDiv.style).toHaveProperty("_values", { display: "none" });
    });

    test("buttons should NOT be visible when hovering BUT user doesn't own comment", async () => {
        render(
            <SingleComment
                userId="test-sub"
                comment={{
                    assetId: "test-asset-id",
                    assetVersionIdAndCommentId: "test-version-id:test-comment-id",
                    commentBody: "<p>Test comment body</p>",
                    commentOwnerID: "test-sub",
                    commentOwnerUsername: "test-email@amazon.com",
                    dateCreated: "2023-07-05T19:46:57.141660Z",
                }}
                setReload={() => {}}
                nestedCommentBool={false}
            />
        );
        var fullCommentDiv = screen.getByTestId("fullCommentDiv");
        await fireEvent.mouseEnter(fullCommentDiv);
        var editDiv = await waitFor(() => {
            return screen.getByTestId("editDeleteCommentDiv");
        });

        expect(editDiv.style).toHaveProperty("_values", { display: "block" });
    });
});

describe("Edit and delete visibility for nested comments", () => {
    test("buttons should be visible when hovering and user owns nested comment", async () => {
        render(
            <SingleComment
                userId="test-sub"
                comment={{
                    assetId: "test-asset-id",
                    assetVersionIdAndCommentId: "test-version-id:test-comment-id",
                    commentBody: "<p>Test comment body</p>",
                    commentOwnerID: "test-sub",
                    commentOwnerUsername: "test-email@amazon.com",
                    dateCreated: "2023-07-05T19:46:57.141660Z",
                }}
                setReload={() => {}}
                nestedCommentBool={true}
            />
        );
        var fullCommentDiv = screen.getByTestId("nestedFullCommentDiv");
        await fireEvent.mouseEnter(fullCommentDiv);
        var editDiv = await waitFor(() => {
            return screen.getByTestId("editDeleteNestedCommentDiv");
        });

        expect(editDiv.style).toHaveProperty("_values", { display: "block" });
    });

    test("buttons should NOT be visible when not hovering", async () => {
        render(
            <SingleComment
                userId="test-sub"
                comment={{
                    assetId: "test-asset-id",
                    assetVersionIdAndCommentId: "test-version-id:test-comment-id",
                    commentBody: "<p>Test comment body</p>",
                    commentOwnerId: "test_owner_id",
                    commentOwnerUsername: "test-email@amazon.com",
                    dateCreated: "2023-07-05T19:46:57.141660Z",
                }}
                setReload={() => {}}
                nestedCommentBool={true}
            />
        );
        var editDiv = await waitFor(() => {
            return screen.getByTestId("editDeleteNestedCommentDiv");
        });
        expect(editDiv.style).toHaveProperty("_values", { display: "none" });
    });

    test("buttons should NOT be visible when hovering BUT user doesn't own nested comment", async () => {
        render(
            <SingleComment
                userId="test-sub"
                comment={{
                    assetId: "test-asset-id",
                    assetVersionIdAndCommentId: "test-version-id:test-comment-id",
                    commentBody: "<p>Test comment body</p>",
                    commentOwnerID: "test-sub",
                    commentOwnerUsername: "test-email@amazon.com",
                    dateCreated: "2023-07-05T19:46:57.141660Z",
                }}
                setReload={() => {}}
                nestedCommentBool={true}
            />
        );
        var fullCommentDiv = screen.getByTestId("nestedFullCommentDiv");
        await fireEvent.mouseEnter(fullCommentDiv);
        var editDiv = await waitFor(() => {
            return screen.getByTestId("editDeleteNestedCommentDiv");
        });

        expect(editDiv.style).toHaveProperty("_values", { display: "block" });
    });
});

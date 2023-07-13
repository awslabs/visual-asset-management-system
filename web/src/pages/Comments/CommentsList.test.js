import { render, screen, prettyDOM } from "@testing-library/react";
import CommentsList from "./CommentsList";
import { fetchAllComments } from "../../services/APIService";

jest.mock("../../services/APIService", () => ({ fetchAllComments: jest.fn() }));

test("Should not be able to leave a comment with no asset selected", () => {
    render(<CommentsList selectedItems={[{}]} />);
    const submitButton = screen.getByText("Submit");
    const quillInput = document.getElementById("commentInput");
    expect(submitButton).toBeDisabled;
    expect(quillInput).toBeDisabled;
});

test("Should be able to leave a comment with an asset selected", () => {
    render(
        <CommentsList
            selectedItems={[
                {
                    assetId: "testId",
                },
            ]}
        />,
    );
    const submitButton = screen.getByText("Submit");
    const quillInput = document.getElementById("commentInput");
    expect(submitButton).not.toBeDisabled;
    expect(quillInput).not.toBeDisabled;
});

test("Should render a test comment correctly", async () => {
    // mock the return value of the fetchAllComments function
    var assetId = "test-asset-id";
    var assetVersionIdAndCommentId = "test-version-id:test-comment-id";
    var commentBody = "<p>Test comment body</p>";
    var commentOwnerId = "test_owner_id";
    var commentOwnerUsername = "test-email@amazon.com";
    var dateCreated = "2023-07-05T19:46:57.141660Z";
    fetchAllComments.mockImplementation(() => {
        return [
            {
                assetId: assetId,
                "assetVersionId:commentId": assetVersionIdAndCommentId,
                commentBody: commentBody,
                commentOwnerID: commentOwnerId,
                commentOwnerUsername: commentOwnerUsername,
                dateCreated: dateCreated,
            },
        ];
    });
    render(
        <CommentsList
            selectedItems={[
                {
                    assetId: assetId,
                    assetName: "Test_asset",
                },
            ]}
        />,
    );
    // wait for fetchAllComments to be called
    await new Promise((r) => setTimeout(r, 2000));
    const commentContainer = document.getElementsByClassName("singleComment").item(0);
    expect(commentContainer.innerHTML).toBe(commentBody);
});

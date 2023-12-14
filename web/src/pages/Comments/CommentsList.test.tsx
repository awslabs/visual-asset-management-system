import { render, screen } from "@testing-library/react";
import CommentsList from "./CommentsList";
import { fetchAllComments } from "../../services/APIService";

jest.mock("../../services/APIService", () => ({ fetchAllComments: jest.fn() }));
jest.mock("aws-amplify");

test("Should not be able to leave a comment with no asset selected", async () => {
    await render(<CommentsList selectedItems={[{}]} />);
    const submitButton = screen.getByTestId("submitButton");
    expect(submitButton).toHaveProperty("disabled", true);
});

test("Should be able to leave a comment with an asset selected", async () => {
    await render(
        <CommentsList
            selectedItems={[
                {
                    assetId: "testId",
                    currentVersion: {
                        S3Version: "test-version-id",
                        Version: "1",
                        Comment: "test-version-comment",
                    },
                    versions: [],
                },
            ]}
        />
    );
    const submitButton = screen.getByTestId("submitButton");
    expect(submitButton).toHaveProperty("disabled", false);
});

test("Should render a test comment correctly", async () => {
    // mock the return value of the fetchAllComments function
    const assetId = "test-asset-id";
    const assetVersionIdAndCommentId = "test-version-id:test-comment-id";
    const commentBody = "<p>Test comment body</p>";
    const commentOwnerId = "test_owner_id";
    const commentOwnerUsername = "test-email@amazon.com";
    const dateCreated = "2023-07-05T19:46:57.141660Z";
    jest.mocked(fetchAllComments).mockImplementation(() =>
        Promise.resolve([
            {
                assetId: assetId,
                "assetVersionId:commentId": assetVersionIdAndCommentId,
                commentBody: commentBody,
                commentOwnerID: commentOwnerId,
                commentOwnerUsername: commentOwnerUsername,
                dateCreated: dateCreated,
            },
        ])
    );
    await render(
        <CommentsList
            selectedItems={[
                {
                    assetId: assetId,
                    assetName: "Test_asset",
                    currentVersion: {
                        S3Version: "test-version-id",
                        Version: "1",
                        Comment: "test-version-comment",
                    },
                    versions: [],
                },
            ]}
        />
    );
    // wait for fetchAllComments to be called
    const commentContainer = screen.getByTestId("singleComment");
    expect(commentContainer.innerHTML).toBe(commentBody);
});

test("Should render multiple versions correctly", async () => {
    // mock the return value of the fetchAllComments function
    const assetId = "test-asset-id";
    const assetVersionIdAndCommentId = "test-version-id:test-comment-id";
    const commentBody = "<p>Test comment body</p>";
    const commentOwnerId = "test_owner_id";
    const commentOwnerUsername = "test-email@amazon.com";
    const dateCreated = "2023-07-05T19:46:57.141660Z";
    jest.mocked(fetchAllComments).mockImplementation(() =>
        Promise.resolve([
            {
                assetId: assetId,
                "assetVersionId:commentId": assetVersionIdAndCommentId,
                commentBody: commentBody,
                commentOwnerID: commentOwnerId,
                commentOwnerUsername: commentOwnerUsername,
                dateCreated: dateCreated,
            },
        ])
    );
    render(
        <CommentsList
            selectedItems={[
                {
                    assetId: assetId,
                    assetName: "Test_asset",
                    currentVersion: {
                        S3Version: "test-version-id-2",
                        Version: "2",
                        Comment: "test-version-comment-2",
                    },
                    versions: [
                        {
                            S3Version: "test-version-id",
                            Version: "1",
                            Comment: "test-version-comment",
                        },
                    ],
                },
            ]}
        />
    );
    // wait for fetchAllComments to be called
    const expandableContainerList = screen.getAllByTestId("expandableSectionDiv");
    expect(expandableContainerList.length).toBe(1);
    let expandableContainer = expandableContainerList.at(0);
    // there should be 2 expandable sections (one for each version of the asset)
    expect(expandableContainer).not.toBeUndefined();
    expect(expandableContainer!.childNodes.length).toBe(2);
});

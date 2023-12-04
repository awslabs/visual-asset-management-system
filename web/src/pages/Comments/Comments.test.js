// /*
//  * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
//  * SPDX-License-Identifier: Apache-2.0
//  */

// import { fetchAllComments, deleteComment } from "../../services/APIService";
// import { API } from "aws-amplify";

// // mock the API call
// jest.mock("aws-amplify");

// describe("fetchAllComments tests", () => {
//     test("should return list of comments on valid API call", async () => {
//         const validComment = {
//             assetId: "test-asset-id",
//             assetVersionIdAndCommentId: "test-version-id:test-comment-id",
//             commentBody: "<p>Test comment body</p>",
//             commentOwnerID: "test_owner_id",
//             commentOwnerUsername: "test-email@amazon.com",
//             dateCreated: "2023-07-05T19:46:57.141660Z",
//         };
//         // mocked return value that fetchAllComments will get when it calls the API
//         API.get.mockResolvedValue({
//             message: [validComment],
//         });
//         let response = await fetchAllComments("test-asset-id");
//         expect(response).toEqual([validComment]);
//     });

//     test("should return false on invalid API call", async () => {
//         // mocked return value to test invalid API return
//         API.get.mockResolvedValue({});
//         let response = await fetchAllComments("test-asset-id");
//         expect(response).toEqual(false);
//     });
// });

// describe("deleteComment tests", () => {
//     test("should delete a comment on valid API call", async () => {
//         // mocked value to test a valid return of the API
//         API.del.mockResolvedValue({ message: "Comment deleted" });
//         var response = await deleteComment({
//             assetId: "test-id",
//             "assetVersionId:commentId": "test-version-id:test-comment-id",
//         });
//         expect(response[0]).toBe(true);
//         expect(response[1]).toBe("Comment deleted");
//     });

//     test("should handle errors gracefully", async () => {
//         // mocked value to test an invalid return of the API
//         API.del.mockResolvedValue({});
//         var response = await deleteComment({
//             assetId: "test-id",
//             "assetVersionId:commentId": "test-version-id:test-comment-id",
//         });
//         console.log(response);
//         expect(response).toBe(false);
//     });
// });

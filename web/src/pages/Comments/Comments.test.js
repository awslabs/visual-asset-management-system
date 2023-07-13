import { render, screen, prettyDOM } from '@testing-library/react';
import CommentsList from './CommentsList';
import { fetchAllComments } from '../../services/APIService';
import { API } from "aws-amplify";

jest.mock('aws-amplify');

test('fetchAllComments should return list of comments on valid API call', async () => {
    var validComment = {
        "assetId": "test-asset-id",
        "assetVersionIdAndCommentId": "test-version-id:test-comment-id",
        "commentBody": "<p>Test comment body</p>",
        "commentOwnerId": "test_owner_id",
        "commentOwnerUsername": "test-email@amazon.com",
        "dateCreated": "2023-07-05T19:46:57.141660Z",
    }
    API.get.mockResolvedValue({
        message: [validComment]
    })
    expect(await fetchAllComments("test-asset-id")).toEqual([validComment])
})

test('fetchAllComments should return false on invalid API call', async () => {
    API.get.mockResolvedValue({

    })
    expect(await fetchAllComments("test-asset-id")).toEqual(false)
})
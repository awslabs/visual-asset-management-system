/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * #1860: comments are addressed by database and asset id, and every subscription request names the
 * asset's database. The services build `comments/database/{databaseId}/assets/{assetId}[/{commentId}]`
 * (each segment encoded) and pass the subscription bodies through unchanged.
 */

import {
    checkSubscription,
    createComment,
    createSubscription,
    deleteComment,
    deleteSubscription,
    fetchAllComments,
    fetchSubscriptionRules,
    unsubscribeFromAsset,
    updateComment,
    updateSubscription,
} from "./APIService";
import { apiClient } from "./apiClient";

jest.mock("./apiClient", () => ({
    ApiError: class extends Error {},
    apiClient: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn() },
}));

const client = apiClient as unknown as Record<"get" | "post" | "put" | "del", jest.Mock>;

describe("comment requests", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => {});
        client.get.mockResolvedValue({ message: [] });
        client.post.mockResolvedValue({ message: "Succeeded", commentId: "c-new" });
        client.put.mockResolvedValue({ message: "Succeeded" });
        client.del.mockResolvedValue({ message: "Comment deleted" });
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("lists the comments of the asset in the named database", async () => {
        await fetchAllComments({ databaseId: "db-b", assetId: "x1" });
        expect(client.get.mock.calls[0][0]).toBe("comments/database/db-b/assets/x1");
    });

    it("creates a comment with its version in the body, not the path", async () => {
        await createComment({
            databaseId: "db-b",
            assetId: "x1",
            body: { commentBody: "hello", assetVersionId: "2" },
        });
        expect(client.post).toHaveBeenCalledWith("comments/database/db-b/assets/x1", {
            body: { commentBody: "hello", assetVersionId: "2" },
        });
    });

    it("edits and deletes one comment by its own id", async () => {
        await updateComment({
            databaseId: "db-b",
            assetId: "x1",
            commentId: "c-1",
            body: { commentBody: "edited" },
        });
        await deleteComment({ databaseId: "db-b", assetId: "x1", commentId: "c-1" });
        expect(client.put.mock.calls[0][0]).toBe("comments/database/db-b/assets/x1/c-1");
        expect(client.del.mock.calls[0][0]).toBe("comments/database/db-b/assets/x1/c-1");
    });

    it("encodes each path segment", async () => {
        await fetchAllComments({ databaseId: "db-b", assetId: "site 4.rev2" });
        expect(client.get.mock.calls[0][0]).toBe("comments/database/db-b/assets/site%204.rev2");
    });
});

describe("subscription requests carry the database id", () => {
    const body = {
        eventName: "Asset Version Change",
        entityName: "Asset",
        entityId: "x1",
        databaseId: "db-b",
        subscribers: ["alice"],
    };

    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => {});
        for (const method of ["post", "put", "del"] as const) {
            client[method].mockResolvedValue({ message: "success" });
        }
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("sends the body as given on every route", async () => {
        await createSubscription(body);
        await updateSubscription(body);
        await deleteSubscription(body);
        await unsubscribeFromAsset(body);
        await checkSubscription({ userId: "alice", assetId: "x1", databaseId: "db-b" });
        expect(client.post).toHaveBeenCalledWith("subscriptions", { body });
        expect(client.put).toHaveBeenCalledWith("subscriptions", { body });
        expect(client.del).toHaveBeenCalledWith("subscriptions", { body });
        expect(client.del).toHaveBeenCalledWith("unsubscribe", { body });
        expect(client.post).toHaveBeenCalledWith("check-subscription", {
            body: { userId: "alice", assetId: "x1", databaseId: "db-b" },
        });
    });
});

describe("the subscription list", () => {
    beforeEach(() => jest.clearAllMocks());

    it("keys each row by its database as well, so same-id assets stay apart", async () => {
        const row = { eventName: "Asset Version Change", entityName: "Asset", entityId: "x1" };
        client.get.mockResolvedValue({
            message: {
                Items: [
                    { ...row, databaseId: "db-a" },
                    { ...row, databaseId: "db-b" },
                ],
            },
        });
        const items = await fetchSubscriptionRules();
        const keys = items.map((item: any) => item.subscriptionKey);
        expect(new Set(keys).size).toBe(2);
        expect(items.map((item: any) => item.databaseId)).toEqual(["db-a", "db-b"]);
    });
});

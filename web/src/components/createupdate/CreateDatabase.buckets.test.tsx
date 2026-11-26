/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Databases page mounts the create form with the list, closed. Reading `GET /buckets` on mount
 * sent the request on every page load, and a role without that route got a 403 and a console error.
 * The form now reads the bucket list once it opens, and only for a role allowed the route.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import CreateDatabase from "./CreateDatabase";

jest.mock("../../services/APIService", () => ({
    createDatabase: jest.fn(),
    updateDatabase: jest.fn(),
    fetchBuckets: jest.fn(),
}));

let mockAllowed = { loading: false, buckets: true, failed: false };
const mockRetry = jest.fn();
jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: mockAllowed.loading,
        failed: mockAllowed.failed,
        retry: mockRetry,
        can: (method: string, path: string) =>
            !mockAllowed.loading && method === "GET" && path === "/buckets" && mockAllowed.buckets,
    }),
}));

const { fetchBuckets } = jest.requireMock("../../services/APIService");

const form = (open: boolean) => (
    <CreateDatabase open={open} setOpen={jest.fn()} setReload={jest.fn()} />
);

beforeEach(() => {
    jest.clearAllMocks();
    mockAllowed = { loading: false, buckets: true, failed: false };
    fetchBuckets.mockResolvedValue({ Items: [] });
});

describe("CreateDatabase bucket list", () => {
    it("does not read the bucket list while the form is closed", async () => {
        render(form(false));
        // Give any mount effect a chance to run before asserting it did not.
        await new Promise((r) => setTimeout(r, 0));
        expect(fetchBuckets).not.toHaveBeenCalled();
    });

    it("reads the bucket list once when the form opens, and not again on reopen", async () => {
        const { rerender } = render(form(false));
        rerender(form(true));
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
        rerender(form(false));
        rerender(form(true));
        await new Promise((r) => setTimeout(r, 0));
        expect(fetchBuckets).toHaveBeenCalledTimes(1);
    });

    it("never reads the bucket list for a role not allowed GET /buckets", async () => {
        mockAllowed = { loading: false, buckets: false, failed: false };
        render(form(true));
        await new Promise((r) => setTimeout(r, 0));
        expect(fetchBuckets).not.toHaveBeenCalled();
    });

    it("waits for the role's permissions before deciding", async () => {
        mockAllowed = { loading: true, buckets: true, failed: false };
        const { rerender } = render(form(true));
        await new Promise((r) => setTimeout(r, 0));
        expect(fetchBuckets).not.toHaveBeenCalled();
        mockAllowed = { loading: false, buckets: true, failed: false };
        rerender(form(true));
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
        expect(screen.getByText("Default Bucket and Prefix")).toBeInTheDocument();
    });

    it("retries a failed permissions check once per opening of the form", async () => {
        mockAllowed = { loading: false, buckets: false, failed: true };
        const { rerender } = render(form(false));
        await new Promise((r) => setTimeout(r, 0));
        expect(mockRetry).not.toHaveBeenCalled();

        rerender(form(true));
        await waitFor(() => expect(mockRetry).toHaveBeenCalledTimes(1));
        rerender(form(true));
        await new Promise((r) => setTimeout(r, 0));
        expect(mockRetry).toHaveBeenCalledTimes(1);

        rerender(form(false));
        rerender(form(true));
        await waitFor(() => expect(mockRetry).toHaveBeenCalledTimes(2));
        expect(fetchBuckets).not.toHaveBeenCalled();
    });

    it("reads the bucket list once a retried permissions check allows it", async () => {
        mockAllowed = { loading: false, buckets: false, failed: true };
        const { rerender } = render(form(true));
        await waitFor(() => expect(mockRetry).toHaveBeenCalledTimes(1));
        mockAllowed = { loading: false, buckets: true, failed: false };
        rerender(form(true));
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
    });
});

describe("CreateDatabase bucket list empty message", () => {
    const openList = async () => {
        const { rerender } = render(form(true));
        if (mockAllowed.failed) {
            // The form retries the check on open; play the retry out to the same answer.
            await waitFor(() => expect(mockRetry).toHaveBeenCalledTimes(1));
            mockAllowed = { ...mockAllowed, loading: true };
            rerender(form(true));
            mockAllowed = { ...mockAllowed, loading: false };
            rerender(form(true));
        }
        const wrapper = createWrapper(document.body);
        const select = wrapper.findSelect('[data-testid="database-bucket"]');
        select!.openDropdown();
        return select!;
    };

    it("says the permissions check failed when it did", async () => {
        mockAllowed = { loading: false, buckets: false, failed: true };
        const select = await openList();
        expect(select.findDropdown().getElement().textContent).toContain(
            "The permissions check failed"
        );
    });

    it("says the role may not list buckets when the check succeeded without the route", async () => {
        mockAllowed = { loading: false, buckets: false, failed: false };
        const select = await openList();
        expect(select.findDropdown().getElement().textContent).toContain(
            "Your role does not allow listing buckets."
        );
    });
});

describe("CreateDatabase bucket option labels", () => {
    const optionTexts = async (items: Record<string, unknown>[]) => {
        fetchBuckets.mockResolvedValue({ Items: items });
        render(form(true));
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
        const select = createWrapper(document.body).findSelect('[data-testid="database-bucket"]');
        select!.openDropdown();
        await waitFor(() => expect(select!.findDropdown().findOptions().length).toBe(items.length));
        return select!
            .findDropdown()
            .findOptions()
            .map((o) => o.getElement().textContent ?? "");
    };

    it("marks only the buckets the API reports as accelerated", async () => {
        const texts = await optionTexts([
            {
                bucketId: "bucket-one",
                bucketName: "fast",
                baseAssetsPrefix: "/",
                bucketRegion: "us-west-2",
                isAccelerated: true,
            },
            {
                bucketId: "bucket-two",
                bucketName: "plain",
                baseAssetsPrefix: "/",
                isAccelerated: false,
            },
            { bucketId: "bucket-three", bucketName: "legacy", baseAssetsPrefix: "/" },
        ]);
        expect(texts[0]).toContain("fast - / (us-west-2) (accelerated)");
        expect(texts[1]).not.toContain("accelerated");
        expect(texts[2]).not.toContain("accelerated");
    });

    it("keeps the Region and default markers ahead of the accelerated one", async () => {
        const texts = await optionTexts([
            {
                bucketId: "bucket-one",
                bucketName: "fast",
                baseAssetsPrefix: "/",
                bucketRegion: "us-east-1",
                isDefault: true,
                isAccelerated: true,
            },
            { bucketId: "bucket-two", bucketName: "other", baseAssetsPrefix: "/" },
        ]);
        expect(texts[0]).toContain("fast - / (us-east-1) (VAMS default) (accelerated)");
    });
});

// GET /buckets lists only the buckets the role may read (#1978). The form reflects that answer: it
// decides nothing itself.
const NO_BUCKET =
    "No bucket is available to your role. Ask an administrator for a bucket permission.";

describe("CreateDatabase with no bucket available to the role", () => {
    it("says so in the bucket list and on the field when the API lists no bucket", async () => {
        fetchBuckets.mockResolvedValue({ Items: [] });
        render(form(true));
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(screen.getByText(NO_BUCKET)).toBeInTheDocument());
        const select = createWrapper(document.body).findSelect('[data-testid="database-bucket"]');
        select!.openDropdown();
        expect(select!.findDropdown().getElement().textContent).toContain(NO_BUCKET);
        expect(screen.queryByText("A default bucket is required")).not.toBeInTheDocument();
    });

    it("keeps the required message when the API lists buckets", async () => {
        fetchBuckets.mockResolvedValue({
            Items: [
                { bucketId: "bucket-one", bucketName: "a", baseAssetsPrefix: "/" },
                { bucketId: "bucket-two", bucketName: "b", baseAssetsPrefix: "/" },
            ],
        });
        render(form(true));
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
        await waitFor(() =>
            expect(screen.getByText("A default bucket is required")).toBeInTheDocument()
        );
        expect(screen.queryByText(NO_BUCKET)).not.toBeInTheDocument();
    });
});

describe("CreateDatabase edit form, current bucket", () => {
    // The database row as GET /database returns it: the bucket id and the bucket's name and prefix.
    const database = {
        databaseId: "team-db",
        description: "a team database",
        defaultBucketId: "bucket-current",
        bucketName: "team-a-assets",
        baseAssetsPrefix: "projects/",
    };
    const editForm = (initState: Record<string, unknown> = database) => (
        <CreateDatabase
            open={true}
            setOpen={jest.fn()}
            setReload={jest.fn()}
            initState={initState as any}
        />
    );
    const select = () =>
        createWrapper(document.body).findSelect('[data-testid="database-bucket"]')!;
    const bucketField = () => {
        const field = createWrapper(document.body)
            .findAllFormFields()
            .find((f) =>
                f.findLabel()?.getElement().textContent?.includes("Default Bucket and Prefix")
            );
        expect(field).toBeDefined();
        return field;
    };
    const SELECT_HINT = /Select a bucket and prefix/;

    it("shows the bucket name and prefix of the database record when the API does not list it (D4)", async () => {
        fetchBuckets.mockResolvedValue({
            Items: [{ bucketId: "bucket-other", bucketName: "other", baseAssetsPrefix: "/" }],
        });
        render(editForm());
        await waitFor(() =>
            expect(screen.getByTestId("database-bucket-current").textContent).toBe(
                "Current bucket: team-a-assets - projects/"
            )
        );
        expect(screen.getByTestId("database-bucket-current").textContent).not.toContain(
            "bucket-current"
        );
        expect(select().findTrigger().getElement().textContent).toContain(
            "Keep the current bucket"
        );
        expect(select().findTrigger().getElement().textContent).not.toContain("Select a bucket");
    });

    it("drops the select hint while the current bucket is kept (D4)", async () => {
        fetchBuckets.mockResolvedValue({ Items: [] });
        render(editForm());
        await waitFor(() =>
            expect(screen.getByTestId("database-bucket-current")).toBeInTheDocument()
        );
        expect(bucketField()?.findConstraint()).toBeNull();
        expect(document.body.textContent).not.toMatch(SELECT_HINT);
    });

    it("never shows the internal id, even for a record without the bucket name (D4)", async () => {
        mockAllowed = { loading: false, buckets: false, failed: false };
        render(
            editForm({
                databaseId: "team-db",
                description: "a team database",
                defaultBucketId: "bucket-current",
            })
        );
        await waitFor(() =>
            expect(screen.getByTestId("database-bucket-current").textContent).toBe(
                "Current bucket: the bucket this database uses now"
            )
        );
        expect(document.body.textContent).not.toContain("bucket-current");
    });

    it("keeps the select hint when the API lists the current bucket", async () => {
        fetchBuckets.mockResolvedValue({
            Items: [{ bucketId: "bucket-current", bucketName: "mine", baseAssetsPrefix: "/" }],
        });
        render(editForm());
        await waitFor(() =>
            expect(select().findTrigger().getElement().textContent).toContain("mine - /")
        );
        expect(bucketField()?.findConstraint()?.getElement().textContent).toMatch(SELECT_HINT);
    });

    it("shows the current bucket when the API lists no bucket", async () => {
        fetchBuckets.mockResolvedValue({ Items: [] });
        render(editForm());
        await waitFor(() =>
            expect(screen.getByTestId("database-bucket-current")).toBeInTheDocument()
        );
        // The database has a bucket, so the field is not in error.
        expect(screen.queryByText(NO_BUCKET)).not.toBeInTheDocument();
        expect(screen.queryByText("A default bucket is required")).not.toBeInTheDocument();
    });

    it("shows the current bucket when the role may not list buckets", async () => {
        mockAllowed = { loading: false, buckets: false, failed: false };
        render(editForm());
        await waitFor(() =>
            expect(screen.getByTestId("database-bucket-current")).toBeInTheDocument()
        );
        expect(fetchBuckets).not.toHaveBeenCalled();
    });

    it("selects the current bucket and shows no extra text when the API lists it", async () => {
        fetchBuckets.mockResolvedValue({
            Items: [{ bucketId: "bucket-current", bucketName: "mine", baseAssetsPrefix: "/" }],
        });
        render(editForm());
        await waitFor(() =>
            expect(select().findTrigger().getElement().textContent).toContain("mine - /")
        );
        expect(screen.queryByTestId("database-bucket-current")).not.toBeInTheDocument();
    });
});

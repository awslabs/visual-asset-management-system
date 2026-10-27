/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Comments tab lists an asset's comments in one asset-level section. Comment sort keys start with
 * whatever version ID was current when the comment was written (the asset ID for web comments, a real
 * version ID for API and migrated comments), so the section shows every comment the API returns and
 * edits use the comment's own assetId.
 */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import PopulateComments from "./PopulateComments";
import Synonyms from "../../../../synonyms";

jest.mock("../../../../services/APIService", () => ({
    deleteComment: jest.fn(),
    updateComment: jest.fn(),
}));
jest.mock("jodit-react", () => ({ __esModule: true, default: () => null }));
// sanitize-html loads htmlparser2 as ESM, which jest cannot parse; the body passes through.
jest.mock("sanitize-html", () => {
    const sanitize = (html: string) => html;
    sanitize.simpleTransform = () => () => ({});
    return { __esModule: true, default: sanitize };
});

const { updateComment } = jest.requireMock("../../../../services/APIService");

const asset = {
    assetId: "asset1",
    assetLocation: {},
    assetName: "Asset 1",
    assetType: ".glb",
    currentVersion: {
        Comment: "",
        dateModified: "2026-10-01T00:00:00Z",
        S3Version: "",
        Version: "3",
        description: "",
    },
    databaseId: "db1",
    description: "",
    isDistributable: true,
};

const comments = [
    {
        assetId: "asset1",
        "assetVersionId:commentId": "asset1:xweb",
        commentBody: "<p>from the web</p>",
        commentOwnerID: "user1",
        commentOwnerUsername: "user1",
        dateCreated: "2026-10-02T00:00:00Z",
    },
    {
        assetId: "asset1",
        "assetVersionId:commentId": "3:xcli",
        commentBody: "<p>from the cli</p>",
        commentOwnerID: "user1",
        commentOwnerUsername: "user1",
        dateCreated: "2026-10-03T00:00:00Z",
    },
];

const mount = (allComments = comments) =>
    render(
        <PopulateComments
            loading={false}
            showLoading={jest.fn()}
            userId="user1"
            asset={asset}
            allComments={[...allComments]}
            setReload={jest.fn()}
            showMessage={jest.fn()}
        />
    );

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("PopulateComments", () => {
    it("lists comments keyed by any version ID in one section", () => {
        mount();
        expect(screen.getByText("from the web")).toBeInTheDocument();
        expect(screen.getByText("from the cli")).toBeInTheDocument();
        expect(screen.getByText(Synonyms.Comments)).toBeInTheDocument();
    });

    // TEMPORARY-TEST: pins the removal of the per-version "Version ..." section heading; drop once released.
    it("shows no version heading above the comments", () => {
        mount();
        expect(screen.queryByText(/^Version /)).toBeNull();
    });

    it("shows asset-level empty text when there are no comments", () => {
        mount([]);
        expect(screen.getByText(`No ${Synonyms.comments} yet`)).toBeInTheDocument();
    });

    it("saves an edit to a comment keyed by a real version ID under the comment's assetId", async () => {
        updateComment.mockResolvedValue([true, "updated"]);
        mount();
        const editButtons = screen.getAllByRole("button", { name: "Edit" });
        // Comments are sorted oldest first, so the second Edit belongs to the "3:xcli" comment.
        fireEvent.click(editButtons[1]);
        const modals = createWrapper(document.body).findAllModals();
        const editModal = modals.find((m) => m.isVisible());
        expect(editModal).toBeDefined();
        const footerButtons = editModal!.findFooter()!.findAllButtons();
        fireEvent.click(footerButtons[footerButtons.length - 1].getElement());
        await waitFor(() => expect(updateComment).toHaveBeenCalled());
        expect(updateComment.mock.calls[0][0]).toMatchObject({
            assetId: "asset1",
            assetVersionIdAndCommentId: "3:xcli",
        });
    });
});

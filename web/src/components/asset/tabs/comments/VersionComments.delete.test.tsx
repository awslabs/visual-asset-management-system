/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The comment delete outcome. `deleteComment` never throws: it resolves `[true, message]` on success,
 * the number `403` for a permission denial, `[false, message]` for any other rejected request (a
 * request the AWS WAF web ACL blocked included) and `false` for an empty reply, so the flash has to be
 * read off the result rather than assumed.
 */
import React from "react";
import { render, waitFor, fireEvent, within } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import VersionComments from "./VersionComments";
import Synonyms from "../../../../synonyms";

jest.mock("../../../../services/APIService", () => ({
    deleteComment: jest.fn(),
    updateComment: jest.fn(),
}));
jest.mock("jodit-react", () => ({ __esModule: true, default: () => null }));
// sanitize-html loads htmlparser2 as ESM, which jest cannot parse; the delete flow does not depend
// on what the comment body sanitizes to, so the body passes through.
jest.mock("sanitize-html", () => {
    const sanitize = (html: string) => html;
    sanitize.simpleTransform = () => () => ({});
    return { __esModule: true, default: sanitize };
});

const { deleteComment } = jest.requireMock("../../../../services/APIService");

const BLOCKED = "Request blocked by the deployment's AWS WAF web ACL (request ID r-1)";

const comment = {
    assetId: "asset1",
    "assetVersionId:commentId": "v1:c1",
    commentBody: "<p>hello</p>",
    commentOwnerID: "owner",
    commentOwnerUsername: "user1",
    dateCreated: "2026-10-14T00:00:00Z",
};

const mount = () => {
    const showMessage = jest.fn();
    const setReload = jest.fn();
    render(
        <VersionComments
            loading={false}
            showLoading={jest.fn()}
            userId="user1"
            setReload={setReload}
            defaultExpanded={true}
            comments={[comment]}
            showMessage={showMessage}
        />
    );
    return { showMessage, setReload };
};

/** Clicks the comment's Delete link, then the confirmation dialog's Delete button. */
const confirmDelete = async () => {
    // The dialog keeps its own Delete button in the DOM while hidden, so the comment's is found
    // inside the comment's action bar.
    const actions = document.querySelector(".commentActions") as HTMLElement;
    fireEvent.click(within(actions).getByRole("button", { name: "Delete" }));
    const modal = createWrapper(document.body).findModal();
    await waitFor(() => expect(modal?.isVisible()).toBe(true));
    const buttons = modal?.findFooter()?.findAllButtons() ?? [];
    expect(buttons.length).toBeGreaterThan(0);
    fireEvent.click(buttons[buttons.length - 1].getElement());
};

const lastMessage = (showMessage: jest.Mock) =>
    showMessage.mock.calls[showMessage.mock.calls.length - 1][0];

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("comment delete outcome", () => {
    it("confirms a delete the API accepted", async () => {
        deleteComment.mockResolvedValue([true, "Comment deleted"]);
        const { showMessage, setReload } = mount();
        await confirmDelete();
        await waitFor(() => expect(showMessage).toHaveBeenCalled());
        expect(deleteComment).toHaveBeenCalledWith({
            assetId: "asset1",
            assetVersionIdAndCommentId: "v1:c1",
        });
        expect(lastMessage(showMessage)).toMatchObject({
            type: "success",
            message: `${Synonyms.Comment} deleted successfully`,
        });
        expect(setReload).toHaveBeenCalledWith(true);
    });

    it("reports a permission denial", async () => {
        deleteComment.mockResolvedValue(403);
        const { showMessage } = mount();
        await confirmDelete();
        await waitFor(() => expect(showMessage).toHaveBeenCalled());
        expect(lastMessage(showMessage)).toMatchObject({
            type: "error",
            message: `Unable to delete ${Synonyms.comment}. You don't have permission to perform this action.`,
        });
    });

    it("reports a blocked request with its message", async () => {
        deleteComment.mockResolvedValue([false, BLOCKED]);
        const { showMessage } = mount();
        await confirmDelete();
        await waitFor(() => expect(showMessage).toHaveBeenCalled());
        expect(lastMessage(showMessage)).toMatchObject({
            type: "error",
            message: `Unable to delete ${Synonyms.comment}: ${BLOCKED}`,
        });
    });

    it("reports an empty reply as a failure", async () => {
        deleteComment.mockResolvedValue(false);
        const { showMessage, setReload } = mount();
        await confirmDelete();
        await waitFor(() => expect(showMessage).toHaveBeenCalled());
        expect(lastMessage(showMessage)).toMatchObject({
            type: "error",
            message: `Unable to delete ${Synonyms.comment}: Unknown error`,
        });
        expect(setReload).toHaveBeenCalledWith(true);
    });

    it("never confirms a delete that failed", async () => {
        for (const result of [403, [false, BLOCKED], false]) {
            deleteComment.mockResolvedValueOnce(result);
        }
        const { showMessage } = mount();
        for (let i = 0; i < 3; i++) {
            await confirmDelete();
            await waitFor(() => expect(showMessage).toHaveBeenCalledTimes(i + 1));
        }
        expect(showMessage.mock.calls.map(([m]) => m.type)).toEqual(["error", "error", "error"]);
    });
});

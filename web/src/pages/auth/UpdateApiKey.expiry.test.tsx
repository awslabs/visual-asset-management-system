/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Update API Key sends the expiration only when the user changed it, so an edit of the description or
 * the Active toggle saves on a key whose expiration has passed. A self-service key whose window from
 * creation has ended says on the form that its date cannot change.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import UpdateApiKey from "./UpdateApiKey";
import { updateApiKey, updateUserApiKey } from "../../services/APIService";

jest.mock("../../services/APIService", () => ({
    updateApiKey: jest.fn(),
    updateUserApiKey: jest.fn(),
}));

const DAY_MS = 24 * 60 * 60 * 1000;
const isoDaysAgo = (days: number) => new Date(Date.now() - days * DAY_MS).toISOString();

/** A self-service key created 400 days ago that expired 40 days ago. */
const expiredUserKey = {
    apiKeyId: "k1",
    apiKeyName: "old-key", // pragma: allowlist secret
    description: "before",
    isActive: "true",
    createdAt: isoDaysAgo(400),
    expiresAt: isoDaysAgo(40).replace(/\.\d{3}Z$/, "Z"),
};

const modalProps = { open: true, setOpen: jest.fn(), setReload: jest.fn() };

const editDescription = (value: string) => {
    const field = screen.getByTestId("update-api-key-description");
    const textarea = field.tagName === "TEXTAREA" ? field : field.querySelector("textarea");
    fireEvent.change(textarea as Element, { target: { value } });
};

describe("UpdateApiKey expiration", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (updateUserApiKey as jest.Mock).mockResolvedValue([true, {}]);
        (updateApiKey as jest.Mock).mockResolvedValue([true, {}]);
    });

    it("saves a description edit on an expired self-service key without re-sending its expiration", async () => {
        render(<UpdateApiKey {...modalProps} apiKey={expiredUserKey} userMode={true} />);

        editDescription("after");
        fireEvent.click(screen.getByTestId("update-api-key-button"));

        await waitFor(() => expect(updateUserApiKey).toHaveBeenCalledTimes(1));
        const body = (updateUserApiKey as jest.Mock).mock.calls[0][0];
        expect(body).toEqual({ apiKeyId: "k1", description: "after", isActive: "true" });
        expect(body).not.toHaveProperty("expiresAt");
    });

    it("says an expired self-service key's date cannot change once its window has ended", () => {
        render(<UpdateApiKey {...modalProps} apiKey={expiredUserKey} userMode={true} />);
        expect(
            screen.getByText(/expiration window has ended, so its date cannot be changed/)
        ).toBeInTheDocument();
    });

    it("does not send an unchanged admin expiration, or a clear the user did not make", async () => {
        render(
            <UpdateApiKey
                {...modalProps}
                apiKey={{ ...expiredUserKey, expiresAt: undefined }}
                userMode={false}
            />
        );

        editDescription("after");
        fireEvent.click(screen.getByTestId("update-api-key-button"));

        await waitFor(() => expect(updateApiKey).toHaveBeenCalledTimes(1));
        expect((updateApiKey as jest.Mock).mock.calls[0][0]).not.toHaveProperty("expiresAt");
    });

    it("sends the expiration when the admin cleared it", async () => {
        render(
            <UpdateApiKey
                {...modalProps}
                apiKey={{ ...expiredUserKey, expiresAt: "2030-01-02T23:59:59Z" }}
                userMode={false}
            />
        );

        fireEvent.click(screen.getByRole("button", { name: "Clear" }));
        fireEvent.click(screen.getByTestId("update-api-key-button"));

        await waitFor(() => expect(updateApiKey).toHaveBeenCalledTimes(1));
        expect((updateApiKey as jest.Mock).mock.calls[0][0].expiresAt).toBe("");
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The calendar button of the expiration date picker on the Create API Key and Update API Key pages
 * says which date it chooses and, once one is picked, which date is selected.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import CreateApiKey from "./CreateApiKey";
import UpdateApiKey from "./UpdateApiKey";

jest.mock("../../services/APIService", () => ({
    createApiKey: jest.fn(),
    createUserApiKey: jest.fn(),
    updateApiKey: jest.fn(),
    updateUserApiKey: jest.fn(),
}));

const modalProps = { open: true, setOpen: jest.fn(), setReload: jest.fn() };

describe("API key expiration date picker calendar buttons", () => {
    it.each([false, true])("names the Create API Key calendar button (userMode %s)", (userMode) => {
        render(<CreateApiKey {...modalProps} userMode={userMode} />);
        expect(
            screen.getByRole("button", { name: "Choose expiration date", hidden: true })
        ).toBeInTheDocument();
    });

    it("names the Update API Key calendar button", () => {
        render(
            <UpdateApiKey
                {...modalProps}
                apiKey={{ apiKeyId: "k1", apiKeyName: "key", description: "", isActive: "true" }}
            />
        );
        expect(
            screen.getByRole("button", { name: "Choose expiration date", hidden: true })
        ).toBeInTheDocument();
    });

    it("says which expiration date is selected on the Update API Key page", () => {
        render(
            <UpdateApiKey
                {...modalProps}
                apiKey={{
                    apiKeyId: "k1",
                    apiKeyName: "key",
                    description: "",
                    isActive: "true",
                    expiresAt: "2030-01-02T23:59:59Z",
                }}
            />
        );
        expect(
            screen.getByRole("button", {
                name: /^Choose expiration date, selected date is .*2030/,
                hidden: true,
            })
        ).toBeInTheDocument();
    });
});

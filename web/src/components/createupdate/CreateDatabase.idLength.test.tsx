/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The create-database form states and applies the name bounds `POST /database` enforces.
 *
 * The API holds `databaseId` to 4-63 characters of letters, digits, `-` and `_`: the model's
 * `min_length=4` plus the identifier pattern `^[-_a-zA-Z0-9]{3,63}$`. The form's checks are what a
 * user reads first, so each refusal names the rule the value actually broke — a 64-character name of
 * valid characters is refused for its length, not for its characters — and the constraint text under
 * the field states the same range the API reference does.
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import CreateDatabase from "./CreateDatabase";
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({
    createDatabase: jest.fn(),
    updateDatabase: jest.fn(),
    fetchBuckets: jest.fn(),
}));

const { fetchBuckets } = jest.requireMock("../../services/APIService");

const LENGTH_MESSAGE = "Between 4 and 63 characters";
const CHARSET_MESSAGE = "No special characters or spaces except - and _";

async function renderAndType(value: string) {
    render(<CreateDatabase open={true} setOpen={jest.fn()} setReload={jest.fn()} />);
    await waitFor(() => expect(fetchBuckets).toHaveBeenCalled());
    const input = screen.getByPlaceholderText(`${Synonyms.Database} Name`);
    fireEvent.change(input, { target: { value } });
    expect(input).toHaveValue(value);
}

beforeEach(() => {
    jest.clearAllMocks();
    fetchBuckets.mockResolvedValue({ Items: [] });
});

describe("CreateDatabase name bounds", () => {
    it.each([
        ["the 4-character minimum", "d".repeat(4)],
        ["the 63-character maximum", "d".repeat(63)],
    ])("accepts %s", async (_label, value) => {
        await renderAndType(value);
        expect(screen.queryByText(LENGTH_MESSAGE)).toBeNull();
        expect(screen.queryByText(CHARSET_MESSAGE)).toBeNull();
    });

    it.each([
        ["one under the minimum", "d".repeat(3)],
        ["one over the maximum", "d".repeat(64)],
    ])("refuses a name %s for its length", async (_label, value) => {
        await renderAndType(value);
        expect(await screen.findByText(LENGTH_MESSAGE)).toBeInTheDocument();
        expect(screen.queryByText(CHARSET_MESSAGE)).toBeNull();
    });

    it("refuses a name with a disallowed character for its characters", async () => {
        // Control: the character check still fires, so the arms above are not passing because the
        // name field reports nothing at all.
        await renderAndType("my db");
        expect(await screen.findByText(CHARSET_MESSAGE)).toBeInTheDocument();
    });

    it("states the enforced range in the field's constraint text", async () => {
        await renderAndType("valid-name");
        expect(screen.getByText(/4-63 characters/)).toBeInTheDocument();
        expect(screen.queryByText(/max 64/)).toBeNull();
    });
});

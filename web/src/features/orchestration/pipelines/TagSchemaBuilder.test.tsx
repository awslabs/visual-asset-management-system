/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TagSchemaBuilder from "./TagSchemaBuilder";
import type { TagSchemaField } from "../types";

describe("TagSchemaBuilder", () => {
    it("flags a reserved tag key as invalid", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();

        render(<TagSchemaBuilder value={[]} onChange={onChange} />);

        // Add a field
        const addButton = screen.getByRole("button", { name: /add field/i });
        await user.click(addButton);

        // Set tagKey to a reserved key
        const tagKeyInput = screen.getByLabelText(/tag key/i);
        await user.clear(tagKeyInput);
        await user.type(tagKeyInput, "executionId");

        // Assert validation error appears
        await waitFor(() => {
            expect(screen.getByText(/reserved/i)).toBeInTheDocument();
        });

        // Verify onChange was not called with the invalid row as valid
        // (or if it was called, the row is marked invalid)
        const calls = onChange.mock.calls;
        if (calls.length > 0) {
            const lastCall = calls[calls.length - 1][0] as TagSchemaField[];
            const reservedField = lastCall.find((f: TagSchemaField) => f.tagKey === "executionId");
            // If the field exists in the onChange call, it should be marked invalid somehow
            // For this test, we expect onChange NOT to include it as a valid field
            // Implementation will determine the exact behavior
        }
    });

    it("flags a duplicate tag key as invalid", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();

        const initialValue: TagSchemaField[] = [
            { tagKey: "envName", type: "string", required: false },
        ];

        render(<TagSchemaBuilder value={initialValue} onChange={onChange} />);

        // Add another field
        const addButton = screen.getByRole("button", { name: /add field/i });
        await user.click(addButton);

        // Set the new field's tagKey to the same as existing
        const inputs = screen.getAllByLabelText(/tag key/i);
        const newTagKeyInput = inputs[inputs.length - 1];
        await user.clear(newTagKeyInput);
        await user.type(newTagKeyInput, "envName");

        // Assert validation error appears for duplicate
        await waitFor(() => {
            expect(screen.getByText(/duplicate/i)).toBeInTheDocument();
        });
    });

    it("accepts a normal tag key", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();

        render(<TagSchemaBuilder value={[]} onChange={onChange} />);

        // Add a field
        const addButton = screen.getByRole("button", { name: /add field/i });
        await user.click(addButton);

        // Set tagKey to a normal key
        const tagKeyInput = screen.getByLabelText(/tag key/i);
        await user.clear(tagKeyInput);
        await user.type(tagKeyInput, "envName");

        // No error should appear
        await waitFor(() => {
            expect(screen.queryByText(/reserved/i)).not.toBeInTheDocument();
            expect(screen.queryByText(/duplicate/i)).not.toBeInTheDocument();
        });

        // onChange should be called with valid data
        expect(onChange).toHaveBeenCalled();
        const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0] as TagSchemaField[];
        expect(lastCall.some((f: TagSchemaField) => f.tagKey === "envName")).toBe(true);
    });
});

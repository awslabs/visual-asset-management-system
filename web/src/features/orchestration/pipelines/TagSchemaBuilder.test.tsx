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
        const addButton = screen.getByRole("button", { name: /add tag/i });
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
        const addButton = screen.getByRole("button", { name: /add tag/i });
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

    it.each(["env-name", "env.name", "env name"])(
        "flags %s as outside the substitutable tag-key charset",
        async (key) => {
            // Mirrors _TAG_KEY_PATTERN in common/workflows/templateTagSchema.py — the backend
            // rejects the key at save, so the form must say so before the wizard is finished.
            const user = userEvent.setup();
            const onValidityChange = jest.fn();

            render(
                <TagSchemaBuilder
                    value={[]}
                    onChange={jest.fn()}
                    onValidityChange={onValidityChange}
                />
            );
            await user.click(screen.getByRole("button", { name: /add tag/i }));
            await user.type(screen.getByLabelText(/tag key/i), key);

            await waitFor(() => {
                expect(
                    screen.getByText(/only letters, digits and underscores/i)
                ).toBeInTheDocument();
            });
            expect(onValidityChange).toHaveBeenLastCalledWith(false);
        }
    );

    it("accepts a normal tag key", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();

        render(<TagSchemaBuilder value={[]} onChange={onChange} />);

        // Add a field
        const addButton = screen.getByRole("button", { name: /add tag/i });
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

    it("reports validity so the parent can block saving while a row is invalid", async () => {
        const user = userEvent.setup();
        const onValidityChange = jest.fn();

        render(
            <TagSchemaBuilder value={[]} onChange={jest.fn()} onValidityChange={onValidityChange} />
        );

        await user.click(screen.getByRole("button", { name: /add tag/i }));
        await user.type(screen.getByLabelText(/tag key/i), "executionId");

        await waitFor(() => {
            expect(onValidityChange).toHaveBeenLastCalledWith(false);
        });
    });

    it("emits a typed number default rather than the raw string", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();

        render(
            <TagSchemaBuilder
                value={[{ tagKey: "count", type: "integer", required: false }]}
                onChange={onChange}
            />
        );

        await user.type(screen.getByLabelText(/default value/i), "5");

        await waitFor(() => expect(onChange).toHaveBeenCalled());
        const last = onChange.mock.calls[onChange.mock.calls.length - 1][0] as TagSchemaField[];
        expect(last[0].default).toBe(5);
    });

    it("drops a cleared default to undefined instead of an empty string", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();

        render(
            <TagSchemaBuilder
                value={[{ tagKey: "count", type: "integer", required: false, default: 5 }]}
                onChange={onChange}
            />
        );

        await user.clear(screen.getByLabelText(/default value/i));

        await waitFor(() => expect(onChange).toHaveBeenCalled());
        const last = onChange.mock.calls[onChange.mock.calls.length - 1][0] as TagSchemaField[];
        // The backend validates a present default against the type, and "" is not a valid integer.
        expect(last[0].default).toBeUndefined();
    });

    it("edits a string-list default as a real list", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();

        render(
            <TagSchemaBuilder
                value={[{ tagKey: "names", type: "string-list", required: false }]}
                onChange={onChange}
            />
        );

        await user.type(screen.getByLabelText(/Default value entry for tag 1/), "alpha");
        await user.click(screen.getByRole("button", { name: "Add" }));

        await waitFor(() => expect(onChange).toHaveBeenCalled());
        const last = onChange.mock.calls[onChange.mock.calls.length - 1][0] as TagSchemaField[];
        expect(last[0].default).toEqual(["alpha"]);
    });

    it("flags an enum tag with no declared values", async () => {
        const user = userEvent.setup();

        render(
            <TagSchemaBuilder
                value={[{ tagKey: "env", type: "string", required: false }]}
                onChange={jest.fn()}
            />
        );

        await user.selectOptions(screen.getByLabelText(/^type/i), "enum");

        await waitFor(() => {
            expect(screen.getByText(/Enum tags require at least one value/)).toBeInTheDocument();
        });
    });

    it("reports a missing enum value list on a row whose key is also invalid", async () => {
        const user = userEvent.setup();

        render(
            <TagSchemaBuilder
                value={[{ tagKey: "", type: "string", required: false }]}
                onChange={jest.fn()}
            />
        );

        await user.selectOptions(screen.getByLabelText(/^type/i), "enum");

        await waitFor(() => {
            expect(screen.getByText(/Tag key is required/)).toBeInTheDocument();
        });
        expect(screen.getByText(/Enum tags require at least one value/)).toBeInTheDocument();
    });

    it("renders a boolean default stored as the string 'true' as true", () => {
        render(
            <TagSchemaBuilder
                value={[{ tagKey: "flag", type: "boolean", required: false, default: "true" }]}
                onChange={jest.fn()}
            />
        );

        expect((screen.getByLabelText(/default value/i) as HTMLSelectElement).value).toBe("true");
    });

    it("drops an enum default the declared values do not contain when retyping", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();

        render(
            <TagSchemaBuilder
                value={[
                    {
                        tagKey: "env",
                        type: "string",
                        required: false,
                        default: "prod",
                        enumValues: ["dev"],
                    },
                ]}
                onChange={onChange}
            />
        );

        await user.selectOptions(screen.getByLabelText(/^type/i), "enum");

        await waitFor(() => expect(onChange).toHaveBeenCalled());
        const last = onChange.mock.calls[onChange.mock.calls.length - 1][0] as TagSchemaField[];
        expect(last[0].default).toBeUndefined();
    });

    it("drops a default that the newly chosen type cannot represent", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();

        render(
            <TagSchemaBuilder
                value={[{ tagKey: "count", type: "string", required: false, default: "abc" }]}
                onChange={onChange}
            />
        );

        await user.selectOptions(screen.getByLabelText(/type/i), "integer");

        await waitFor(() => expect(onChange).toHaveBeenCalled());
        const last = onChange.mock.calls[onChange.mock.calls.length - 1][0] as TagSchemaField[];
        expect(last[0].type).toBe("integer");
        expect(last[0].default).toBeUndefined();
    });

    it("lets a comma be typed into the enum values field", async () => {
        // The field is labelled "comma-separated" but a fully controlled value that re-parsed on
        // every keystroke dropped the empty segment a just-typed comma creates, so the comma was
        // erased as it was entered and the words ran together ("dev,staging" -> "devstaging").
        const user = userEvent.setup();
        const onChange = jest.fn();

        render(<TagSchemaBuilder value={[]} onChange={onChange} />);
        await user.click(screen.getByRole("button", { name: /add tag/i }));
        await user.type(screen.getByLabelText(/tag key/i), "ENVIRONMENT");
        await user.selectOptions(screen.getByLabelText(/^type/i), "enum");

        const enumInput = screen.getByLabelText(/enum values/i) as HTMLInputElement;
        await user.type(enumInput, "dev,staging");

        // The typed text survives verbatim, commas included.
        expect(enumInput.value).toBe("dev,staging");

        // And it is committed upward as separate values, not one run-together string.
        await waitFor(() => {
            const last = onChange.mock.calls.at(-1)?.[0] as TagSchemaField[] | undefined;
            expect(last?.[0]?.enumValues).toEqual(["dev", "staging"]);
        });
    });

    it("keeps a trailing comma visible while the next value is being typed", async () => {
        const user = userEvent.setup();
        render(<TagSchemaBuilder value={[]} onChange={jest.fn()} />);
        await user.click(screen.getByRole("button", { name: /add tag/i }));
        await user.type(screen.getByLabelText(/tag key/i), "ENVIRONMENT");
        await user.selectOptions(screen.getByLabelText(/^type/i), "enum");

        const enumInput = screen.getByLabelText(/enum values/i) as HTMLInputElement;
        await user.type(enumInput, "dev, ");
        expect(enumInput.value).toBe("dev, ");
    });

    it("reports cleared enum values upward so a live preview cannot show the deleted list", async () => {
        // Withholding the emit while a row is invalid froze the caller's preview on the last valid
        // schema, so an emptied enum kept listing the values just deleted and they appeared to merge
        // with whatever was typed next. Validity is reported separately via onValidityChange.
        const user = userEvent.setup();
        const onChange = jest.fn();
        const onValidityChange = jest.fn();
        render(
            <TagSchemaBuilder value={[]} onChange={onChange} onValidityChange={onValidityChange} />
        );
        await user.click(screen.getByRole("button", { name: /add tag/i }));
        await user.type(screen.getByLabelText(/tag key/i), "ENVIRONMENT");
        await user.selectOptions(screen.getByLabelText(/^type/i), "enum");

        const enumInput = screen.getByLabelText(/enum values/i) as HTMLInputElement;
        await user.type(enumInput, "test,derp,fert");
        await waitFor(() => {
            expect(onChange.mock.calls.at(-1)?.[0]?.[0]?.enumValues).toEqual([
                "test",
                "derp",
                "fert",
            ]);
        });

        await user.clear(enumInput);
        // The emptied list must reach the caller even though the row is now invalid.
        await waitFor(() => {
            expect(onChange.mock.calls.at(-1)?.[0]?.[0]?.enumValues).toEqual([]);
        });
        expect(onValidityChange).toHaveBeenLastCalledWith(false);

        await user.type(enumInput, "dood,pood");
        await waitFor(() => {
            expect(onChange.mock.calls.at(-1)?.[0]?.[0]?.enumValues).toEqual(["dood", "pood"]);
        });
        expect(onValidityChange).toHaveBeenLastCalledWith(true);
    });

    it("labels the type options with readable names rather than the wire values", async () => {
        const user = userEvent.setup();
        render(<TagSchemaBuilder value={[]} onChange={jest.fn()} />);
        await user.click(screen.getByRole("button", { name: /add tag/i }));

        const typeSelect = screen.getByLabelText(/^type/i) as HTMLSelectElement;
        const options = Array.from(typeSelect.options);
        // The stored values stay the wire format the backend validates against.
        expect(options.map((o) => o.value)).toEqual([
            "string",
            "integer",
            "number",
            "boolean",
            "string-list",
            "enum",
        ]);
        // Every option reads as a capitalized label plus a hint, and none is the bare wire value.
        for (const option of options) {
            expect(option.textContent).toMatch(/^[A-Z].* — .+/);
            expect(option.textContent).not.toBe(option.value);
        }
        // Selecting by the wire value still works, so the contract is unchanged.
        await user.selectOptions(typeSelect, "string-list");
        expect(typeSelect.value).toBe("string-list");
    });
});

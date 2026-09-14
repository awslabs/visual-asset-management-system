/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DynamicTagForm, {
    tagSchemaToJsonSchema,
    formDataToTags,
    spansFullRow,
} from "./DynamicTagForm";
import type { TagSchemaField } from "../types";

describe("DynamicTagForm converters", () => {
    it("tagSchemaToJsonSchema maps the 6 types + required + enum", () => {
        const { schema } = tagSchemaToJsonSchema([
            { tagKey: "env", type: "enum", required: true, enumValues: ["a", "b"] },
            { tagKey: "n", type: "integer" },
            { tagKey: "flag", type: "boolean" },
            { tagKey: "list", type: "string-list" },
        ] as TagSchemaField[]);
        expect(schema.required).toContain("env");
        expect(schema.properties.env.enum).toEqual(["a", "b"]);
        expect(schema.properties.n.type).toBe("integer");
        expect(schema.properties.flag.type).toBe("boolean");
        expect(schema.properties.list.type).toBe("array");
    });

    it("tagSchemaToJsonSchema leaves a required string-list without minItems", () => {
        const { schema } = tagSchemaToJsonSchema([
            { tagKey: "regions", type: "string-list", required: true },
        ] as TagSchemaField[]);
        // minItems would make RJSF seed a placeholder row, which reads as a supplied value; the
        // empty-list check in resolveTemplate is what enforces the requirement.
        expect(schema.properties.regions.minItems).toBeUndefined();
    });

    it("tagSchemaToJsonSchema stringifies non-string enum members to match the declared type", () => {
        const { schema } = tagSchemaToJsonSchema([
            { tagKey: "quality", type: "enum", enumValues: [1, 2, 3] },
        ] as unknown as TagSchemaField[]);
        expect(schema.properties.quality.type).toBe("string");
        expect(schema.properties.quality.enum).toEqual(["1", "2", "3"]);
    });

    it("merges an externally supplied uiSchema without dropping the submit-button suppression", () => {
        render(
            <DynamicTagForm
                schema={[{ tagKey: "env", type: "string" }] as TagSchemaField[]}
                uiSchema={{ env: { "ui:widget": "textarea" } }}
            />
        );
        expect(screen.queryByRole("button", { name: /submit/i })).not.toBeInTheDocument();
    });

    it("formDataToTags flattens to {key,value}[]", () => {
        expect(formDataToTags({ env: "a", n: 3 })).toEqual([
            { key: "env", value: "a" },
            { key: "n", value: 3 },
        ]);
    });
});

describe("DynamicTagForm string-list controls", () => {
    it("renders labelled Add/Remove controls carrying the module button styles", async () => {
        const onChange = jest.fn();
        render(
            <DynamicTagForm
                schema={[{ tagKey: "extraArgs", type: "string-list" }] as TagSchemaField[]}
                formData={{ extraArgs: [] }}
                onChange={onChange}
            />
        );

        const addButton = screen.getByRole("button", { name: /Add item/ });
        // RJSF's default Bootstrap button carries no text and no module styling.
        expect(addButton).toHaveTextContent("Add item");
        expect(addButton.className).toContain("px-4");
        expect(addButton.querySelector(".glyphicon")).toBeNull();

        await userEvent.click(addButton);

        expect(screen.getByRole("button", { name: /Remove/ })).toHaveTextContent("Remove");
    });
});

describe("DynamicTagForm field presentation", () => {
    const FIELDS = [
        {
            tagKey: "PROMPT",
            type: "string",
            label: "Prompt",
            description: "The generation prompt. Leave blank to use the asset's metadata.",
        },
        { tagKey: "STEPS", type: "integer", label: "Steps", required: true },
    ] as TagSchemaField[];

    it("gives each tag a real label bound to its control", () => {
        // The defect: RJSF's default FieldTemplate rendered name + description + input as one
        // undifferentiated run of text, so a tag read as prose rather than as a form field.
        render(<DynamicTagForm schema={FIELDS} formData={{}} onChange={jest.fn()} />);
        const prompt = screen.getByLabelText(/Prompt/);
        expect(prompt.tagName.toLowerCase()).toBe("input");
    });

    it("renders the description as secondary text, separate from the label", () => {
        render(<DynamicTagForm schema={FIELDS} formData={{}} onChange={jest.fn()} />);
        const description = screen.getByText(/The generation prompt/);
        expect(description.className).toContain("text-text-secondary");
        // Not inside the label — that is what made the two read as one block.
        expect(description.closest("label")).toBeNull();
    });

    it("gives every control a painted border, so it is visible in light mode", () => {
        // Tailwind's preflight is disabled module-wide: the scoped reset sets border-width 0 on bare
        // inputs, so a control with no border utility was white-on-white in light mode. `orch-outline`
        // plus an explicit border utility is what makes it visible.
        render(<DynamicTagForm schema={FIELDS} formData={{}} onChange={jest.fn()} />);
        for (const label of [/Prompt/, /Steps/]) {
            const control = screen.getByLabelText(label);
            expect(control.className).toContain("orch-outline");
            expect(control.className).toContain("border-border-input");
            expect(control.className).toContain("bg-surface-input");
        }
    });

    it("marks a required tag", () => {
        render(<DynamicTagForm schema={FIELDS} formData={{}} onChange={jest.fn()} />);
        const stepsLabel = screen.getByText("Steps").closest("label");
        expect(stepsLabel?.textContent).toContain("*");
    });

    it("reports a typed number rather than a string for an integer tag", () => {
        // The backend validates the declared type, so an integer field must not submit "7".
        const onChange = jest.fn();
        render(<DynamicTagForm schema={FIELDS} formData={{}} onChange={onChange} />);
        fireEvent.change(screen.getByLabelText(/Steps/), { target: { value: "7" } });
        // This component's onChange passes the form DATA directly, not RJSF's {formData} envelope.
        const last = onChange.mock.calls.at(-1)?.[0];
        expect(last?.STEPS).toBe(7);
        // A real number, not "7": the backend validates the declared type.
        expect(typeof last?.STEPS).toBe("number");
    });

    it("offers an explicit empty choice for an optional enum", () => {
        // Without it the first member looks pre-selected when the user has chosen nothing.
        render(
            <DynamicTagForm
                schema={
                    [
                        { tagKey: "SIZE", type: "enum", enumValues: ["2B", "14B"] },
                    ] as TagSchemaField[]
                }
                formData={{}}
                onChange={jest.fn()}
            />
        );
        expect(screen.getByRole("option", { name: "Not set" })).toBeInTheDocument();
    });

    it("renders a boolean tag as a checkbox with a readable state", () => {
        render(
            <DynamicTagForm
                schema={
                    [{ tagKey: "FAST", type: "boolean", label: "Fast mode" }] as TagSchemaField[]
                }
                formData={{ FAST: true }}
                onChange={jest.fn()}
            />
        );
        expect(screen.getByText("Enabled")).toBeInTheDocument();
        // RJSF hands a boolean's title to the widget (displayLabel is false for a checkbox), so the
        // tag's name is lost unless the widget renders it.
        expect(screen.getByRole("checkbox", { name: /Fast mode/ })).toBeChecked();
    });

    it("shows the {{tagKey}} placeholder each field fills, beside its label and in a checkbox title", () => {
        render(
            <DynamicTagForm
                schema={
                    [
                        { tagKey: "PROMPT", type: "string", label: "Prompt" },
                        { tagKey: "FAST", type: "boolean", label: "Fast mode" },
                    ] as TagSchemaField[]
                }
                formData={{ PROMPT: "", FAST: true }}
                onChange={jest.fn()}
            />
        );
        const chips = screen.getAllByTestId("tag-placeholder").map((c) => c.textContent);
        expect(chips).toEqual(["{{PROMPT}}", "{{FAST}}"]);
        expect(screen.getByRole("checkbox", { name: /Fast mode/ })).toBeInTheDocument();
    });

    it("keeps the placeholder chip when an external uiSchema customises the same field", () => {
        render(
            <DynamicTagForm
                schema={[{ tagKey: "NOTES", type: "string", label: "Notes" }] as TagSchemaField[]}
                uiSchema={{ NOTES: { "ui:widget": "textarea" } }}
                formData={{ NOTES: "" }}
                onChange={jest.fn()}
            />
        );
        expect(screen.getByTestId("tag-placeholder")).toHaveTextContent("{{NOTES}}");
        expect(screen.getByRole("textbox", { name: /Notes/ }).tagName).toBe("TEXTAREA");
    });

    it("renders a boolean tag's description after the checkbox, not above an unnamed control", () => {
        const { container } = render(
            <DynamicTagForm
                schema={
                    [
                        {
                            tagKey: "FAST",
                            type: "boolean",
                            label: "Fast mode",
                            description: "Skips the slow passes.",
                        },
                    ] as TagSchemaField[]
                }
                formData={{ FAST: false }}
                onChange={jest.fn()}
            />
        );
        const box = screen.getByRole("checkbox", { name: /Fast mode/ });
        const guidance = screen.getByText("Skips the slow passes.");
        // eslint-disable-next-line no-bitwise
        expect(
            box.compareDocumentPosition(guidance) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
        expect(container.textContent).toContain("Disabled");
    });
});

/**
 * The pipeline step lays template inputs on a two-column grid. The switch travels through RJSF's
 * `formContext` because the templates object is a module constant a prop cannot reach; a field that
 * needs the width (a list, a long description, a textarea, or an explicit hint) takes the full row.
 */
describe("DynamicTagForm grid layout", () => {
    const LONG = "x".repeat(121);
    const SCHEMA = [
        { tagKey: "short", type: "string", label: "Short" },
        { tagKey: "list", type: "string-list", label: "List" },
        { tagKey: "wordy", type: "string", label: "Wordy", description: LONG },
        { tagKey: "multiline", type: "string", label: "Multiline" },
        { tagKey: "wide", type: "string", label: "Wide" },
    ] as TagSchemaField[];

    const cell = (container: HTMLElement, name: string) =>
        container.querySelector(`[data-tag-field="${name}"]`) as HTMLElement;

    it("stacks fields by default (no formContext)", () => {
        const { container } = render(<DynamicTagForm schema={SCHEMA} formData={{}} />);
        const wrapper = screen.getByTestId("tag-form-fields");
        expect(wrapper.className).toContain("space-y-3");
        expect(wrapper.className).not.toContain("grid-cols-2");
        expect(cell(container, "list").className).not.toContain("col-span-2");
    });

    it("lays fields on a two-column grid when asked, spanning the ones that need width", () => {
        const { container } = render(
            <DynamicTagForm
                schema={SCHEMA}
                formData={{}}
                formContext={{ layout: "grid" }}
                uiSchema={{
                    multiline: { "ui:widget": "textarea" },
                    wide: { "ui:colSpan": 2 },
                }}
            />
        );
        expect(screen.getByTestId("tag-form-fields").className).toContain("md:grid-cols-2");
        expect(cell(container, "short").className).not.toContain("md:col-span-2");
        expect(cell(container, "list").className).toContain("md:col-span-2");
        expect(cell(container, "wordy").className).toContain("md:col-span-2");
        expect(cell(container, "multiline").className).toContain("md:col-span-2");
        expect(cell(container, "wide").className).toContain("md:col-span-2");
    });

    it("decides the span from the schema and uiSchema alone", () => {
        const schema = {
            properties: {
                a: { type: "array" },
                b: { type: "string", description: LONG },
                c: { type: "string", description: "short" },
                d: { type: "string" },
            },
        };
        expect(spansFullRow("a", schema, {})).toBe(true);
        expect(spansFullRow("b", schema, {})).toBe(true);
        expect(spansFullRow("c", schema, {})).toBe(false);
        expect(spansFullRow("d", schema, { d: { "ui:widget": "textarea" } })).toBe(true);
        expect(spansFullRow("d", schema, { d: { "ui:colSpan": 2 } })).toBe(true);
        expect(spansFullRow("d", schema, {})).toBe(false);
    });
});

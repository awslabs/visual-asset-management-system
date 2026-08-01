/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DynamicTagForm, { tagSchemaToJsonSchema, formDataToTags } from "./DynamicTagForm";
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

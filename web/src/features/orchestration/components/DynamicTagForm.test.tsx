/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { tagSchemaToJsonSchema, formDataToTags } from "./DynamicTagForm";
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

    it("formDataToTags flattens to {key,value}[]", () => {
        expect(formDataToTags({ env: "a", n: 3 })).toEqual([
            { key: "env", value: "a" },
            { key: "n", value: 3 },
        ]);
    });
});

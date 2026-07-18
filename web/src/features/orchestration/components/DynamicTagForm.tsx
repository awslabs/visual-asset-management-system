/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import type { TagSchemaField } from "../types";

interface DynamicTagFormProps {
    schema: TagSchemaField[];
    uiSchema?: any;
    formData?: any;
    onChange?: (data: any) => void;
    onSubmit?: (data: any) => void;
}

export function tagSchemaToJsonSchema(fields: TagSchemaField[]): { schema: any; uiSchema: any } {
    const schema: any = {
        type: "object",
        properties: {},
        required: [],
    };

    const uiSchema: any = {};

    for (const field of fields) {
        const prop: any = {};

        // Map type
        switch (field.type) {
            case "string":
                prop.type = "string";
                break;
            case "integer":
                prop.type = "integer";
                break;
            case "number":
                prop.type = "number";
                break;
            case "boolean":
                prop.type = "boolean";
                break;
            case "string-list":
                prop.type = "array";
                prop.items = { type: "string" };
                break;
            case "enum":
                prop.type = "string";
                if (field.enumValues) {
                    prop.enum = field.enumValues;
                }
                break;
        }

        // Add optional fields
        if (field.label) {
            prop.title = field.label;
        }
        if (field.description) {
            prop.description = field.description;
        }
        if (field.default !== undefined) {
            prop.default = field.default;
        }

        schema.properties[field.tagKey] = prop;

        // Add to required array if required
        if (field.required) {
            schema.required.push(field.tagKey);
        }
    }

    return { schema, uiSchema };
}

export function formDataToTags(data: Record<string, any>): { key: string; value: any }[] {
    return Object.entries(data).map(([key, value]) => ({ key, value }));
}

const DynamicTagForm: React.FC<DynamicTagFormProps> = ({
    schema: tagSchema,
    uiSchema: externalUiSchema,
    formData,
    onChange,
    onSubmit,
}) => {
    const { schema, uiSchema } = tagSchemaToJsonSchema(tagSchema);
    const finalUiSchema = externalUiSchema || uiSchema;

    const handleSubmit = (data: any) => {
        if (onSubmit) {
            onSubmit(data.formData);
        }
    };

    const handleChange = (data: any) => {
        if (onChange) {
            onChange(data.formData);
        }
    };

    return (
        <Form
            schema={schema}
            uiSchema={finalUiSchema}
            formData={formData}
            validator={validator}
            onChange={handleChange}
            onSubmit={handleSubmit}
        />
    );
};

export default DynamicTagForm;

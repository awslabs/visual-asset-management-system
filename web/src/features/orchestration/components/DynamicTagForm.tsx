/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import { getSubmitButtonOptions } from "@rjsf/utils";
import type { IconButtonProps, SubmitButtonProps } from "@rjsf/utils";
import type { TagSchemaField } from "../types";
import { btnPrimary, btnSecondary } from "./controlStyles";

interface DynamicTagFormProps {
    schema: TagSchemaField[];
    uiSchema?: any;
    formData?: any;
    onChange?: (data: any) => void;
    onSubmit?: (data: any) => void;
}

/**
 * RJSF's built-in button templates emit Bootstrap 3 markup — a `btn btn-*` class plus a glyphicon
 * `<i>` as the only child — and neither Bootstrap nor the glyphicon font is part of this module, so
 * those buttons render unlabeled and unstyled. These replacements carry text labels and the shared
 * orchestration control styles.
 */
type TagFormButtonProps = IconButtonProps & { style?: React.CSSProperties };

/** Strip the RJSF-only props so the rest can be spread onto a DOM button. */
function domButtonProps({
    icon,
    iconType,
    uiSchema,
    registry,
    className,
    style,
    ...rest
}: TagFormButtonProps) {
    return rest;
}

const smallButton =
    "px-2 py-1 text-xs font-medium rounded border border-border-input " +
    "bg-surface text-text-primary hover:bg-surface-hover disabled:opacity-50 " +
    "disabled:cursor-not-allowed";

const TagFormAddButton: React.FC<TagFormButtonProps> = (props) => (
    <button type="button" {...domButtonProps(props)} className={`${btnSecondary} mt-2`}>
        Add item
    </button>
);

const TagFormRemoveButton: React.FC<TagFormButtonProps> = (props) => (
    <button
        type="button"
        {...domButtonProps(props)}
        className={`${smallButton} text-red-700 dark:text-red-400`}
    >
        Remove
    </button>
);

const TagFormMoveUpButton: React.FC<TagFormButtonProps> = (props) => (
    <button type="button" {...domButtonProps(props)} className={smallButton}>
        Move up
    </button>
);

const TagFormMoveDownButton: React.FC<TagFormButtonProps> = (props) => (
    <button type="button" {...domButtonProps(props)} className={smallButton}>
        Move down
    </button>
);

const TagFormCopyButton: React.FC<TagFormButtonProps> = (props) => (
    <button type="button" {...domButtonProps(props)} className={smallButton}>
        Copy
    </button>
);

const TagFormSubmitButton: React.FC<SubmitButtonProps> = ({ uiSchema }) => {
    const {
        submitText,
        norender,
        props: submitButtonProps = {},
    } = getSubmitButtonOptions(uiSchema);
    if (norender) {
        return null;
    }
    return (
        <div className="mt-2">
            <button type="submit" {...submitButtonProps} className={btnPrimary}>
                {submitText}
            </button>
        </div>
    );
};

const tagFormTemplates = {
    ButtonTemplates: {
        AddButton: TagFormAddButton,
        RemoveButton: TagFormRemoveButton,
        MoveUpButton: TagFormMoveUpButton,
        MoveDownButton: TagFormMoveDownButton,
        CopyButton: TagFormCopyButton,
        SubmitButton: TagFormSubmitButton,
    },
};

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
                    // The backend compares a submitted enum value as text (str(v)), so members are
                    // carried as strings to keep them consistent with the declared type.
                    prop.enum = field.enumValues.map((v) => (v === null ? "" : String(v)));
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
    // Hide RJSF's built-in Submit button when this is a read-only preview (no onSubmit handler) —
    // the preview only shows the fields, not a submittable form.
    const finalUiSchema = {
        ...uiSchema,
        ...externalUiSchema,
        ...(onSubmit ? {} : { "ui:submitButtonOptions": { norender: true } }),
    };

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
            templates={tagFormTemplates}
            onChange={handleChange}
            onSubmit={handleSubmit}
        />
    );
};

export default DynamicTagForm;

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

/**
 * Field chrome for one tag. RJSF's default FieldTemplate emits a bare label + control with no styling,
 * which rendered every tag as one undifferentiated run of text — the name, the description and the
 * input all reading as prose rather than as a form.
 *
 * It also caused an invisible control in LIGHT mode: Tailwind's preflight is disabled module-wide, so
 * the scoped reset in styles/tailwind.css sets `border-width: 0` on bare input/select/textarea. A
 * control with no border utility therefore painted NO border at all — white-on-white on light, while
 * dark mode still read because its fill differs from the panel. The widget classes below supply the
 * border explicitly.
 */
const TagFieldTemplate = (props: any) => {
    const { id, label, required, description, errors, children, hidden, schema, displayLabel } =
        props;
    if (hidden) return <div className="hidden">{children}</div>;

    // The object wrapper and array items carry no label of their own; rendering the chrome for them
    // would add an empty bordered row around the real fields.
    const isContainer = schema?.type === "object" || schema?.type === "array";
    if (isContainer) {
        return (
            <div className="space-y-3">
                {children}
                {errors}
            </div>
        );
    }

    return (
        <div className="orch-outline rounded-md border border-border-default bg-surface-secondary p-3">
            {displayLabel !== false && label && (
                <label
                    htmlFor={id}
                    className="block text-sm font-semibold text-text-primary mb-0.5"
                >
                    {label}
                    {required && <span className="ml-1 text-vams-error">*</span>}
                </label>
            )}
            {/* Instructions read as guidance, distinct from the label above and the control below. */}
            {description && <div className="text-xs text-text-secondary mb-2">{description}</div>}
            {children}
            {errors}
        </div>
    );
};

/** The tag description. RJSF renders this through its own template, so restyling has to happen HERE —
 *  wrapping props.description in the field template leaves RJSF's markup (class "field-description")
 *  untouched, which is what kept the description looking like part of the same text block. */
const TagDescriptionFieldTemplate = (props: any) => {
    const text = props.description;
    if (!text) return null;
    return <div className="text-xs text-text-secondary mb-2">{text}</div>;
};

/** The object wrapper: tags stacked with real separation instead of running together. */
const TagObjectFieldTemplate = (props: any) => (
    <div className="space-y-3">
        {props.properties.map((element: any) => (
            <div key={element.name}>{element.content}</div>
        ))}
    </div>
);

/** Shared control chrome. `orch-outline` is what opts a control into a painted border. */
const widgetClass =
    "orch-outline w-full px-3 py-2 text-sm rounded border border-border-input " +
    "bg-surface-input text-text-primary focus:outline-none focus:ring-2 focus:ring-blue-500 " +
    "disabled:opacity-50";

const TagTextWidget = (props: any) => (
    <input
        id={props.id}
        type={
            props.schema?.type === "integer" || props.schema?.type === "number" ? "number" : "text"
        }
        className={widgetClass}
        value={props.value ?? ""}
        required={props.required}
        disabled={props.disabled || props.readonly}
        placeholder={props.placeholder}
        onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return props.onChange(undefined);
            if (props.schema?.type === "integer") {
                const n = parseInt(raw, 10);
                return props.onChange(Number.isNaN(n) ? undefined : n);
            }
            if (props.schema?.type === "number") {
                const n = parseFloat(raw);
                return props.onChange(Number.isNaN(n) ? undefined : n);
            }
            props.onChange(raw);
        }}
    />
);

const TagSelectWidget = (props: any) => {
    const options = props.options?.enumOptions || [];
    return (
        <select
            id={props.id}
            className={widgetClass}
            value={props.value ?? ""}
            required={props.required}
            disabled={props.disabled || props.readonly}
            onChange={(e) => props.onChange(e.target.value === "" ? undefined : e.target.value)}
        >
            {/* An optional enum needs an explicit empty choice, or the first member looks pre-selected
                when the user has not chosen anything. */}
            {!props.required && <option value="">Not set</option>}
            {options.map((o: any) => (
                <option key={String(o.value)} value={o.value}>
                    {o.label}
                </option>
            ))}
        </select>
    );
};

const TagCheckboxWidget = (props: any) => (
    <label className="inline-flex items-center gap-2 text-sm text-text-primary">
        <input
            id={props.id}
            type="checkbox"
            checked={!!props.value}
            disabled={props.disabled || props.readonly}
            onChange={(e) => props.onChange(e.target.checked)}
        />
        <span>{props.value ? "Enabled" : "Disabled"}</span>
    </label>
);

const TagTextareaWidget = (props: any) => (
    <textarea
        id={props.id}
        rows={3}
        className={widgetClass}
        value={props.value ?? ""}
        required={props.required}
        disabled={props.disabled || props.readonly}
        onChange={(e) => props.onChange(e.target.value === "" ? undefined : e.target.value)}
    />
);

const tagFormWidgets = {
    TextWidget: TagTextWidget,
    SelectWidget: TagSelectWidget,
    CheckboxWidget: TagCheckboxWidget,
    TextareaWidget: TagTextareaWidget,
};

const tagFormTemplates = {
    FieldTemplate: TagFieldTemplate,
    ObjectFieldTemplate: TagObjectFieldTemplate,
    DescriptionFieldTemplate: TagDescriptionFieldTemplate,
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
            widgets={tagFormWidgets}
            onChange={handleChange}
            onSubmit={handleSubmit}
        />
    );
};

export default DynamicTagForm;

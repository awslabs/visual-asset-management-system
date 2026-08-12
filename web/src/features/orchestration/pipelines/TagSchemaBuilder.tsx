/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import type { TagSchemaField, TagType } from "../types";
import { isReservedTagKey } from "../reservedTagKeys";
import StringListInput from "../components/StringListInput";

interface TagSchemaBuilderProps {
    value: TagSchemaField[];
    onChange: (value: TagSchemaField[]) => void;
    /** Reports whether every row is currently valid. Invalid rows are withheld from onChange. */
    onValidityChange?: (valid: boolean) => void;
}

/**
 * The comma-separated enum-values input.
 *
 * It keeps the typed TEXT in local state and reports the parsed array upward, because a fully
 * controlled `value={values.join(", ")}` cannot be typed into: parsing on every keystroke drops the
 * empty segment a just-typed comma creates, so the comma is erased as it is entered and the words
 * run together ("dev," -> "dev" -> "devstaging"). Re-deriving the text only when the incoming array
 * differs from what this draft parses to keeps an external reset (row removal, type change) working
 * without fighting the user mid-word.
 */
const EnumValuesInput: React.FC<{
    id: string;
    values: string[];
    onChange: (values: string[]) => void;
    className: string;
}> = ({ id, values, onChange, className }) => {
    const parse = (text: string): string[] =>
        text
            .split(",")
            .map((v) => v.trim())
            .filter((v) => v);
    const [text, setText] = useState(() => values.join(", "));
    useEffect(() => {
        const incoming = values.join(", ");
        if (parse(text).join(", ") !== incoming) setText(incoming);
        // Intentionally keyed on the committed values only: including `text` would overwrite the
        // draft on every keystroke, which is the bug this component exists to fix.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [values]);
    return (
        <input
            id={id}
            type="text"
            value={text}
            onChange={(e) => {
                setText(e.target.value);
                onChange(parse(e.target.value));
            }}
            className={className}
            placeholder="e.g., dev, staging, prod"
        />
    );
};

interface ValidationError {
    index: number;
    message: string;
    /** Which input the message belongs beside. Defaults to the tag key. */
    field?: "tagKey" | "enumValues";
}

const TAG_TYPES: TagType[] = ["string", "integer", "number", "boolean", "string-list", "enum"];

/**
 * Display labels for the type selector. The stored values are the wire format the backend validates
 * against, so only the presentation changes here. Each entry carries a short hint describing what the
 * execute form renders and what the tag accepts, shown alongside the label in the open list.
 */
const TAG_TYPE_LABELS: Record<TagType, { label: string; hint: string }> = {
    string: { label: "String", hint: "any single-line text value" },
    integer: { label: "Number", hint: "no decimals" },
    number: { label: "Decimal", hint: "decimals allowed" },
    boolean: { label: "Boolean", hint: "true / false checkbox" },
    "string-list": { label: "String Multi-line", hint: "several line values" },
    enum: { label: "List", hint: "pick one of the values from a list" },
};

// Mirrors _TAG_KEY_PATTERN in common/workflows/templateTagSchema.py: only these characters are
// captured by a {{tag}} placeholder, so a key outside the set can be declared but never rendered.
export const TAG_KEY_PATTERN = /^[A-Za-z0-9_]+$/;

/**
 * Coerce a default-value editor input to the tag's declared type. A blank input carries no default,
 * so it resolves to undefined rather than "" (the backend validates a present default against the
 * type, and "" is not a valid integer/number/boolean/list/enum value).
 */
const coerceDefault = (type: TagType, raw: string): any => {
    if (raw === "") return undefined;
    if (type === "integer" || type === "number") {
        const parsed = Number(raw);
        return Number.isNaN(parsed) ? raw : parsed;
    }
    if (type === "boolean") return raw === "true";
    return raw;
};

/** Re-read an existing default through a newly chosen type, dropping it when it cannot represent it. */
const retypeDefault = (type: TagType, current: any, enumValues?: any[]): any => {
    if (current === undefined || current === null || current === "") return undefined;
    if (type === "string-list") return Array.isArray(current) ? current : undefined;
    if (Array.isArray(current)) return undefined;
    if (type === "boolean") {
        if (typeof current === "boolean") return current;
        const text = String(current).toLowerCase();
        return text === "true" || text === "false" ? text === "true" : undefined;
    }
    if (type === "integer" || type === "number") {
        const parsed = Number(current);
        return Number.isNaN(parsed) ? undefined : parsed;
    }
    if (type === "enum") {
        const text = String(current);
        return (enumValues || []).map(String).includes(text) ? text : undefined;
    }
    return String(current);
};

const TagSchemaBuilder: React.FC<TagSchemaBuilderProps> = ({
    value,
    onChange,
    onValidityChange,
}) => {
    const [fields, setFields] = useState<TagSchemaField[]>(value);
    const [errors, setErrors] = useState<ValidationError[]>([]);

    useEffect(() => {
        setFields(value);
    }, [value]);

    useEffect(() => {
        onValidityChange?.(errors.length === 0);
    }, [errors, onValidityChange]);

    const validateFields = (fieldsList: TagSchemaField[]): ValidationError[] => {
        const newErrors: ValidationError[] = [];
        const tagKeys = new Set<string>();

        fieldsList.forEach((field, index) => {
            // Check for empty tagKey
            if (!field.tagKey || field.tagKey.trim() === "") {
                newErrors.push({ index, message: "Tag key is required" });
            } else if (!TAG_KEY_PATTERN.test(field.tagKey)) {
                newErrors.push({
                    index,
                    message:
                        "Tag key may contain only letters, digits and underscores so a " +
                        "{{tagKey}} placeholder can be substituted",
                });
            } else if (isReservedTagKey(field.tagKey)) {
                newErrors.push({
                    index,
                    message: "Tag key is reserved by the system and cannot be used",
                });
            } else if (tagKeys.has(field.tagKey)) {
                // Check for duplicates
                newErrors.push({ index, message: "Duplicate tag key" });
            } else {
                tagKeys.add(field.tagKey);
            }

            // An enum tag renders as a picker, so it needs at least one declared value
            if (field.type === "enum" && !(field.enumValues || []).length) {
                newErrors.push({
                    index,
                    field: "enumValues",
                    message: "Enum tags require at least one value",
                });
            }
        });

        return newErrors;
    };

    const handleFieldChange = (index: number, updates: Partial<TagSchemaField>) => {
        const updatedFields = [...fields];
        updatedFields[index] = { ...updatedFields[index], ...updates };
        setFields(updatedFields);

        const validationErrors = validateFields(updatedFields);
        setErrors(validationErrors);

        // Emit on every edit, valid or not, so the caller's live preview always mirrors the editor.
        // Withholding an invalid schema froze the preview on the last valid one: clearing an enum's
        // values leaves it momentarily invalid, so the preview kept listing the values just deleted
        // and appeared to merge them with whatever was typed next. Validity travels separately
        // through onValidityChange, which is what gates advancing the wizard and saving.
        onChange(updatedFields);
    };

    const handleAddField = () => {
        const newField: TagSchemaField = {
            tagKey: "",
            type: "string",
            required: false,
        };
        const updatedFields = [...fields, newField];
        setFields(updatedFields);

        const validationErrors = validateFields(updatedFields);
        setErrors(validationErrors);

        onChange(updatedFields);
    };

    const handleRemoveField = (index: number) => {
        const updatedFields = fields.filter((_, i) => i !== index);
        setFields(updatedFields);

        const validationErrors = validateFields(updatedFields);
        setErrors(validationErrors);

        onChange(updatedFields);
    };

    const getErrorForField = (
        index: number,
        field: ValidationError["field"] = "tagKey"
    ): string | null => {
        const error = errors.find((e) => e.index === index && (e.field || "tagKey") === field);
        return error ? error.message : null;
    };

    return (
        <div className="space-y-3">
            {fields.length === 0 && (
                <div className="rounded-lg border border-dashed border-border-default bg-surface-secondary p-6 text-center text-sm text-text-secondary">
                    No tags yet. Add a tag for each <code>{"{{placeholder}}"}</code> in the config
                    body — each becomes a field on the execute form.
                </div>
            )}
            {fields.map((field, index) => {
                const error = getErrorForField(index);
                return (
                    <div
                        key={index}
                        className="rounded-lg border border-border-default bg-surface-container p-4 space-y-3"
                    >
                        <div className="flex items-center justify-between border-b border-border-default pb-2">
                            <span className="text-sm font-semibold text-text-primary">
                                {field.tagKey ? (
                                    <code>{`{{${field.tagKey}}}`}</code>
                                ) : (
                                    `Tag ${index + 1}`
                                )}
                                <span className="ml-2 px-2 py-0.5 text-xs rounded bg-surface-secondary text-text-secondary">
                                    {TAG_TYPE_LABELS[field.type]?.label || field.type}
                                </span>
                                {field.required && (
                                    <span className="ml-1 px-2 py-0.5 text-xs rounded bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200">
                                        required
                                    </span>
                                )}
                            </span>
                            <button
                                type="button"
                                onClick={() => handleRemoveField(index)}
                                className="text-sm text-red-600 dark:text-red-400 hover:underline"
                            >
                                Remove
                            </button>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label
                                    htmlFor={`tagKey-${index}`}
                                    className="block text-sm font-medium text-text-primary mb-1"
                                >
                                    Tag Key *
                                </label>
                                <input
                                    id={`tagKey-${index}`}
                                    type="text"
                                    value={field.tagKey}
                                    onChange={(e) =>
                                        handleFieldChange(index, { tagKey: e.target.value })
                                    }
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                    placeholder="e.g., envName"
                                />
                                {error && <p className="mt-1 text-sm text-vams-error">{error}</p>}
                            </div>
                            <div>
                                <label
                                    htmlFor={`type-${index}`}
                                    className="block text-sm font-medium text-text-primary mb-1"
                                >
                                    Type *
                                </label>
                                <select
                                    id={`type-${index}`}
                                    value={field.type}
                                    onChange={(e) => {
                                        // Re-read the default through the new type.
                                        const nextType = e.target.value as TagType;
                                        handleFieldChange(index, {
                                            type: nextType,
                                            default: retypeDefault(
                                                nextType,
                                                field.default,
                                                field.enumValues
                                            ),
                                        });
                                    }}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                >
                                    {TAG_TYPES.map((type) => (
                                        <option key={type} value={type}>
                                            {`${TAG_TYPE_LABELS[type].label} — ${TAG_TYPE_LABELS[type].hint}`}
                                        </option>
                                    ))}
                                </select>
                            </div>
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label
                                    htmlFor={`label-${index}`}
                                    className="block text-sm font-medium text-text-primary mb-1"
                                >
                                    Label
                                </label>
                                <input
                                    id={`label-${index}`}
                                    type="text"
                                    value={field.label || ""}
                                    onChange={(e) =>
                                        handleFieldChange(index, { label: e.target.value })
                                    }
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                    placeholder="Display label"
                                />
                            </div>
                            <div>
                                <label
                                    htmlFor={`default-${index}`}
                                    className="block text-sm font-medium text-text-primary mb-1"
                                >
                                    Default Value
                                </label>
                                {field.type === "string-list" ? (
                                    <StringListInput
                                        value={
                                            Array.isArray(field.default)
                                                ? (field.default as string[])
                                                : []
                                        }
                                        onChange={(next) =>
                                            handleFieldChange(index, {
                                                default: next.length > 0 ? next : undefined,
                                            })
                                        }
                                        ariaLabel={`Default value entry for tag ${index + 1}`}
                                        placeholder="Add a default entry"
                                    />
                                ) : field.type === "boolean" ? (
                                    <select
                                        id={`default-${index}`}
                                        value={
                                            field.default === undefined
                                                ? ""
                                                : String(
                                                      field.default === true ||
                                                          String(field.default).toLowerCase() ===
                                                              "true"
                                                  )
                                        }
                                        onChange={(e) =>
                                            handleFieldChange(index, {
                                                default: coerceDefault(field.type, e.target.value),
                                            })
                                        }
                                        className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                    >
                                        <option value="">No default</option>
                                        <option value="true">true</option>
                                        <option value="false">false</option>
                                    </select>
                                ) : field.type === "enum" ? (
                                    <select
                                        id={`default-${index}`}
                                        value={
                                            field.default !== undefined ? String(field.default) : ""
                                        }
                                        onChange={(e) =>
                                            handleFieldChange(index, {
                                                default: coerceDefault(field.type, e.target.value),
                                            })
                                        }
                                        className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                    >
                                        <option value="">No default</option>
                                        {(field.enumValues || []).map((option) => (
                                            <option key={String(option)} value={String(option)}>
                                                {String(option)}
                                            </option>
                                        ))}
                                    </select>
                                ) : (
                                    <input
                                        id={`default-${index}`}
                                        type={
                                            field.type === "integer" || field.type === "number"
                                                ? "number"
                                                : "text"
                                        }
                                        step={field.type === "integer" ? 1 : undefined}
                                        value={
                                            field.default !== undefined ? String(field.default) : ""
                                        }
                                        onChange={(e) =>
                                            handleFieldChange(index, {
                                                default: coerceDefault(field.type, e.target.value),
                                            })
                                        }
                                        className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                        placeholder="Default value"
                                    />
                                )}
                            </div>
                        </div>

                        <div>
                            <label
                                htmlFor={`description-${index}`}
                                className="block text-sm font-medium text-text-primary mb-1"
                            >
                                Description
                            </label>
                            <textarea
                                id={`description-${index}`}
                                value={field.description || ""}
                                onChange={(e) =>
                                    handleFieldChange(index, { description: e.target.value })
                                }
                                className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                rows={2}
                                placeholder="Field description"
                            />
                        </div>

                        {field.type === "enum" && (
                            <div>
                                <label
                                    htmlFor={`enumValues-${index}`}
                                    className="block text-sm font-medium text-text-primary mb-1"
                                >
                                    Enum Values (comma-separated)
                                </label>
                                <EnumValuesInput
                                    id={`enumValues-${index}`}
                                    values={field.enumValues || []}
                                    onChange={(values) => {
                                        // A default must be one of the declared values.
                                        const keepDefault =
                                            field.default !== undefined &&
                                            values.includes(String(field.default));
                                        handleFieldChange(index, {
                                            enumValues: values,
                                            default: keepDefault ? field.default : undefined,
                                        });
                                    }}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                />
                                {getErrorForField(index, "enumValues") && (
                                    <p className="mt-1 text-sm text-vams-error">
                                        {getErrorForField(index, "enumValues")}
                                    </p>
                                )}
                            </div>
                        )}

                        <label className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                checked={field.required || false}
                                onChange={(e) =>
                                    handleFieldChange(index, { required: e.target.checked })
                                }
                                className="w-4 h-4"
                            />
                            <span className="text-sm text-text-primary">Required</span>
                        </label>
                    </div>
                );
            })}

            <button
                type="button"
                onClick={handleAddField}
                className="px-4 py-2 text-sm text-blue-600 dark:text-blue-400 border border-blue-600 dark:border-blue-400 rounded hover:bg-blue-50 dark:hover:bg-blue-900/20"
            >
                Add tag
            </button>
        </div>
    );
};

export default TagSchemaBuilder;

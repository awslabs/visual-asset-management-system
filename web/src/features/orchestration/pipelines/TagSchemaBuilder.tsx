/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import type { TagSchemaField, TagType } from "../types";
import { isReservedTagKey } from "../reservedTagKeys";

interface TagSchemaBuilderProps {
    value: TagSchemaField[];
    onChange: (value: TagSchemaField[]) => void;
}

interface ValidationError {
    index: number;
    message: string;
}

const TAG_TYPES: TagType[] = ["string", "integer", "number", "boolean", "string-list", "enum"];

const TagSchemaBuilder: React.FC<TagSchemaBuilderProps> = ({ value, onChange }) => {
    const [fields, setFields] = useState<TagSchemaField[]>(value);
    const [errors, setErrors] = useState<ValidationError[]>([]);

    useEffect(() => {
        setFields(value);
    }, [value]);

    const validateFields = (fieldsList: TagSchemaField[]): ValidationError[] => {
        const newErrors: ValidationError[] = [];
        const tagKeys = new Set<string>();

        fieldsList.forEach((field, index) => {
            // Check for empty tagKey
            if (!field.tagKey || field.tagKey.trim() === "") {
                newErrors.push({ index, message: "Tag key is required" });
                return;
            }

            // Check for reserved keys
            if (isReservedTagKey(field.tagKey)) {
                newErrors.push({
                    index,
                    message: "Tag key is reserved by the system and cannot be used",
                });
                return;
            }

            // Check for duplicates
            if (tagKeys.has(field.tagKey)) {
                newErrors.push({ index, message: "Duplicate tag key" });
            } else {
                tagKeys.add(field.tagKey);
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

        // Only emit valid fields (no errors)
        if (validationErrors.length === 0) {
            onChange(updatedFields);
        }
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

        if (validationErrors.length === 0) {
            onChange(updatedFields);
        }
    };

    const handleRemoveField = (index: number) => {
        const updatedFields = fields.filter((_, i) => i !== index);
        setFields(updatedFields);

        const validationErrors = validateFields(updatedFields);
        setErrors(validationErrors);

        onChange(updatedFields);
    };

    const getErrorForField = (index: number): string | null => {
        const error = errors.find((e) => e.index === index);
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
                                    {field.type}
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
                                    onChange={(e) =>
                                        handleFieldChange(index, {
                                            type: e.target.value as TagType,
                                        })
                                    }
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                >
                                    {TAG_TYPES.map((type) => (
                                        <option key={type} value={type}>
                                            {type}
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
                                <input
                                    id={`default-${index}`}
                                    type="text"
                                    value={field.default !== undefined ? String(field.default) : ""}
                                    onChange={(e) =>
                                        handleFieldChange(index, { default: e.target.value })
                                    }
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                    placeholder="Default value"
                                />
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
                                <input
                                    id={`enumValues-${index}`}
                                    type="text"
                                    value={field.enumValues?.join(", ") || ""}
                                    onChange={(e) => {
                                        const values = e.target.value
                                            .split(",")
                                            .map((v) => v.trim())
                                            .filter((v) => v);
                                        handleFieldChange(index, { enumValues: values });
                                    }}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                                    placeholder="e.g., dev, staging, prod"
                                />
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

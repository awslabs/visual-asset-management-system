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
        <div className="space-y-4">
            {fields.map((field, index) => {
                const error = getErrorForField(index);
                return (
                    <div
                        key={index}
                        className="border border-gray-300 dark:border-gray-700 rounded p-4 space-y-3"
                    >
                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label
                                    htmlFor={`tagKey-${index}`}
                                    className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
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
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                    placeholder="e.g., envName"
                                />
                                {error && (
                                    <p className="mt-1 text-sm text-red-600 dark:text-red-400">
                                        {error}
                                    </p>
                                )}
                            </div>
                            <div>
                                <label
                                    htmlFor={`type-${index}`}
                                    className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
                                >
                                    Type *
                                </label>
                                <select
                                    id={`type-${index}`}
                                    value={field.type}
                                    onChange={(e) =>
                                        handleFieldChange(index, { type: e.target.value as TagType })
                                    }
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
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
                                    className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
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
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                    placeholder="Display label"
                                />
                            </div>
                            <div>
                                <label
                                    htmlFor={`default-${index}`}
                                    className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
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
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                    placeholder="Default value"
                                />
                            </div>
                        </div>

                        <div>
                            <label
                                htmlFor={`description-${index}`}
                                className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
                            >
                                Description
                            </label>
                            <textarea
                                id={`description-${index}`}
                                value={field.description || ""}
                                onChange={(e) =>
                                    handleFieldChange(index, { description: e.target.value })
                                }
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                rows={2}
                                placeholder="Field description"
                            />
                        </div>

                        {field.type === "enum" && (
                            <div>
                                <label
                                    htmlFor={`enumValues-${index}`}
                                    className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
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
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                    placeholder="e.g., dev, staging, prod"
                                />
                            </div>
                        )}

                        <div className="flex items-center justify-between">
                            <label className="flex items-center space-x-2">
                                <input
                                    type="checkbox"
                                    checked={field.required || false}
                                    onChange={(e) =>
                                        handleFieldChange(index, { required: e.target.checked })
                                    }
                                    className="w-4 h-4"
                                />
                                <span className="text-sm text-gray-700 dark:text-gray-300">
                                    Required
                                </span>
                            </label>
                            <button
                                type="button"
                                onClick={() => handleRemoveField(index)}
                                className="px-3 py-1 text-sm bg-red-600 text-white rounded hover:bg-red-700"
                            >
                                Remove
                            </button>
                        </div>
                    </div>
                );
            })}

            <button
                type="button"
                onClick={handleAddField}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
            >
                Add Field
            </button>
        </div>
    );
};

export default TagSchemaBuilder;

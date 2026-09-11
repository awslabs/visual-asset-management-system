/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Pipeline, Template, PipelineExecutionParameters, TagSchemaField } from "../types";
import { RESERVED_TAG_KEYS, METADATA_DYNAMIC_TAG_PREFIX } from "../reservedTagKeys";

const SYSTEM_KEYS = RESERVED_TAG_KEYS;

export interface ResolvePipelineParamsInput {
    pipeline: Pipeline;
    template?: Template;
    templateId?: string;
    tags: { key: string; value: any }[];
    customTemplateOverride?: string;
    customEditedBody?: string;
}

export interface ResolvePipelineParamsResult {
    errors: string[];
    params: PipelineExecutionParameters;
    mode: 1 | 2 | 3 | 4 | 5;
}

/**
 * A provided tag value counts as absent when it is undefined, null, an empty string, or an empty
 * list — matching the backend's `_is_absent`, which treats an empty collection like a blank string.
 */
function isAbsentTagValue(value: any): boolean {
    if (value === undefined || value === null || value === "") return true;
    return Array.isArray(value) && value.length === 0;
}

/**
 * Whether a schema field declares a usable default. The backend's test is `is not None`, and pydantic
 * serializes an absent default as `null`, so a schema saved through the tagSchema sub-resource comes
 * back carrying nulls that must read as "no default" rather than as a declared one.
 */
export function hasDeclaredDefault(field: TagSchemaField): boolean {
    return field.default !== undefined && field.default !== null;
}

/**
 * Return tagKeys of schema fields with required===true that have no provided value
 * (missing or empty/undefined/null) and no default.
 */
export function missingRequiredTags(
    schema: TagSchemaField[],
    tags: { key: string; value: any }[]
): string[] {
    const tagMap = new Map<string, any>();
    for (const tag of tags) {
        tagMap.set(tag.key, tag.value);
    }

    const missing: string[] = [];
    for (const field of schema) {
        if (field.required !== true) continue;
        if (hasDeclaredDefault(field)) continue;

        if (isAbsentTagValue(tagMap.get(field.tagKey))) {
            missing.push(field.tagKey);
        }
    }

    return missing;
}

/**
 * Resolve pipeline execution parameters according to the 5-case template-resolution contract.
 */
export function resolvePipelineParams(
    input: ResolvePipelineParamsInput
): ResolvePipelineParamsResult {
    const { pipeline, template, templateId, tags, customTemplateOverride, customEditedBody } =
        input;
    const errors: string[] = [];

    const requireTemplate = !!pipeline.systemConfig?.requireTemplate;
    const allowOverride = !!pipeline.systemConfig?.allowCustomTemplateOverride;

    // Check for reserved key collisions
    for (const tag of tags) {
        if (SYSTEM_KEYS.has(tag.key) || tag.key.startsWith(METADATA_DYNAMIC_TAG_PREFIX)) {
            errors.push(`Tag key "${tag.key}" is reserved and cannot be user-provided`);
        }
    }

    // Case 5 check: customEditedBody requires allowCustomEdit
    if (customEditedBody) {
        if (!template?.allowCustomEdit) {
            errors.push("This template does not allow custom editing of the final config");
        }
    }

    // A template-backed override is allowed when EITHER the pipeline allows a custom override OR the
    // chosen template allows custom edit (the unified "Customize configuration" toggle). A
    // template-LESS override still requires the pipeline-level grant.
    const allowTemplateEdit = !!template?.allowCustomEdit;
    if (customTemplateOverride && !allowOverride) {
        if (!(templateId && allowTemplateEdit)) {
            errors.push("This pipeline does not allow a custom template override");
        }
    }

    // Determine case
    if (templateId) {
        if (!template) {
            errors.push("Template must be provided when templateId is specified");
            return { errors, params: {}, mode: 1 };
        }

        if (customTemplateOverride) {
            // Case 2: templateId + override
            // Validate tags against template schema
            const missing = missingRequiredTags(template.tagSchema || [], tags);
            if (missing.length > 0) {
                errors.push(`Required tags missing: ${missing.join(", ")}`);
            }
            return {
                errors,
                params: { templateId, templateTags: tags, customTemplateOverride },
                mode: 2,
            };
        } else {
            // Case 1 or Case 5
            // Validate tags against template schema
            const missing = missingRequiredTags(template.tagSchema || [], tags);
            if (missing.length > 0) {
                errors.push(`Required tags missing: ${missing.join(", ")}`);
            }

            if (customEditedBody) {
                // Case 5
                return {
                    errors,
                    params: {
                        templateId,
                        templateTags: tags,
                        customTemplateOverride: customEditedBody,
                    },
                    mode: 5,
                };
            } else {
                // Case 1
                return {
                    errors,
                    params: { templateId, templateTags: tags },
                    mode: 1,
                };
            }
        }
    } else {
        // No templateId
        if (customTemplateOverride) {
            // Case 3: override without template
            if (requireTemplate) {
                errors.push(
                    "This pipeline requires a template; a template-less override is not allowed"
                );
            }

            return {
                errors,
                params: { templateTags: tags, customTemplateOverride },
                mode: 3,
            };
        } else {
            // Case 4: no template, no override
            if (requireTemplate) {
                errors.push("This pipeline requires a template (templateId) for execution");
            }

            return {
                errors,
                params: { templateTags: tags },
                mode: 4,
            };
        }
    }
}

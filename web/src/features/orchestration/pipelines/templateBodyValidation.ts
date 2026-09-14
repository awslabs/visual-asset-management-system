/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ConfigFormat, TagSchemaField } from "../types";

/**
 * The template config body's `{{tagKey}}` contract, mirrored from the backend so the form can report
 * the save-time verdict while the body is being typed:
 *   - which keys a placeholder can name (common/workflows/templateTagSchema.py _TAG_KEY_PATTERN);
 *   - how a placeholder is spelled (common/workflows/templateRender.py _TAG_PATTERN);
 *   - the two-pass parse check a `json` body is held to (templateRender.json_body_placeholder_text,
 *     applied by models/pipelines.py _validate_json_config_body).
 * The backend remains the authority at save; this module exists so the author sees the same verdict
 * first.
 */

// Only these characters are captured by a {{tag}} placeholder, so a key outside the set can be
// declared but never rendered.
export const TAG_KEY_PATTERN = /^[A-Za-z0-9_]+$/;

// A placeholder as the renderer matches it, whitespace inside the braces tolerated.
const TAG_PLACEHOLDER_SOURCE = "\\{\\{\\s*([A-Za-z0-9_]+)\\s*\\}\\}";

/** The declared tag types whose value renders as a JSON literal rather than as text. */
const USER_TAG_TYPE_SHAPES: Record<string, string> = {
    integer: "0",
    number: "0.0",
    boolean: "true",
    "string-list": "[]",
};

/**
 * System tags substituted as JSON literals (kind "json" in templateRender.build_template_context and
 * _metadata_context), each with the literal it stands in as. Every other system tag renders text.
 */
const SYSTEM_JSON_TAG_SHAPES: Record<string, string> = {
    assetFileKeyArray: "[]",
    assetFileRelativePathArray: "[]",
    assetFileS3UriArray: "[]",
    assetFileVersionIdArray: "[]",
    assetFileObjectArray: "[]",
    assetFileAssetIdArray: "[]",
    assetFileUniqueAssetIdArray: "[]",
    assetFileDatabaseIdArray: "[]",
    assetFileUniqueDatabaseIdArray: "[]",
    assetFileCount: "0",
    inputMetadataObject: "{}",
    assetMetadataObject: "{}",
    fileMetadataObject: "{}",
    fileAttributesObject: "{}",
    assetDataObject: "{}",
    databaseMetadataObject: "{}",
};

// The stand-in for a tag that renders text: valid inside the template's own quotes, invalid as a
// bare value.
const SCALAR_STAND_IN = "vams-tag";

const normalizeType = (type?: string) =>
    String(type ?? "string")
        .trim()
        .toLowerCase();

/** Whether a tag of this declared type renders a JSON value, so its placeholder takes no quotes. */
export const rendersJsonValue = (type?: string): boolean =>
    USER_TAG_TYPE_SHAPES[normalizeType(type)] !== undefined;

/**
 * The placeholder text a tag chip inserts into the body. In a json body a string or enum tag renders
 * text and belongs inside the quotes of the string it fills, so it is inserted quoted; an integer,
 * number, boolean or string-list tag renders a JSON value of that type and is the whole value, so it is
 * inserted bare. Every other format substitutes text verbatim and always gets the bare placeholder.
 */
export function placeholderFor(field: TagSchemaField, format: ConfigFormat): string {
    const bare = `{{${field.tagKey}}}`;
    return format === "json" && !rendersJsonValue(field.type) ? `"${bare}"` : bare;
}

/**
 * The declared tags no `{{tagKey}}` in the body references. The renderer only substitutes tags the
 * body names, so such a tag is collected on the execute form and then dropped. Keys outside the
 * substitutable charset are skipped: they can never be referenced, and the tag builder already
 * reports them.
 */
export const unreferencedTagKeys = (schema: TagSchemaField[], body: string): string[] =>
    schema
        .map((field) => field.tagKey)
        .filter(
            (key) =>
                TAG_KEY_PATTERN.test(key || "") &&
                !new RegExp(`\\{\\{\\s*${key}\\s*\\}\\}`).test(body)
        );

const hasPlaceholders = (body: string) => new RegExp(TAG_PLACEHOLDER_SOURCE).test(body);

/** {tagKey: JSON literal} for the declared tags that render a non-text value. */
function userTagShapes(tagSchema: TagSchemaField[]): Record<string, string> {
    const shapes: Record<string, string> = {};
    for (const field of tagSchema) {
        const shape = USER_TAG_TYPE_SHAPES[normalizeType(field?.type)];
        if (field?.tagKey && shape) shapes[field.tagKey] = shape;
    }
    return shapes;
}

/**
 * The body with every placeholder replaced by a JSON-valid stand-in for what it renders. A system tag
 * name wins over a same-named user tag, as it does in the renderer. With `structuredAsString` the
 * object/array stand-ins (and every typed user tag) become a quoted string instead, which parses in a
 * value position and breaks a string it sits inside — that is what tells a placeholder standing alone
 * apart from one written inside quotes.
 */
function standInText(
    body: string,
    tagSchema: TagSchemaField[],
    structuredAsString: boolean
): string {
    const declared = userTagShapes(tagSchema);
    return body.replace(new RegExp(TAG_PLACEHOLDER_SOURCE, "g"), (_match, name: string) => {
        const systemShape = SYSTEM_JSON_TAG_SHAPES[name];
        if (systemShape !== undefined) {
            return structuredAsString && systemShape !== "0"
                ? JSON.stringify(SCALAR_STAND_IN)
                : systemShape;
        }
        const userShape = declared[name];
        if (userShape !== undefined) {
            return structuredAsString ? JSON.stringify(SCALAR_STAND_IN) : userShape;
        }
        return SCALAR_STAND_IN;
    });
}

const parses = (text: string): boolean => {
    try {
        JSON.parse(text);
        return true;
    } catch {
        return false;
    }
};

const NOT_JSON = "The config body is not valid JSON.";
const TEXT_TAG_ADVICE =
    'A {{tagName}} placeholder for a text value belongs inside the JSON string it fills ("key": "{{tagName}}").';
const TYPED_TAG_ADVICE =
    "A {{tagName}} placeholder for a string or enum tag belongs inside the JSON string it fills " +
    '("key": "{{tagName}}"); a tag declared integer, number, boolean or string-list renders a JSON ' +
    'value of that type, so its placeholder is the whole value and takes no quotes ("key": {{tagName}}).';
const QUOTED_TYPED_TAG =
    "The config body quotes a {{tagName}} placeholder for a tag declared integer, number, boolean or " +
    "string-list. Such a tag renders a JSON value of that type, so its placeholder is the whole value " +
    'and takes no quotes ("key": {{tagName}}). Quoted, a string-list renders a structure inside the ' +
    "string's own quotes and the pipeline receives malformed JSON, while a number or boolean is " +
    "delivered as text.";
const QUOTED_STRUCTURED_TAG =
    "The config body quotes a {{tagName}} placeholder that renders a JSON object or array. Such a " +
    'placeholder is the whole value and takes no quotes ("key": {{tagName}}); quoted, it renders an ' +
    "object inside the string's own quotes and the pipeline receives malformed JSON.";

/**
 * The save-time verdict on a config body, or null when the backend would accept it. Only a `json`
 * body is checked — the other formats are stored verbatim. A body with no placeholders must simply
 * parse; one with placeholders is parsed twice, once with each tag replaced by the literal shape it
 * renders and once with the structured shapes quoted, so a text tag outside its quotes and a typed tag
 * inside quotes are both reported. The advice names the rule, never the offending tag.
 */
export function validateJsonConfigBody(
    body: string,
    format: ConfigFormat,
    tagSchema: TagSchemaField[]
): string | null {
    if (format !== "json" || !body) return null;
    if (!hasPlaceholders(body)) return parses(body) ? null : NOT_JSON;
    const typed = Object.keys(userTagShapes(tagSchema)).length > 0;
    if (!parses(standInText(body, tagSchema, false))) {
        return `${NOT_JSON} ${typed ? TYPED_TAG_ADVICE : TEXT_TAG_ADVICE}`;
    }
    if (!parses(standInText(body, tagSchema, true))) {
        return typed ? QUOTED_TYPED_TAG : QUOTED_STRUCTURED_TAG;
    }
    return null;
}

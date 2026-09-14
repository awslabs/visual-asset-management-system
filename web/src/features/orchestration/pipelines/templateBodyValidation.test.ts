/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    placeholderFor,
    rendersJsonValue,
    unreferencedTagKeys,
    validateJsonConfigBody,
} from "./templateBodyValidation";
import type { TagSchemaField } from "../types";

const tag = (tagKey: string, type: TagSchemaField["type"]): TagSchemaField => ({
    tagKey,
    type,
    required: false,
});

describe("placeholderFor — what a tag chip inserts", () => {
    it("quotes a string or enum tag in a json body, because it renders text", () => {
        expect(placeholderFor(tag("PROMPT", "string"), "json")).toBe('"{{PROMPT}}"');
        expect(placeholderFor(tag("MODE", "enum"), "json")).toBe('"{{MODE}}"');
    });

    it("inserts a typed tag bare in a json body, because it renders a JSON value", () => {
        expect(placeholderFor(tag("STEPS", "integer"), "json")).toBe("{{STEPS}}");
        expect(placeholderFor(tag("RATIO", "number"), "json")).toBe("{{RATIO}}");
        expect(placeholderFor(tag("FLAG", "boolean"), "json")).toBe("{{FLAG}}");
        expect(placeholderFor(tag("NAMES", "string-list"), "json")).toBe("{{NAMES}}");
    });

    it("always inserts the bare placeholder outside json", () => {
        for (const format of ["yaml", "openjd", "xml", "raw"] as const) {
            expect(placeholderFor(tag("PROMPT", "string"), format)).toBe("{{PROMPT}}");
            expect(placeholderFor(tag("STEPS", "integer"), format)).toBe("{{STEPS}}");
        }
    });

    it("classifies the declared type case-insensitively, as the backend normalizes it", () => {
        expect(rendersJsonValue("INTEGER")).toBe(true);
        expect(rendersJsonValue("String")).toBe(false);
        expect(rendersJsonValue(undefined)).toBe(false);
    });
});

describe("validateJsonConfigBody — the backend's two-pass rule", () => {
    it("checks only json bodies, and skips an empty one", () => {
        expect(validateJsonConfigBody("not: json: at: all", "yaml", [])).toBeNull();
        expect(validateJsonConfigBody("<a>{{PROMPT}}</a>", "xml", [])).toBeNull();
        expect(validateJsonConfigBody("", "json", [])).toBeNull();
    });

    it("requires a body with no placeholders to parse", () => {
        expect(validateJsonConfigBody('{"a": 1}', "json", [])).toBeNull();
        expect(validateJsonConfigBody('{"a": ', "json", [])).toBe(
            "The config body is not valid JSON."
        );
    });

    it("accepts a text tag inside its quotes and a typed tag bare", () => {
        const schema = [
            tag("PROMPT", "string"),
            tag("STEPS", "integer"),
            tag("NAMES", "string-list"),
        ];
        expect(
            validateJsonConfigBody(
                '{"prompt": "{{PROMPT}}", "steps": {{STEPS}}, "names": {{ NAMES }}}',
                "json",
                schema
            )
        ).toBeNull();
    });

    it("rejects a text tag standing bare, with the typed-schema advice when the schema declares typed tags", () => {
        const withTyped = validateJsonConfigBody(
            '{"prompt": {{PROMPT}}, "steps": {{STEPS}}}',
            "json",
            [tag("PROMPT", "string"), tag("STEPS", "integer")]
        );
        expect(withTyped).toMatch(/^The config body is not valid JSON\./);
        expect(withTyped).toMatch(/string or enum tag belongs inside the JSON string it fills/);
        expect(withTyped).toMatch(/integer, number, boolean or string-list renders a JSON value/);

        const textOnly = validateJsonConfigBody('{"prompt": {{PROMPT}}}', "json", [
            tag("PROMPT", "string"),
        ]);
        expect(textOnly).toMatch(/^The config body is not valid JSON\./);
        expect(textOnly).toMatch(/placeholder for a text value belongs inside the JSON string/);
        expect(textOnly).not.toMatch(/string-list/);
    });

    it("rejects a quoted typed tag — it parses, but delivers the wrong type", () => {
        const message = validateJsonConfigBody('{"steps": "{{STEPS}}"}', "json", [
            tag("STEPS", "integer"),
        ]);
        expect(message).toMatch(
            /quotes a \{\{tagName\}\} placeholder for a tag declared integer, number, boolean or string-list/
        );
        expect(
            validateJsonConfigBody('{"flag": "{{FLAG}}"}', "json", [tag("FLAG", "boolean")])
        ).toMatch(/takes no quotes/);
        expect(
            validateJsonConfigBody('{"names": "{{NAMES}}"}', "json", [tag("NAMES", "string-list")])
        ).toMatch(/malformed JSON/);
    });

    it("treats an undeclared typed-looking key as text: without its schema entry it must be quoted", () => {
        // Mirrors the backend fallback: with no declaration the stand-in is text.
        expect(validateJsonConfigBody('{"steps": {{STEPS}}}', "json", [])).toMatch(
            /not valid JSON/
        );
        expect(validateJsonConfigBody('{"steps": "{{STEPS}}"}', "json", [])).toBeNull();
    });

    it("knows the system tags that render JSON values", () => {
        expect(
            validateJsonConfigBody(
                '{"files": {{assetFileKeyArray}}, "n": {{assetFileCount}}}',
                "json",
                []
            )
        ).toBeNull();
        expect(validateJsonConfigBody('{"meta": {{assetMetadataObject}}}', "json", [])).toBeNull();
        expect(validateJsonConfigBody('{"files": "{{assetFileKeyArray}}"}', "json", [])).toMatch(
            /quotes a \{\{tagName\}\} placeholder that renders a JSON object or array/
        );
        // A scalar system tag is text and needs its quotes.
        expect(validateJsonConfigBody('{"id": {{executionId}}}', "json", [])).toMatch(
            /not valid JSON/
        );
        expect(validateJsonConfigBody('{"id": "{{executionId}}"}', "json", [])).toBeNull();
    });

    it("lets a system tag name win over a same-named user tag, as the renderer does", () => {
        // Declared string, but the renderer substitutes the system count: the bare form is right.
        expect(
            validateJsonConfigBody('{"n": {{assetFileCount}}}', "json", [
                tag("assetFileCount", "string"),
            ])
        ).toBeNull();
    });
});

describe("unreferencedTagKeys", () => {
    it("lists the declared tags the body never names, whitespace tolerated", () => {
        const schema = [tag("PROMPT", "string"), tag("STEPS", "integer"), tag("MODE", "enum")];
        expect(unreferencedTagKeys(schema, '{"p": "{{ PROMPT }}", "s": {{STEPS}}}')).toEqual([
            "MODE",
        ]);
    });

    it("skips a key the renderer could never substitute", () => {
        expect(unreferencedTagKeys([tag("bad-key", "string")], "{}")).toEqual([]);
    });
});

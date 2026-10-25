/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The pages that describe temporary session keys quote what the API key service does with them, and
 * nothing else ties them to it: the reserved name prefix, the `403` a caller holding such a key gets
 * on an API-key create or update, and the `400` a create gets for a name under the prefix. The API
 * reference quotes both messages, and the security page and the OpenAPI create and update operations
 * name the prefix and the refusals.
 *
 * Durable (root CLAUDE.md Rule 13): the prefix and either message can change again, and a page can
 * describe the refusals wrong again.
 */

import { OPENAPI, capture, page, pythonCode, read, yamlBlock, yamlNode } from "./support/docsText";

const CONSTANTS = "backend/backend/common/constants.py";
const SERVICE = "backend/backend/handlers/auth/apiKeyService.py";

/** The reserved prefix of a temporary session key's name. */
const prefix = (): string =>
    capture(pythonCode(read(CONSTANTS)), /^TEMPORARY_SESSION_KEY_NAME_PREFIX\s*=\s*"([^"]+)"$/m);

/** The message of the 403 a caller holding a temporary session key gets on create or update. */
const sessionKeyRefusal = (): string =>
    capture(
        read(SERVICE),
        /if [^\n]*_caller_is_temporary_session_key\(\):[\s\S]*?body=\{'message': '([^']+)'\}/
    );

/** The message of the 400 a create gets for a name under the prefix, its string pieces joined. */
const reservedNameRefusal = (): string => {
    const fn = capture(read(SERVICE), /(def _reserved_key_name_refusal\([\s\S]*?)\ndef /);
    const literal = capture(fn, /body=\{'message':\s*((?:"[^"]*"\s*)+)\}/);
    return Array.from(literal.matchAll(/"([^"]*)"/g), (m) => m[1]).join("");
};

/** The `description` of an OpenAPI operation. */
const operationDescription = (route: string, method: string): string => {
    const lines = read(OPENAPI).split("\n");
    const at = yamlNode(lines, ["paths", route, method]);
    if (at === -1) return "";
    const block = yamlBlock(lines, at).filter((l) => l.trim() !== "");
    const childIndent = block.length > 0 ? block[0].search(/\S/) : -1;
    const line = block.find((l) => l.search(/\S/) === childIndent && /^\s*description:/.test(l));
    return line ? capture(line, /description:\s*"(.*)"\s*$/) : "";
};

const CREATE_ROUTES = ["/auth/api-keys", "/auth/user/api-keys"];
const UPDATE_ROUTES = ["/auth/api-keys/{apiKeyId}", "/auth/user/api-keys/{apiKeyId}"];

describe("temporary session key documentation matches the API key service", () => {
    test("the service declares the prefix and both refusals (control)", () => {
        expect(prefix()).toMatch(/^[a-z-]+-$/);
        expect(sessionKeyRefusal()).toMatch(/temporary session key$/);
        expect(reservedNameRefusal()).toMatch(/^API key names starting with .+\.$/);
        expect(read(SERVICE)).toMatch(/startswith\(TEMPORARY_SESSION_KEY_NAME_PREFIX\)/);
    });

    test("the OpenAPI reader finds the create and update operations (control)", () => {
        for (const route of CREATE_ROUTES) {
            expect(operationDescription(route, "post")).not.toBe("");
        }
        for (const route of UPDATE_ROUTES) {
            expect(operationDescription(route, "put")).not.toBe("");
        }
    });

    test("api/auth.md names the prefix and quotes both refusals with their status codes", () => {
        const text = page("api/auth.md");
        const [paragraph] = text.split(/\n\s*\n/).filter((p) => p.includes(sessionKeyRefusal()));
        expect(paragraph).toBeDefined();
        expect(paragraph).toContain(`\`${prefix()}\``);
        expect(paragraph).toContain(`\`403\` \`${sessionKeyRefusal()}\``);
        expect(paragraph).toContain(`\`400\` \`${reservedNameRefusal()}\``);
    });

    test("architecture/security.md names the prefix and both refusals", () => {
        const [paragraph] = page("architecture/security.md")
            .split(/\n\s*\n/)
            .filter((p) => p.includes(`\`${prefix()}\``));
        expect(paragraph).toBeDefined();
        expect(paragraph).toContain("`403`");
        expect(paragraph).toContain("`400`");
    });

    test.each(CREATE_ROUTES)(
        "VAMS_API.yaml POST %s names both refusals and the prefix",
        (route) => {
            const description = operationDescription(route, "post");
            expect(description).toContain("temporary session key (403)");
            expect(description).toContain(`${prefix()} (400)`);
        }
    );

    test.each(UPDATE_ROUTES)("VAMS_API.yaml PUT %s names the session-key refusal", (route) => {
        expect(operationDescription(route, "put")).toContain("temporary session key (403)");
    });
});

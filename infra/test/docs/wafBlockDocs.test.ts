/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The pages that explain an AWS WAF block quote names and texts the code produces, and nothing else
 * ties them to it:
 *   * the WAF_FILTERED gateway response: the 403 body API Gateway returns for a block, in the API
 *     reference, the security page and the OpenAPI document, and the response header carrying the
 *     request ID the web ACL log records;
 *   * the CLI's reading of a 403: the global error headings as printed, the `--json-output` keys of a
 *     block, the web ACL log group the administrator searches, and the JSON error documents the
 *     automation page shows.
 * Each assertion derives the expected value from the source that produces it and reads it back out of
 * the page, so a page that drifts fails here instead of shipping. An add-on's WAF exemption is held by
 * the guard tests of its pages under infra/test/addon/<addon>/docs/.
 *
 * Durable (root CLAUDE.md Rule 13): every name and text below can change again with the next
 * response, heading, field or message, and every forbidden wording can be written again.
 */

import { WAF_FILTERED_RESPONSE_TEMPLATE } from "../../lib/nestedStacks/apiLambda/constructs/rest-api-gateway-construct";
import {
    OPENAPI,
    capture,
    docsPages,
    jsonBlocks,
    page,
    paragraphsWith,
    read,
    sectionOf,
    yamlBlock,
    yamlKeys,
    yamlNode,
    yamlText,
} from "./support/docsText";

const CLI_UTILS = "tools/VamsCLI/vamscli/utils";

/** The `_handle_global_error` heading the global handler prints for an exception class. */
function globalHeading(exceptionClass: string): string {
    return capture(
        read(`${CLI_UTILS}/global_exceptions.py`),
        new RegExp(
            `isinstance\\(e, ${exceptionClass}\\):\\s*(?:\\S.*\\n\\s*)?_handle_global_error\\("([^"]+)"`
        )
    );
}

describe("AWS WAF block documentation matches the code", () => {
    describe("WAF_FILTERED gateway response", () => {
        const body = (): Record<string, string> => JSON.parse(WAF_FILTERED_RESPONSE_TEMPLATE);
        // The regional web ACL log records a request under API Gateway's extended request ID, which the
        // x-amz-apigw-id response header carries; the pages that send an administrator to that log name
        // the header beside the log field.
        const LOG_FIELD = "httpRequest.requestId";
        const HEADER = "x-amz-apigw-id";

        test("the response template is the documented JSON body (control)", () => {
            expect(Object.keys(body()).sort()).toEqual(["blockedBy", "message", "requestId"]);
        });

        test("the response template carries API Gateway's extended request ID", () => {
            expect(body().requestId).toBe("$context.extendedRequestId");
        });

        test("the paragraph check flags a log paragraph that does not name the header (control)", () => {
            const text = `Look it up as \`${LOG_FIELD}\`.\n\nThe \`${HEADER}\` header carries it.`;
            const paragraphs = paragraphsWith(text, LOG_FIELD);
            expect(paragraphs).toHaveLength(1);
            expect(paragraphs.filter((p) => !p.includes(`\`${HEADER}\``))).toHaveLength(1);
        });

        test.each([
            "api/overview.md",
            "architecture/security.md",
            "cli/troubleshooting/general.md",
        ])(`%s names ${HEADER} as the header carrying the web ACL log's request ID`, (rel) => {
            const paragraphs = paragraphsWith(page(rel), LOG_FIELD);
            expect(paragraphs.length).toBeGreaterThan(0);
            expect(paragraphs.filter((p) => !p.includes(`\`${HEADER}\``))).toEqual([]);
        });

        test(`VAMS_API.yaml names ${HEADER} as the header carrying the web ACL log's request ID`, () => {
            const requestId = yamlText(read(OPENAPI), [
                "components",
                "schemas",
                "requestBlockedError",
                "properties",
                "requestId",
            ]);
            expect(requestId).toContain(LOG_FIELD);
            expect(requestId).toContain(HEADER);
        });

        test.each(["api/overview.md", "architecture/security.md"])(
            "%s quotes the block body",
            (rel) => {
                const text = page(rel);
                expect(text).toContain(body().message);
                expect(text).toContain(`"blockedBy": "${body().blockedBy}"`);
                expect(text).toContain('"requestId"');
            }
        );

        test("VAMS_API.yaml declares the block body as a schema and a reusable 403 response", () => {
            const yaml = read(OPENAPI);
            const schema = ["components", "schemas", "requestBlockedError"];
            expect(yamlKeys(yaml, [...schema, "properties"]).sort()).toEqual(
                Object.keys(body()).sort()
            );
            expect(yamlText(yaml, [...schema, "properties", "blockedBy", "enum"]).trim()).toBe(
                `- ${body().blockedBy}`
            );
            expect(yamlText(yaml, [...schema, "properties", "message"])).toContain(
                `example: "${body().message}"`
            );
            expect(
                yamlText(yaml, [
                    "components",
                    "responses",
                    "RequestBlocked",
                    "content",
                    "application/json",
                    "schema",
                ]).trim()
            ).toBe('$ref: "#/components/schemas/requestBlockedError"');
        });

        test("the YAML reader finds an existing schema's fields (control)", () => {
            expect(
                yamlKeys(read(OPENAPI), ["components", "schemas", "error", "properties"])
            ).toEqual(["message"]);
            expect(yamlKeys(read(OPENAPI), ["components", "schemas", "noSuchSchema"])).toEqual([]);
        });

        test("the YAML reader steps over a quoted scalar continued in column zero (control)", () => {
            const lines = [
                "paths:",
                "    /a:",
                "        put:",
                '            description: "One.',
                "",
                'Two, in column zero."',
                "    /b:",
                "        get:",
                "            summary: x",
            ];
            expect(yamlNode(lines, ["paths", "/b", "get"])).toBe(7);
            expect(yamlBlock(lines, 1)).toHaveLength(4);
        });
    });

    describe("CLI reading of a 403", () => {
        const GENERAL = "cli/troubleshooting/general.md";
        const PERMISSIONS = "cli/troubleshooting/permissions.md";
        const AUTOMATION = "cli/automation.md";

        /** The `--json-output` keys a core CLI exception class adds beside `error` and `error_type`. */
        const jsonFieldKeys = (): string[] =>
            Array.from(
                read(`${CLI_UTILS}/exceptions.py`).matchAll(
                    /json_fields\s*(?:=\s*\{\s*|\[)"(\w+)"/g
                ),
                (m) => m[1]
            );
        /** The keys of a global error's and of a command error's `--json-output` document. */
        const errorDocumentKeys = (): string[] => [
            ...Array.from(
                read(`${CLI_UTILS}/global_exceptions.py`).matchAll(
                    /error_data(?:\s*=\s*\{|\[)"(\w+)"/g
                ),
                (m) => m[1]
            ),
            ...Array.from(
                capture(read(`${CLI_UTILS}/json_output.py`), /error_data = \{([^}]*)\}/).matchAll(
                    /"(\w+)":/g
                ),
                (m) => m[1]
            ),
        ];

        test("the CLI sources declare the headings, fields and keys read below (control)", () => {
            expect(globalHeading("RequestBlockedError")).toBe("Request Blocked");
            expect(globalHeading("PermissionDeniedError")).not.toBe("");
            expect(globalHeading("TokenExpiredError")).not.toBe("");
            expect(jsonFieldKeys()).toEqual(expect.arrayContaining(["blockedBy", "requestId"]));
            expect(errorDocumentKeys()).toEqual(
                expect.arrayContaining(["error", "error_type", "additional_info"])
            );
        });

        test("the general troubleshooting page has a section for a block, as the CLI prints it", () => {
            const heading = globalHeading("RequestBlockedError");
            const text = page(GENERAL);
            const blocked = text.split("\n").find((l) => l.startsWith(`## ${heading}`));
            expect(blocked).toBeDefined();
            const body = sectionOf(text, blocked as string);
            expect(body).toContain(`✗ ${heading}:`);
            expect(body).toContain("`RequestBlockedError`");
            const exceptions = read(`${CLI_UTILS}/exceptions.py`);
            const blockedClass = capture(exceptions, /(class RequestBlockedError[\s\S]*?)\nclass /);
            const values = Array.from(blockedClass.matchAll(/"(waf[\w-]*)"/g), (m) => m[1]);
            expect(values.length).toBeGreaterThanOrEqual(2);
            const keys = Array.from(
                blockedClass.matchAll(/json_fields\s*(?:=\s*\{\s*|\[)"(\w+)"/g),
                (m) => m[1]
            );
            expect(keys.length).toBeGreaterThanOrEqual(2);
            expect([...keys, ...values].filter((k) => !body.includes(`\`${k}\``))).toEqual([]);
        });

        // The administrator finds the matching rule in the web ACL's log group, whose prefix the WAF
        // construct sets; the CLI's hint and the troubleshooting page name the same one.
        test("the CLI hint and the general troubleshooting page name the web ACL log group the construct creates", () => {
            const prefix = capture(
                read("infra/lib/constructs/wafv2-basic-construct.ts"),
                /"(aws-waf-logs-[a-z-]+-)"/
            );
            expect(prefix).toMatch(/^aws-waf-logs-/);
            expect(read("tools/VamsCLI/vamscli/constants.py")).toContain(`${prefix}*`);
            expect(page(GENERAL)).toContain(`\`${prefix}*\``);
        });

        test("the permissions page shows the 403 headings as the CLI prints them", () => {
            const tokenExpired = capture(
                read(`${CLI_UTILS}/api_client.py`),
                /TokenExpiredError\(\s*"(Authentication token has expired\.)/
            );
            expect(tokenExpired).not.toBe("");
            const text = page(PERMISSIONS);
            expect(text).toContain(`${globalHeading("PermissionDeniedError")}: Access forbidden.`);
            expect(text).toContain(`${globalHeading("TokenExpiredError")}: ${tokenExpired}`);
            expect(text).toContain("general.md#request-blocked-aws-waf");
        });

        /**
         * Lines that present a 403 as an authentication failure: the `Authentication Error` heading
         * over a text the CLI raises under another heading, or `Authentication Error` /
         * `Authentication failed` on a line that names HTTP 403.
         */
        const authenticationWording = (text: string): string[] =>
            text
                .split("\n")
                .filter(
                    (l) =>
                        /Authentication Error: (Access forbidden|Authentication token has expired|Override token has expired|The deployment's AWS WAF|Amazon API Gateway refused)/.test(
                            l
                        ) ||
                        (/Authentication (Error|failed)/.test(l) && /\b403\b/.test(l))
                );

        test("the wording scanner flags a 403 shown as an authentication failure (control)", () => {
            expect(
                authenticationWording(
                    [
                        "-   `Authentication Error: Access forbidden. You do not have permission.`",
                        "-   The command fails with `Authentication failed` (HTTP 403)",
                        "-   `Authentication Error: Authentication failed.` after an HTTP 401",
                        "-   `Permission Denied: Access forbidden. Not Authorized` (HTTP 403)",
                    ].join("\n")
                )
            ).toHaveLength(2);
        });

        test("no CLI page presents a 403 as an authentication failure", () => {
            const hits = docsPages("cli").flatMap((rel) =>
                authenticationWording(page(rel)).map((l) => `${rel}: ${l.trim()}`)
            );
            expect(hits).toEqual([]);
        });

        /** Sentences that place a JSON error document on stderr, which the CLI writes to stdout. */
        const jsonOnStderr = (text: string): string[] =>
            text
                .split(/\n|(?<=\.)\s+/)
                .filter((s) => /JSON/.test(s) && /stderr/.test(s) && !/stdout/.test(s));

        test("the stderr scanner flags a JSON error placed on stderr (control)", () => {
            expect(
                jsonOnStderr(
                    "Errors are returned as JSON objects to stderr. In text mode the heading goes to stderr. With --json-output the JSON error goes to stdout, the heading to stderr in text mode."
                )
            ).toHaveLength(1);
        });

        test.each([AUTOMATION, GENERAL])("%s places the JSON error document on stdout", (rel) => {
            expect(jsonOnStderr(page(rel))).toEqual([]);
        });

        test("the automation page's JSON error examples carry only keys the core CLI emits", () => {
            const allowed = new Set([...errorDocumentKeys(), ...jsonFieldKeys()]);
            const examples = jsonBlocks(page(AUTOMATION))
                .map((block) => JSON.parse(block) as Record<string, unknown>)
                .filter((doc) => "error_type" in doc);
            expect(examples.length).toBeGreaterThanOrEqual(2);
            expect(
                examples.flatMap((doc) => Object.keys(doc)).filter((k) => !allowed.has(k))
            ).toEqual([]);
            expect(examples.some((doc) => "additional_info" in doc && "blockedBy" in doc)).toBe(
                true
            );
        });
    });
});

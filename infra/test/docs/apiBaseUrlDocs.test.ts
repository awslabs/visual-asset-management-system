/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every path in the API reference is relative to the REST API's stage-inclusive invoke URL, whatever
 * fronts the web application, and the pages that say which URL that is quote what the code builds:
 *   * the fixed stage name, in the OpenAPI server's example URL and the API overview's base URL;
 *   * the anonymous routes registered under `/api/`, which CloudFront forwards under their own path
 *     while every other `/api/*` path is forwarded without the prefix, and the path patterns the ALB
 *     redirects to the stage;
 *   * the configuration field that names an operator-supplied execute-api VPC endpoint for a
 *     `PRIVATE` API, and the one that serves the API on a custom domain;
 *   * the core stack output that carries the invoke URL;
 *   * the trailing slash the amplify-config function gives `api`;
 *   * the Stage Path Fronting section, which both OpenAPI base URL texts point at.
 * No page may say a front absorbs the stage, that a CLI profile or other client reaching the API
 * through a front survives a change of the invoke URL (setup stores the base URL that amplify-config
 * returns), or that the CloudFront `/api/*` behavior reaches only the bootstrap routes.
 *
 * Durable (root CLAUDE.md Rule 13): the stage, the routes, the redirects, the field, the output and the
 * section can change again, and either wrong wording can be written again.
 */

import * as fs from "fs";
import * as path from "path";
import { API_GATEWAY_STAGE_NAME, API_STAGE_PREFIXED_ROUTE_PATHS } from "../../config/config";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const DOCS = path.join(REPO_ROOT, "documentation", "docusaurus-site", "docs");
/** File text with line endings normalized, so a CRLF checkout reads the same as an LF one. */
const read = (rel: string): string =>
    fs.readFileSync(path.join(REPO_ROOT, rel), "utf8").replace(/\r\n/g, "\n");
const page = (rel: string): string =>
    read(path.join("documentation", "docusaurus-site", "docs", rel));
const OPENAPI = "documentation/VAMS_API.yaml";

const REST_API = "infra/lib/nestedStacks/apiLambda/constructs/rest-api-gateway-construct.ts";
const AMPLIFY_CONFIG =
    "infra/lib/nestedStacks/apiLambda/constructs/amplify-config-lambda-construct.ts";
const ALB = "infra/lib/nestedStacks/staticWebApp/constructs/alb-s3-website-albDeploy-construct.ts";
const CLOUDFRONT =
    "infra/lib/nestedStacks/staticWebApp/constructs/cloudfront-s3-website-construct.ts";
const CORE_STACK = "infra/lib/core-stack.ts";

/** The first capture of `re` in `text`, or "" when it does not match. */
function capture(text: string, re: RegExp): string {
    const m = re.exec(text);
    return m ? m[1] : "";
}

/** The text of a page section: its heading line up to the next heading of the same or a higher level. */
function section(text: string, heading: string): string {
    const lines = text.split("\n");
    const start = lines.indexOf(heading);
    if (start === -1) return "";
    const level = capture(heading, /^(#+)\s/).length;
    const next = lines.findIndex(
        (l, i) => i > start && /^#+\s/.test(l) && capture(l, /^(#+)\s/).length <= level
    );
    return lines.slice(start, next === -1 ? lines.length : next).join("\n");
}

/** Every Markdown page under the docs root, as a path relative to it. */
function docsPages(dir = ""): string[] {
    return fs.readdirSync(path.join(DOCS, dir), { withFileTypes: true }).flatMap((e) => {
        const rel = path.posix.join(dir, e.name);
        if (e.isDirectory()) return docsPages(rel);
        return /\.mdx?$/.test(e.name) ? [rel] : [];
    });
}

/** The sentences of a text: each line, split again after every full stop. */
function sentences(text: string): string[] {
    return text.split(/\n|(?<=\.)\s+/).filter((s) => s.trim() !== "");
}

/** The OpenAPI document's first server: its url and description, and its variable's default and description. */
function firstServer(): {
    url: string;
    description: string;
    variableDefault: string;
    variableDescription: string;
} {
    const block = capture(read(OPENAPI), /^servers:\n([\s\S]*?)^\S/m);
    return {
        url: capture(block, /^ {4}- url: "([^"]*)"$/m),
        description: capture(block, /^ {6}description: "([^"]*)"$/m),
        variableDefault: capture(block, /^ {14}default: "([^"]*)"$/m),
        variableDescription: capture(block, /^ {14}description: "([^"]*)"$/m),
    };
}

/** The anonymous routes the REST API registers under `/api/`. */
function apiPrefixedRoutes(): string[] {
    return Array.from(
        read(REST_API).matchAll(/registry\.register\(\{\s*path: "(\/api\/[^"]+)"/g),
        (m) => m[1]
    );
}

/** The path patterns of the ALB listener rules that redirect to the REST API stage. */
function albRedirectPatterns(): string[] {
    return Array.from(
        read(ALB).matchAll(
            /path: `\/\$\{props\.apiStageName\}\/#\{path\}`[\s\S]*?pathPatterns\(\[([^\]]*)\]\)/g
        ),
        (m) => Array.from(m[1].matchAll(/"([^"]+)"/g), (p) => p[1])
    ).flat();
}

/** The configuration field a `PRIVATE` API reads an operator-supplied execute-api VPC endpoint from. */
function externalEndpointField(): string {
    return capture(read(REST_API), /apiGatewayRest\.(optionalExternal\w+)/);
}

/** The configuration block that serves the REST API on a custom domain, as the docs name it. */
function customDomainField(): string {
    return /apiGatewayRest\.customDomain\b/.test(read(REST_API))
        ? "app.api.apiGatewayRest.customDomain"
        : "";
}

/** The managed CloudFront policies the `/api/*` behavior uses, by their console names. */
function catchAllManagedPolicies(): string[] {
    const text = read(CLOUDFRONT);
    return [
        text.includes("cloudfront.CachePolicy.CACHING_DISABLED") ? "CachingDisabled" : "",
        text.includes("cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER")
            ? "AllViewerExceptHostHeader"
            : "",
    ];
}

/** The core stack output whose value is the stage-inclusive invoke URL. */
function invokeUrlOutput(): string {
    return capture(
        read(CORE_STACK),
        /new cdk\.CfnOutput\(this, "(\w+)", \{\s*value: `\$\{apiNestedStack\.invokeUrlWithStage\}`/
    );
}

/** The OpenAPI description of the `api` property `GET /api/amplify-config` returns. */
function amplifyConfigApiDescription(): string {
    return capture(
        read(OPENAPI),
        /\/api\/amplify-config:[\s\S]*?\n\s+api:\n\s+type: string\n\s+description: "([^"]*)"/
    );
}

/** The two texts that say which URL every path is relative to. */
const BASE_URL_TEXTS: Array<[string, () => string]> = [
    ["VAMS_API.yaml servers[0]", () => firstServer().description],
    ["api/overview.md Base URL", () => section(page("api/overview.md"), "## Base URL")],
];

describe("The API base URL documentation matches the stage and the fronting", () => {
    test("the sources name the routes, redirects, field and output (control)", () => {
        expect(API_GATEWAY_STAGE_NAME).not.toBe("");
        expect(apiPrefixedRoutes()).toContain("/api/amplify-config");
        expect(apiPrefixedRoutes().sort()).toEqual([...API_STAGE_PREFIXED_ROUTE_PATHS].sort());
        expect(albRedirectPatterns()).toContain("/secure-config*");
        expect(externalEndpointField()).not.toBe("");
        expect(customDomainField()).not.toBe("");
        expect(catchAllManagedPolicies()).toEqual(["CachingDisabled", "AllViewerExceptHostHeader"]);
        expect(invokeUrlOutput()).not.toBe("");
    });

    test("Stage Path Fronting describes the CloudFront behaviors and the API custom domain", () => {
        const text = section(page("architecture/networking.md"), "### Stage Path Fronting");
        for (const routePath of API_STAGE_PREFIXED_ROUTE_PATHS) {
            expect(text).toContain(`\`${routePath}\``);
        }
        expect(text).toContain('`originPath: "/api"`');
        expect(text).toContain("`/api/*`");
        for (const policy of catchAllManagedPolicies()) {
            expect(text).toContain(`\`${policy}\``);
        }
        expect(text).toContain(`\`${customDomainField()}\``);
        expect(text).toContain(`https://{api-domain}/${API_GATEWAY_STAGE_NAME}/{route}`);
    });

    test.each(BASE_URL_TEXTS)("%s names the API custom domain field", (_, text) => {
        expect(text()).toContain(`\`${customDomainField()}\``);
    });

    test("the amplify-config `api` description names the API custom domain", () => {
        expect(amplifyConfigApiDescription()).toContain(`\`${customDomainField()}\``);
    });

    test("the OpenAPI server is the stage-inclusive invoke URL, with an example default", () => {
        const server = firstServer();
        expect(server.url).toBe("{apiGatewayUrl}");
        expect(server.variableDefault).toMatch(
            new RegExp(
                `^https://[a-z0-9]+\\.execute-api\\.[a-z0-9-]+\\.amazonaws\\.com/${API_GATEWAY_STAGE_NAME}$`
            )
        );
        expect(server.variableDescription).toContain(`\`${invokeUrlOutput()}\``);
    });

    test.each(BASE_URL_TEXTS)(
        "%s names the routes the web URL reaches and the ALB redirects",
        (_, text) => {
            const names = [...apiPrefixedRoutes(), ...albRedirectPatterns()];
            expect(names.filter((n) => !text().includes(`\`${n}\``))).toEqual([]);
        }
    );

    test.each(BASE_URL_TEXTS)(
        "%s names the endpoint a PRIVATE API is reached through",
        (_, text) => {
            expect(text()).toContain("`PRIVATE`");
            expect(text()).toContain(`\`${externalEndpointField()}\``);
        }
    );

    test("the API overview gives the stage-inclusive base URL, its output and Stage Path Fronting", () => {
        const text = section(page("api/overview.md"), "## Base URL");
        expect(text).toContain(
            `https://{api-id}.execute-api.{region}.amazonaws.com/${API_GATEWAY_STAGE_NAME}\n`
        );
        expect(text).toContain(`\`${invokeUrlOutput()}\``);
        expect(text).toContain("(../architecture/networking.md#stage-path-fronting)");
    });

    test("both OpenAPI base URL texts point at Stage Path Fronting", () => {
        expect(page("architecture/networking.md").split("\n")).toContain("### Stage Path Fronting");
        expect(firstServer().description).toContain("Stage Path Fronting");
        expect(amplifyConfigApiDescription()).toContain("Stage Path Fronting");
    });

    // The function appends "/" to the stage, so the documented value carries it in both API sources.
    test("the amplify-config `api` value is documented with its trailing slash", () => {
        expect(read(AMPLIFY_CONFIG)).toMatch(
            /config\.api = "https:\/\/" \+ host \+ stageSeg \+ "\/";/
        );
        expect(amplifyConfigApiDescription()).toContain("trailing slash");
        expect(capture(page("api/authentication.md"), /"api": "([^"]+)"/)).toMatch(
            new RegExp(`\\.execute-api\\.[a-z0-9-]+\\.amazonaws\\.com/${API_GATEWAY_STAGE_NAME}/$`)
        );
    });

    describe("wording no page may carry", () => {
        const absorbs = (s: string) => /absorb/i.test(s) && /\bstage\b/i.test(s);
        const survives = (s: string) =>
            /\b(profiles?|CLIs?|[Cc]lients?)\b/.test(s) &&
            /\b(CloudFront|ALB|front)\b/.test(s) &&
            /\b(unaffected|without change|work unchanged)\b/i.test(s);
        const bootstrapOnly = (s: string) =>
            s.includes("/api/*") &&
            /\b(reach(es)? only|only reach(es)?|for the bootstrap calls only)\b/i.test(s);

        test("the sentence checks flag the wordings they forbid (control)", () => {
            expect(absorbs("The front absorbs the stage path.")).toBe(true);
            expect(absorbs("The front maps /api/* onto the stage.")).toBe(false);
            expect(survives("A profile that targets a CloudFront front is unaffected.")).toBe(true);
            expect(
                survives("Clients that reach the API through the front continue to work unchanged.")
            ).toBe(true);
            expect(survives("The web application is unaffected.")).toBe(false);
            expect(
                bootstrapOnly("The CloudFront `/api/*` behavior reaches only `/api/version`.")
            ).toBe(true);
            expect(
                bootstrapOnly(
                    "The front maps `/api/*` onto the stage for the bootstrap calls only."
                )
            ).toBe(true);
            expect(bootstrapOnly("The `/api/*` behavior forwards every other path.")).toBe(false);
        });

        test("no page or OpenAPI text says the CloudFront /api/* behavior reaches only some routes", () => {
            const texts: Array<[string, string]> = [
                ...docsPages().map((rel): [string, string] => [rel, page(rel)]),
                [OPENAPI, read(OPENAPI)],
            ];
            const offenders = texts.flatMap(([rel, text]) =>
                sentences(text)
                    .filter(bootstrapOnly)
                    .map((s) => `${rel}: ${s.trim()}`)
            );
            expect(offenders).toEqual([]);
        });

        test("no page or OpenAPI text says a front absorbs the stage", () => {
            const texts: Array<[string, string]> = [
                ...docsPages().map((rel): [string, string] => [rel, page(rel)]),
                [OPENAPI, read(OPENAPI)],
            ];
            const offenders = texts.flatMap(([rel, text]) =>
                sentences(text)
                    .filter(absorbs)
                    .map((s) => `${rel}: ${s.trim()}`)
            );
            expect(offenders).toEqual([]);
        });

        // setup stores the invoke URL amplify-config returns, so a profile set up through a front
        // calls the stage directly and breaks with it.
        test("no page says a CLI profile set up through a front survives an invoke URL change", () => {
            const setup = read("tools/VamsCLI/vamscli/commands/setup.py");
            expect(setup).toContain("api_gateway_url = amplify_config.get('api')");
            expect(setup).toContain("'api_gateway_url': api_gateway_url,");
            const offenders = docsPages().flatMap((rel) =>
                sentences(page(rel))
                    .filter(survives)
                    .map((s) => `${rel}: ${s.trim()}`)
            );
            expect(offenders).toEqual([]);
        });
    });
});

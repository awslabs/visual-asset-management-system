/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as fs from "fs";
import * as path from "path";
import { expectAbsent, synthTemplate, SynthResult } from "../support/templateSynth";

/**
 * Grounds the numbers the API-stack-split rationale states in the synthesized templates rather than
 * in the construct source.
 *
 * The split rationale went stale once without anything noticing. It named the CloudFormation
 * per-template resource ceiling as the limit the primary API stack was approaching, and after the
 * REST migration put every API Gateway resource in the RestApi stack, that stack holds around a
 * fifth of it. A reader following the stale rationale would conclude the split had become
 * unnecessary — the opposite of what the ceilings that now bind call for.
 *
 * The assertions read the emitted templates, because two of the three ceilings are properties of a
 * template (its resource count and its serialized size) and the third is a property of the single
 * REST API both stacks feed, which no construct source states.
 *
 * Two kinds of assertion live here and they answer different questions. The first describe checks the
 * ceilings are not breached, in bands wide enough to survive ordinary growth. The second checks that
 * every document quoting a figure quotes the emitted one — `infra/CLAUDE.md` and the Kiro steering
 * document that Rule 11 keeps in step with it. Only the second protects the prose; the bands would
 * stay green through a doubling.
 *
 * App-wide log retention is NOT asserted here — `t1LogGroupRetentionInventory.test.ts` sweeps every
 * emitted log group across all three config templates, which is strictly more than a commercial-only
 * check would add, and Jest gives each file its own worker so a duplicate would cost a second
 * full-app synth for nothing.
 */

// Full-app synth from a config template costs ~20 s and the harness caches one result per template.
jest.setTimeout(600_000);

/** CloudFormation: maximum resources in one template. Hard limit, per template. */
const CFN_RESOURCES_PER_TEMPLATE = 500;

/** CloudFormation: maximum template body in an Amazon S3 object. Hard limit, per template. */
const CFN_TEMPLATE_BODY_BYTES = 1024 * 1024;

/** API Gateway: resources per REST API. Default quota, adjustable, per REST API. */
const APIGW_RESOURCES_PER_REST_API = 300;

/** A route only `apiBuilder-nestedStack.ts` registers, and one only `apiBuilder2` registers. */
const PRIMARY_STACK_ROUTE = "/buckets";
const SECONDARY_STACK_ROUTE = "/tag-types";

const synth = (): SynthResult => synthTemplate("commercial");

/**
 * The emitted template of the nested stack built by construct id `construct`.
 *
 * A substring match on the template key cannot separate these two stacks: `ApiBuilder2` and
 * `ApiBuilder` followed by a hash beginning with `2` read identically. CDK names a nested-stack
 * artifact `<sanitized stack name><construct path><8 hex uniqueid>`, so stripping the fixed-width
 * suffix and requiring the remainder to END with the construct id is exact. `aws:cdk:path` metadata
 * would be simpler but is added by the CDK CLI, not by `app.synth()`, so it is absent here.
 */
function nestedTemplateKeyFor(s: SynthResult, construct: string): string {
    const keys = Object.keys(s.templates)
        .map((key) => ({ key, id: key.replace(/\.nested$/, "") }))
        .filter(({ id }) => /[0-9A-F]{8}$/.test(id) && id.slice(0, -8).endsWith(construct))
        .map(({ key }) => key);
    if (keys.length !== 1) {
        throw new Error(
            `expected exactly one nested template for construct ${construct}, found ` +
                `${keys.length} (${keys.join(
                    ", "
                )}). Without it every count below would be taken ` +
                `from the wrong template or from nothing at all.`
        );
    }
    return keys[0];
}

/** Resources declared in one emitted template. */
const resourceCount = (s: SynthResult, key: string): number =>
    Object.keys(s.templates[key].Resources ?? {}).length;

/**
 * Serialized size of one emitted template, in bytes.
 *
 * CDK writes `*.template.json` minified, so `JSON.stringify` of the parsed template is byte-for-byte
 * the object CloudFormation reads from Amazon S3 — not an approximation of it.
 */
const templateBytes = (s: SynthResult, key: string): number =>
    JSON.stringify(s.templates[key]).length;

/** The inline OpenAPI paths of the single `SpecRestApi`. */
function restApiPaths(s: SynthResult): string[] {
    const apis = s.ofType("AWS::ApiGateway::RestApi");
    if (apis.length !== 1) {
        throw new Error(`expected exactly one AWS::ApiGateway::RestApi, found ${apis.length}`);
    }
    const paths = Object.keys(apis[0].properties.Body?.paths ?? {});
    if (paths.length === 0) {
        throw new Error(
            "the RestApi carries no inline OpenAPI paths; every count below would be 0"
        );
    }
    return paths;
}

/**
 * Distinct nodes in the REST API path tree — what API Gateway counts as "resources per REST API".
 *
 * The quota counts tree nodes, not routes: `/database/{databaseId}/assets` is three nodes, and a
 * sibling path sharing that prefix adds only its own leaf. Counting OpenAPI paths instead would
 * under-report on a deep tree and over-report on a flat one.
 */
function restApiPathNodes(s: SynthResult): number {
    const nodes = new Set<string>();
    for (const p of restApiPaths(s)) {
        let acc = "";
        for (const segment of p.split("/").filter(Boolean)) {
            acc += `/${segment}`;
            nodes.add(acc);
        }
    }
    return nodes.size;
}

describe("API stack split — the ceilings the rationale names", () => {
    test("both API builder stacks and the REST API are emitted", () => {
        // Positive control. Every assertion below is a count taken from one of these three
        // artifacts; if any were missing, the helper throws rather than reporting a comfortable 0.
        const s = synth();
        expect(resourceCount(s, nestedTemplateKeyFor(s, "ApiBuilder"))).toBeGreaterThan(0);
        expect(resourceCount(s, nestedTemplateKeyFor(s, "ApiBuilder2"))).toBeGreaterThan(0);
        expect(restApiPathNodes(s)).toBeGreaterThan(0);
    });

    test("neither API stack is near the CloudFormation resource ceiling", () => {
        // The stale rationale claimed the primary stack was approaching this. Documented as "well
        // under half"; the band is what the documentation may keep saying without being rewritten.
        const s = synth();
        for (const construct of ["ApiBuilder", "ApiBuilder2"]) {
            const count = resourceCount(s, nestedTemplateKeyFor(s, construct));
            expect(count).toBeLessThan(CFN_RESOURCES_PER_TEMPLATE / 2);
        }
    });

    test("template body size fills faster than resource count in the primary API stack", () => {
        // The documented reason the split still buys headroom: the primary stack sits around a fifth
        // of the resource ceiling while its template is already around two fifths of the 1 MB body
        // limit, so a resource count understates how full it is. Both ceilings are per-template, so
        // both are relieved by keeping the stacks split.
        const s = synth();
        const key = nestedTemplateKeyFor(s, "ApiBuilder");
        const resourceFraction = resourceCount(s, key) / CFN_RESOURCES_PER_TEMPLATE;
        const byteFraction = templateBytes(s, key) / CFN_TEMPLATE_BODY_BYTES;

        expect(byteFraction).toBeGreaterThan(resourceFraction);
        expect(byteFraction).toBeLessThan(1);
    });

    test("the REST API path tree is within the API Gateway per-API quota", () => {
        expect(restApiPathNodes(synth())).toBeLessThan(APIGW_RESOURCES_PER_REST_API);
    });

    test("one REST API materializes the routes of both API stacks", () => {
        // Grounds the documented claim that splitting the CDK stacks does NOT relieve the API
        // Gateway quota: the path tree is built from what both stacks register into one
        // RouteRegistry, so its size is a whole-deployment figure rather than a per-stack one.
        const paths = restApiPaths(synth());
        expect(paths).toContain(PRIMARY_STACK_ROUTE);
        expect(paths).toContain(SECONDARY_STACK_ROUTE);
    });

    test("no path node is a CloudFormation resource", () => {
        // And the reason the CloudFormation per-template ceilings cannot govern the path tree: the
        // API is a SpecRestApi, so API Gateway creates the tree from the inline OpenAPI document and
        // no AWS::ApiGateway::Resource is emitted anywhere in the assembly.
        const s = synth();
        expectAbsent("AWS::ApiGateway::Resource", s.ofType("AWS::ApiGateway::Resource"), {
            description: "the assembly emits a REST API with a non-empty path tree",
            count: restApiPathNodes(s),
        });
    });
});

/**
 * The figures the documentation states, read out of the documentation itself.
 *
 * The tests above assert the ceilings are not breached, in bands wide enough to survive ordinary
 * growth. That is deliberate — a test that fails every time an endpoint is added is a test somebody
 * deletes — but it means they do NOT protect the numbers in the table. `apiBuilder` could reach 200
 * resources and 0.90 MB with every band above still satisfied while the table still read 106 and
 * ~0.40 MB, which is exactly the drift that made the previous rationale stale.
 *
 * So the table is parsed and compared. The assertion subject is the prose, which makes
 * "this table cannot silently go stale" in `infra/CLAUDE.md` a statement this file backs rather than
 * an assurance nothing checks. Adding an endpoint now fails here, and the fix is to update the one
 * table the failure names — the same maintenance the CSP hash list already imposes.
 */
/**
 * Every document that states the figures. Root `CLAUDE.md` Rule 11 requires the Kiro steering document
 * to mirror the `CLAUDE.md` guidance, so the numbers are deliberately duplicated there — which means a
 * guard reading only one file leaves the other free to drift, the same defect one file over. Both are
 * parsed, and each is required to state a figure so a copy that quietly drops the table is caught too.
 */
const FIGURE_SOURCES = [
    path.join(__dirname, "..", "..", "CLAUDE.md"),
    path.join(
        __dirname,
        "..",
        "..",
        "..",
        ".kiro",
        "steering",
        "BACKEND_CDK_DEVELOPMENT_WORKFLOW.md"
    ),
];

/** One document's figures. `null` for a figure that document does not state. */
interface DocumentedFigures {
    source: string;
    resources: Record<string, number> | null;
    megabytes: Record<string, number> | null;
    pathNodes: number | null;
    openApiPaths: number | null;
}

/**
 * The figures each document states, matched on the numbers rather than on the sentence around them.
 *
 * The two documents word this differently — a table in `infra/CLAUDE.md`, prose in the Kiro steering
 * document — so the patterns deliberately anchor only on the values and their units. A document that
 * states none of them is reported by the caller rather than silently contributing nothing.
 */
function documentedFigures(source: string): DocumentedFigures {
    const md = fs.readFileSync(source, "utf8");

    // Matched per stack rather than as one pair, and bounded to a single LINE rather than to a
    // sentence. A pair pattern spanning both stacks has to cross whatever sits between them, and in
    // the Kiro document that is "~0.40 MB" — so a `[^.\n]` bridge cannot get past the decimal point
    // and silently matches nothing, which is how the first version of this guard passed while
    // checking only one of the two files.
    const count = (stack: string): number | null => {
        const m = new RegExp("`" + stack + "` (?:emits )?(\\d+)").exec(md);
        return m ? Number(m[1]) : null;
    };
    const mb = (stack: string): number | null => {
        const m = new RegExp("`" + stack + "`[^\\n]*?~([\\d.]+) MB").exec(md);
        return m ? Number(m[1]) : null;
    };

    const resources = { ApiBuilder: count("apiBuilder"), ApiBuilder2: count("apiBuilder2") };
    const megabytes = { ApiBuilder: mb("apiBuilder"), ApiBuilder2: mb("apiBuilder2") };
    const tree = /(\d+) path-tree nodes \((\d+) OpenAPI paths\)/.exec(md);

    const complete = <T>(o: Record<string, T | null>): Record<string, T> | null =>
        Object.values(o).some((v) => v === null) ? null : (o as Record<string, T>);

    return {
        source: path.relative(path.join(__dirname, "..", "..", ".."), source),
        resources: complete(resources),
        megabytes: complete(megabytes),
        pathNodes: tree ? Number(tree[1]) : null,
        openApiPaths: tree ? Number(tree[2]) : null,
    };
}

/** Every figure, all present — what `infra/CLAUDE.md` is required to state. */
type RequiredFigures = {
    [K in keyof DocumentedFigures]: NonNullable<DocumentedFigures[K]>;
};

/** The `infra/CLAUDE.md` table, which is the one document required to state every figure. */
function documentedCeilings(): RequiredFigures {
    const doc = documentedFigures(FIGURE_SOURCES[0]);
    if (!doc.resources || !doc.megabytes || doc.pathNodes === null || doc.openApiPaths === null) {
        throw new Error(
            `could not parse the API Stack Ceilings table out of ${doc.source}. If the table was ` +
                `reworded, update these patterns in the same change — otherwise this file silently ` +
                `stops guarding the figures it exists to guard.`
        );
    }
    return doc as RequiredFigures;
}

describe("the documented API-stack figures match the synthesized templates", () => {
    test("the table is parseable and holds non-zero figures", () => {
        // Positive control. Every comparison below is against a parsed number; a regex that stopped
        // matching would otherwise throw once here and, if the throw were ever softened to a default,
        // compare zero against zero.
        const doc = documentedCeilings();
        expect(doc.resources.ApiBuilder).toBeGreaterThan(0);
        expect(doc.resources.ApiBuilder2).toBeGreaterThan(0);
        expect(doc.megabytes.ApiBuilder).toBeGreaterThan(0);
        expect(doc.pathNodes).toBeGreaterThan(0);
        expect(doc.openApiPaths).toBeGreaterThan(0);
    });

    test("the documented resource counts are the counts emitted", () => {
        const s = synth();
        const doc = documentedCeilings();
        const actual = {
            ApiBuilder: resourceCount(s, nestedTemplateKeyFor(s, "ApiBuilder")),
            ApiBuilder2: resourceCount(s, nestedTemplateKeyFor(s, "ApiBuilder2")),
        };
        expect(actual).toEqual(doc.resources);
    });

    test("the documented template body sizes are the sizes emitted", () => {
        // Compared at the precision the table states (two decimals of a MB, ~10 kB), because that is
        // the claim being made. A change too small to move the second decimal is not a stale table.
        const s = synth();
        const doc = documentedCeilings();
        const round = (bytes: number): number =>
            Math.round((bytes / CFN_TEMPLATE_BODY_BYTES) * 100) / 100;
        const actual = {
            ApiBuilder: round(templateBytes(s, nestedTemplateKeyFor(s, "ApiBuilder"))),
            ApiBuilder2: round(templateBytes(s, nestedTemplateKeyFor(s, "ApiBuilder2"))),
        };
        expect(actual).toEqual(doc.megabytes);
    });

    test("the documented path-tree and OpenAPI path counts are the counts emitted", () => {
        const s = synth();
        const doc = documentedCeilings();
        expect(restApiPathNodes(s)).toBe(doc.pathNodes);
        expect(restApiPaths(s)).toHaveLength(doc.openApiPaths);
    });

    test("every document stating the figures states the same ones", () => {
        // Rule 11 duplicates these numbers into the Kiro steering document, so both are checked
        // against the synthesized templates. Each source must state at least one figure: a copy that
        // drops the numbers entirely would otherwise pass by having nothing to disagree with.
        const s = synth();
        const actual = {
            resources: {
                ApiBuilder: resourceCount(s, nestedTemplateKeyFor(s, "ApiBuilder")),
                ApiBuilder2: resourceCount(s, nestedTemplateKeyFor(s, "ApiBuilder2")),
            },
            megabytes: {
                ApiBuilder:
                    Math.round(
                        (templateBytes(s, nestedTemplateKeyFor(s, "ApiBuilder")) /
                            CFN_TEMPLATE_BODY_BYTES) *
                            100
                    ) / 100,
                ApiBuilder2:
                    Math.round(
                        (templateBytes(s, nestedTemplateKeyFor(s, "ApiBuilder2")) /
                            CFN_TEMPLATE_BODY_BYTES) *
                            100
                    ) / 100,
            },
        };

        const disagreements: string[] = [];
        for (const source of FIGURE_SOURCES) {
            const doc = documentedFigures(source);
            if (!doc.resources && !doc.megabytes && doc.pathNodes === null) {
                disagreements.push(`${doc.source}: states none of the figures`);
                continue;
            }
            if (
                doc.resources &&
                JSON.stringify(doc.resources) !== JSON.stringify(actual.resources)
            ) {
                disagreements.push(
                    `${doc.source}: resources ${JSON.stringify(doc.resources)} != ${JSON.stringify(
                        actual.resources
                    )}`
                );
            }
            if (
                doc.megabytes &&
                JSON.stringify(doc.megabytes) !== JSON.stringify(actual.megabytes)
            ) {
                disagreements.push(
                    `${doc.source}: MB ${JSON.stringify(doc.megabytes)} != ${JSON.stringify(
                        actual.megabytes
                    )}`
                );
            }
            if (doc.pathNodes !== null && doc.pathNodes !== restApiPathNodes(s)) {
                disagreements.push(
                    `${doc.source}: path nodes ${doc.pathNodes} != ${restApiPathNodes(s)}`
                );
            }
        }
        expect(disagreements).toEqual([]);
    });
});

describe("nested-stack CloudFormation Outputs ceiling", () => {
    /**
     * CloudFormation allows 200 Outputs per template and the limit is not adjustable. The storage stack is
     * the one anywhere near it: every table referenced from a sibling nested stack contributes a
     * `tableName` Output for its SSM parameter, plus a `tableArn` Output where a cross-stack grant needs
     * one, and `ResourceNamesBuilder` consumes 64 of them as its own Parameters.
     *
     * Exceeding 200 is rejected at ValidateTemplate — the same class of ceiling that already forced the
     * apiBuilder / apiBuilder2 split. A threshold below the hard limit is what makes that discoverable
     * while there is still headroom to design the split, rather than at the deploy that crosses it.
     *
     * Counted from a fresh synth rather than the checked-in cdk.out artifact, which can be stale.
     */
    const OUTPUTS_HARD_LIMIT = 200;
    const OUTPUTS_THRESHOLD = 170;

    const outputCounts = (): { name: string; outputs: number }[] =>
        Object.entries(synth().templates).map(([name, template]) => ({
            name,
            outputs: Object.keys((template as { Outputs?: object }).Outputs ?? {}).length,
        }));

    test("the synth exposes templates with Outputs to count", () => {
        // Control: an assembly whose templates carried no Outputs would satisfy every ceiling below.
        const counts = outputCounts();
        expect(counts.length).toBeGreaterThan(5);
        expect(counts.some((c) => c.outputs > 20)).toBe(true);
    });

    test("no nested template is within 30 Outputs of the CloudFormation limit", () => {
        const over = outputCounts().filter((c) => c.outputs > OUTPUTS_THRESHOLD);
        expect(
            over.map((c) => `${c.name}: ${c.outputs} Outputs (threshold ${OUTPUTS_THRESHOLD})`)
        ).toEqual([]);
    });

    test("the storage stack is the one that governs this ceiling", () => {
        // Recorded so a future reader knows where the headroom actually is: if some other stack ever
        // overtakes it, the split reasoning above needs revisiting rather than the threshold raising.
        const counts = outputCounts().sort((a, b) => b.outputs - a.outputs);
        expect(counts[0].name).toMatch(/StorageResourcesBuilder/);
        expect(counts[0].outputs).toBeLessThan(OUTPUTS_HARD_LIMIT);
        // The gap to the runner-up is the reason only one stack needs watching.
        expect(counts[0].outputs).toBeGreaterThan(counts[1].outputs * 2);
    });
});

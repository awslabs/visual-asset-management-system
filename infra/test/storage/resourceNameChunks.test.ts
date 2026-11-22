/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The resource-name chunk parameters (lib/nestedStacks/resourceNames/resourceNameChunks.ts).
 *
 * Every registered resource name is published a second time as JSON in numbered parameters
 * `<prefix>Chunks/<i>`, which backend/common/resourceNames.py reads with one GetParameters call per
 * 10 chunks. Asserted here: the packing and its bounds on the planner itself, then on full synths of
 * the shipped templates (add-ons off and every add-on on) the layout, the header, the worst case of
 * every chunk, that each chunk lives in the stack holding its names' per-key parameters and adds no
 * cross-stack reference there, and the Lambda grant on the chunk path.
 */

import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import { ADDON_RESOURCE_PARAM_KEYS } from "../../common/addonResourceParamKeys";
import { publishCustomResourceName } from "../../lib/nestedStacks/custom/customStackHelpers";
import { ResourceNameRegistry } from "../../lib/nestedStacks/resourceNames/resourceNameRegistry";
import {
    buildResourceNameChunks,
    planResourceNameChunks,
    renderResourceNameChunk,
    RESOURCE_NAME_CHUNK_COUNT_LIMIT,
    RESOURCE_NAME_CHUNK_MAX_CHARS,
    TOKEN_MAX_LENGTH_BY_CATEGORY,
    UNKNOWN_TOKEN_MAX_LENGTH,
    worstCaseValueChars,
} from "../../lib/nestedStacks/resourceNames/resourceNameChunks";
import { enableEdh } from "../addon/edh/support/edhSynth";
import {
    actionsOf,
    resourcesOf,
    SynthResult,
    synthTemplate,
    TemplateName,
    ALL_TEMPLATES,
} from "../support/templateSynth";
import { newTestApp } from "../support/testApp";

jest.setTimeout(1_200_000);

const tokenValue = (stack: cdk.Stack, id: string): string =>
    new cdk.CfnResource(stack, id, { type: "AWS::DynamoDB::Table" }).ref;

describe("planResourceNameChunks", () => {
    const stack = () => new cdk.Stack(newTestApp(), "PlanStack");

    test("sizes a token by its category and a literal by its own length", () => {
        const s = stack();
        const t = tokenValue(s, "T");
        expect(worstCaseValueChars({ key: "dynamoTables/a", value: t })).toBe(255 + 2);
        expect(worstCaseValueChars({ key: "addon/dynamoTables/a", value: t })).toBe(255 + 2);
        expect(worstCaseValueChars({ key: "s3Buckets/a", value: t })).toBe(63 + 2);
        expect(worstCaseValueChars({ key: "cloudwatchLogGroups/a", value: t })).toBe(512 + 2);
        expect(worstCaseValueChars({ key: "integration/a", value: t })).toBe(
            UNKNOWN_TOKEN_MAX_LENGTH + 2
        );
        expect(worstCaseValueChars({ key: "integration/a", value: t, maxLength: 80 })).toBe(82);
        expect(worstCaseValueChars({ key: "integration/a", value: 'a"b' })).toBe(
            JSON.stringify('a"b').length
        );
        expect(TOKEN_MAX_LENGTH_BY_CATEGORY).toEqual({
            dynamoTables: 255,
            s3Buckets: 63,
            cloudwatchLogGroups: 512,
        });
    });

    test("packs in order, keeps every worst case within 4,096 and numbers the chunks 0..n-1", () => {
        const s = stack();
        const other = new cdk.Stack(newTestApp(), "OtherStack");
        const core = Array.from({ length: 40 }, (_, i) => ({
            key: `dynamoTables/table${i}`,
            value: tokenValue(s, `Table${i}`),
        }));
        const lane = [{ key: "custom/dynamoTables/widgets", value: tokenValue(other, "W") }];
        const plan = planResourceNameChunks([
            { owner: s, entries: core },
            { owner: other, entries: lane },
        ]);

        expect(plan.chunks.map((c) => c.index)).toEqual(
            Array.from({ length: plan.count }, (_, i) => i)
        );
        expect(plan.chunks.every((c) => c.worstCaseChars <= RESOURCE_NAME_CHUNK_MAX_CHARS)).toBe(
            true
        );
        // 40 table names at the 255-character bound fill 14 + 14 + 12; the lane stack gets its own.
        expect(plan.count).toBe(4);
        expect(plan.chunks.map((c) => c.entries.length)).toEqual([14, 14, 12, 1]);
        expect(plan.chunks.map((c) => c.owner)).toEqual([s, s, s, other]);
        expect(plan.chunks.flatMap((c) => c.entries.map((e) => e.key))).toEqual([
            ...core.map((e) => e.key),
            "custom/dynamoTables/widgets",
        ]);
        expect(plan.layoutId).toMatch(/^[0-9a-f]{12}$/);
    });

    test("the layout id follows the keys and their chunks, not the values", () => {
        const s = stack();
        const entries = (n: number, value: (i: number) => string) =>
            Array.from({ length: n }, (_, i) => ({ key: `dynamoTables/t${i}`, value: value(i) }));
        const a = planResourceNameChunks([{ owner: s, entries: entries(3, (i) => `a${i}`) }]);
        const b = planResourceNameChunks([{ owner: s, entries: entries(3, (i) => `b${i}`) }]);
        const c = planResourceNameChunks([{ owner: s, entries: entries(4, (i) => `a${i}`) }]);
        expect(a.layoutId).toBe(b.layoutId);
        expect(c.layoutId).not.toBe(a.layoutId);
    });

    test("renders JSON a reader parses, escaping a literal", () => {
        const s = stack();
        const plan = planResourceNameChunks([
            {
                owner: s,
                entries: [
                    { key: "integration/prefix", value: 'vams."quoted"\\x' },
                    { key: "dynamoTables/b", value: "table-b" },
                ],
            },
        ]);
        const parsed = JSON.parse(renderResourceNameChunk(plan, plan.chunks[0]));
        expect(parsed).toEqual({
            v: 1,
            n: 1,
            i: 0,
            l: plan.layoutId,
            m: { "integration/prefix": 'vams."quoted"\\x', "dynamoTables/b": "table-b" },
        });
    });

    test("refuses a key SSM cannot name, a name no chunk holds and too many chunks", () => {
        const s = stack();
        expect(() =>
            planResourceNameChunks([{ owner: s, entries: [{ key: 'bad"key', value: "x" }] }])
        ).toThrow(/not a valid SSM parameter path/);
        expect(() =>
            planResourceNameChunks([
                { owner: s, entries: [{ key: "integration/big", value: "x".repeat(4100) }] },
            ])
        ).toThrow(/no 4096-character chunk holds/);
        const many = Array.from({ length: RESOURCE_NAME_CHUNK_COUNT_LIMIT + 1 }, (_, i) => ({
            owner: new cdk.Stack(newTestApp(), `Owner${i}`),
            entries: [{ key: `dynamoTables/t${i}`, value: "x" }],
        }));
        expect(() => planResourceNameChunks(many)).toThrow(/exceed the 100/);
    });
});

describe("lane names reach the chunks of their own stack", () => {
    test("a custom name published in a nested stack lands in that stack's chunk", () => {
        const app = newTestApp();
        const root = new cdk.Stack(app, "Root");
        const registry = new ResourceNameRegistry();
        ResourceNameRegistry.attach(root, registry);
        const coreStack = new cdk.NestedStack(root, "ResourceNamesBuilder");
        registry.register({ paramKey: "dynamoTables/assetStorage", value: "assets" });
        const custom = new cdk.NestedStack(root, "CustomBuilder");
        const config = { resourceNamesSSMParamPrefix: "/vams-test/resourceNames" } as any;
        publishCustomResourceName(
            custom,
            config,
            "custom/dynamoTables/widgetStorage",
            tokenValue(custom, "Widgets")
        );

        const plan = buildResourceNameChunks({ config, registry, coreOwner: coreStack });

        expect(plan.count).toBe(2);
        expect(plan.chunks[1].owner).toBe(custom);
        const template = Template.fromStack(custom);
        template.hasResourceProperties("AWS::SSM::Parameter", {
            Name: "/vams-test/resourceNamesChunks/1",
            Tier: "Standard",
        });
        const chunk = Object.values(template.findResources("AWS::SSM::Parameter")).find(
            (r: any) => r.Properties.Name === "/vams-test/resourceNamesChunks/1"
        ) as any;
        const flat = SynthResult.flatten(chunk.Properties.Value);
        expect(flat).toBe(
            `{"v":1,"n":2,"i":1,"l":"${plan.layoutId}","m":{"custom/dynamoTables/widgetStorage":"\${Widgets}"}}`
        );
        Template.fromStack(coreStack).hasResourceProperties("AWS::SSM::Parameter", {
            Name: "/vams-test/resourceNamesChunks/0",
        });
    });

    test("a stack outside a VAMS core stack publishes per key only", () => {
        const stack = new cdk.Stack(newTestApp(), "Bare");
        const config = { resourceNamesSSMParamPrefix: "/vams-test/resourceNames" } as any;
        expect(() =>
            publishCustomResourceName(stack, config, "custom/dynamoTables/x", "x")
        ).not.toThrow();
        expect(ResourceNameRegistry.of(stack)).toBeUndefined();
    });

    test("a lane key published twice fails at synth", () => {
        const root = new cdk.Stack(newTestApp(), "Root");
        const registry = new ResourceNameRegistry();
        ResourceNameRegistry.attach(root, registry);
        const config = { resourceNamesSSMParamPrefix: "/vams-test/resourceNames" } as any;
        const a = new cdk.NestedStack(root, "A");
        const b = new cdk.NestedStack(root, "B");
        publishCustomResourceName(a, config, "custom/dynamoTables/x", "x");
        expect(() => publishCustomResourceName(b, config, "custom/dynamoTables/x", "y")).toThrow(
            /published twice/
        );
    });
});

/** Every add-on on, with placeholder settings (no real credential). */
function allAddonsOn(c: any): void {
    enableEdh(c);
    c.app.addons.usePhysnaSync.enabled = true;
    c.app.addons.usePhysnaSync.tenantId = "11111111-2222-3333-4444-555555555555";
    c.app.addons.useGarnetFramework.enabled = true;
    c.app.addons.useGarnetFramework.garnetApiEndpoint = "https://garnet.example.com";
    c.app.addons.useGarnetFramework.garnetApiToken = "placeholder";
    c.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl =
        "https://sqs.us-east-1.amazonaws.com/123456789012/garnet-ingest";
}

const synthFor = (name: TemplateName, addons: boolean): SynthResult =>
    addons
        ? synthTemplate(name, { mutate: allAddonsOn, mutateKey: "resourceNameChunksAllAddonsOn" })
        : synthTemplate(name);

const prefixOf = (s: SynthResult) => `/vams-t1-${s.name}/resourceNames`;

interface SynthChunk {
    index: number;
    stack: string;
    flat: string;
    parsed: any;
    refs: string[];
    tier: string;
}

/** The chunk parameters of a synth, parsed with every token replaced by a placeholder. */
function chunksOf(s: SynthResult): SynthChunk[] {
    const chunkPrefix = `${prefixOf(s)}Chunks/`;
    return s
        .ofType("AWS::SSM::Parameter")
        .filter((r) => SynthResult.flatten(r.properties.Name).startsWith(chunkPrefix))
        .map((r) => {
            const flat = SynthResult.flatten(r.properties.Value);
            const refs = flat.match(/\$\{[^}]+\}/g) ?? [];
            return {
                index: Number(SynthResult.flatten(r.properties.Name).slice(chunkPrefix.length)),
                stack: r.stack,
                flat,
                parsed: JSON.parse(flat.replace(/\$\{[^}]+\}/g, "token")),
                refs,
                tier: r.properties.Tier,
            };
        })
        .sort((a, b) => a.index - b.index);
}

/** The per-key parameters under the prefix, by key, with the stack holding each. */
function perKeyParametersOf(s: SynthResult): Map<string, { stack: string; flat: string }> {
    const prefix = `${prefixOf(s)}/`;
    const out = new Map<string, { stack: string; flat: string }>();
    for (const r of s.ofType("AWS::SSM::Parameter")) {
        const name = SynthResult.flatten(r.properties.Name);
        if (name.startsWith(prefix)) {
            out.set(name.slice(prefix.length), {
                stack: r.stack,
                flat: SynthResult.flatten(r.properties.Value),
            });
        }
    }
    return out;
}

/** Keys published per key but deliberately outside the registry and the lane helpers. */
const LATE_BOUND_KEYS = new Set<string>([
    RESOURCE_PARAM_KEYS.lambdaFunctions.crOsReindexer,
    RESOURCE_PARAM_KEYS.lambdaFunctions.vectorReindexer,
    RESOURCE_PARAM_KEYS.lambdaFunctions.importGlobalPipelineWorkflow,
    RESOURCE_PARAM_KEYS.lambdaFunctions.largeObjectCopyEngine,
    RESOURCE_PARAM_KEYS.integration.workflowExecutionRoleArn,
]);
const isLateBound = (key: string) => LATE_BOUND_KEYS.has(key) || key.startsWith("securityGroups/");

function worstCaseOf(chunk: SynthChunk): number {
    // Each token stands for its key's category bound, as the planner sized it.
    let chars = chunk.flat.length;
    const pattern = /"([^"]+)":"(\$\{[^}]+\})"/g;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(chunk.flat)) !== null) {
        const category = match[1]
            .split("/")
            .slice(0, -1)
            .find((segment) => segment in TOKEN_MAX_LENGTH_BY_CATEGORY);
        const bound = category ? TOKEN_MAX_LENGTH_BY_CATEGORY[category] : UNKNOWN_TOKEN_MAX_LENGTH;
        chars += bound - match[2].length;
    }
    return chars;
}

describe.each([
    ["commercial", false],
    ["commercial", true],
    ["govcloud", false],
    ["eusovereign", false],
] as [TemplateName, boolean][])("chunk parameters: %s template, add-ons on %s", (name, addons) => {
    let s: SynthResult;
    let chunks: SynthChunk[];

    beforeAll(() => {
        s = synthFor(name, addons);
        chunks = chunksOf(s);
    });

    test("are numbered 0..n-1, standard tier, each with the shared header", () => {
        expect(chunks.length).toBeGreaterThan(0);
        const n = chunks.length;
        expect(chunks.map((c) => c.index)).toEqual(Array.from({ length: n }, (_, i) => i));
        const layout = chunks[0].parsed.l;
        expect(layout).toMatch(/^[0-9a-f]{12}$/);
        for (const chunk of chunks) {
            expect(chunk.parsed).toEqual(
                expect.objectContaining({ v: 1, n, i: chunk.index, l: layout })
            );
            expect(chunk.tier).toBe("Standard");
        }
    });

    test("every chunk's worst case fits 4,096 characters", () => {
        for (const chunk of chunks) {
            expect(worstCaseOf(chunk)).toBeLessThanOrEqual(RESOURCE_NAME_CHUNK_MAX_CHARS);
        }
    });

    test("carry every registered and lane name exactly once, beside its per-key parameter", () => {
        const perKey = perKeyParametersOf(s);
        const carried = chunks.flatMap((c) =>
            Object.keys(c.parsed.m).map((key) => ({ key, chunk: c }))
        );
        const keys = carried.map((c) => c.key);
        expect(new Set(keys).size).toBe(keys.length);
        const expected = [...perKey.keys()].filter((k) => !isLateBound(k)).sort();
        expect([...keys].sort()).toEqual(expected);
        for (const { key, chunk } of carried) {
            // Same stack as the per-key parameter, and the same value expression.
            expect(perKey.get(key)?.stack).toBe(chunk.stack);
            const value = chunk.flat.match(
                new RegExp(`"${key.replace(/[/.]/g, "\\$&")}":"([^"]*)"`)
            )?.[1];
            expect(value).toBe(perKey.get(key)?.flat);
        }
    });

    test("reference nothing the per-key parameters of their stack do not", () => {
        // A chunk adds no cross-stack parameter: every reference it makes is one a per-key
        // parameter in the same template already makes.
        const perKeyRefs = new Map<string, Set<string>>();
        for (const [, p] of perKeyParametersOf(s)) {
            const refs = perKeyRefs.get(p.stack) ?? new Set<string>();
            (p.flat.match(/\$\{[^}]+\}/g) ?? []).forEach((r) => refs.add(r));
            perKeyRefs.set(p.stack, refs);
        }
        for (const chunk of chunks) {
            for (const ref of chunk.refs) {
                expect(perKeyRefs.get(chunk.stack)?.has(ref)).toBe(true);
            }
        }
    });

    test("Lambda roles read the chunk path with ssm:GetParameters only", () => {
        const chunkArnTail = `:parameter/vams-t1-${name}/resourceNamesChunks/*`;
        const statements = [
            ...s.ofType("AWS::IAM::Policy"),
            ...s.ofType("AWS::IAM::ManagedPolicy"),
        ].flatMap((p) => (p.properties.PolicyDocument?.Statement ?? []) as any[]);
        const onChunks = statements.filter((st) =>
            resourcesOf(st).some((r) => r.endsWith(chunkArnTail))
        );
        expect(onChunks.length).toBeGreaterThan(0);
        for (const st of onChunks) {
            expect(st.Effect).toBe("Allow");
            expect(actionsOf(st)).toEqual(["ssm:GetParameters"]);
            for (const r of resourcesOf(st).filter((x) => x.includes("resourceNamesChunks"))) {
                expect(r).toBe(`arn:${s.partition}:ssm:${s.region}:123456789012${chunkArnTail}`);
            }
        }
        // No grant reaches the chunk path through a wider pattern on the prefix.
        const wider = statements.filter((st) =>
            resourcesOf(st).some((r) => /\/resourceNames\*$/.test(r))
        );
        expect(wider).toEqual([]);
    });
});

/** Nested stacks that hold chunks, most deeply nested first. */
const CHUNK_STACKS = [
    "EdhWorkstationsBuilder",
    "PhysnaSyncBuilder",
    "GarnetFrameworkBuilder",
    "CustomBuilder",
    "ResourceNamesBuilder",
];

describe("chunk layout per configuration (recorded: a change that adds names shows here)", () => {
    const layout = (s: SynthResult) => {
        const perStack: Record<string, number> = {};
        for (const chunk of chunksOf(s)) {
            const stack = CHUNK_STACKS.find((label) => chunk.stack.includes(label)) ?? chunk.stack;
            perStack[stack] = (perStack[stack] ?? 0) + 1;
        }
        const names = chunksOf(s).reduce((sum, c) => sum + Object.keys(c.parsed.m).length, 0);
        return { chunks: chunksOf(s).length, names, perStack };
    };

    test("commercial template, add-ons off", () => {
        expect(layout(synthFor("commercial", false))).toEqual({
            chunks: 8,
            names: 76,
            perStack: { ResourceNamesBuilder: 8 },
        });
    });

    test("commercial template, every add-on on", () => {
        const s = synthFor("commercial", true);
        expect(layout(s)).toEqual({
            chunks: 9,
            names: 78,
            perStack: { ResourceNamesBuilder: 8, EdhWorkstationsBuilder: 1 },
        });
        const edhKeys = Object.values(ADDON_RESOURCE_PARAM_KEYS.dynamoTables);
        const edhChunks = chunksOf(s).filter((c) => edhKeys.some((k) => k in c.parsed.m));
        expect(edhChunks.every((c) => c.stack.includes("EdhWorkstationsBuilder"))).toBe(true);
    });

    test("one GetParameters call reads every chunk in each shipped template", () => {
        for (const name of ALL_TEMPLATES) {
            expect(chunksOf(synthFor(name, false)).length).toBeLessThanOrEqual(10);
        }
        expect(chunksOf(synthFor("commercial", true)).length).toBeLessThanOrEqual(10);
    });
});

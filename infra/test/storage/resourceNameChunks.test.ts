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
 * every chunk, that each chunk carries the same value as its name's per-key parameter, that every
 * chunk is a resource of the root core stack (so a layout change updates a chunk in place and never
 * moves it between stacks, which CloudFormation would refuse with ParameterAlreadyExists), that the
 * root template takes no new parameter and stays within the template quotas, and the Lambda grant
 * on the chunk path.
 */

import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import { ADDON_RESOURCE_PARAM_KEYS } from "../../common/addonResourceParamKeys";
import { publishAddonResourceName } from "../../lib/nestedStacks/addon/addonStackHelpers";
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
    resolveReference,
    resourcesOf,
    SynthResult,
    synthTemplate,
    TemplateName,
    ALL_TEMPLATES,
} from "../support/templateSynth";
import { newTestApp } from "../support/testApp";
import {
    chunksOf,
    expectChunksInRootStack,
    expectRootTemplateWithinLimits,
    prefixOf,
    rawChunkValues,
    rootStackKey,
    SynthChunk,
    sourceOf,
} from "./support/resourceNameChunkSynth";

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
            { source: s, entries: core },
            { source: other, entries: lane },
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
        expect(plan.chunks.map((c) => c.source)).toEqual([s, s, s, other]);
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
        const a = planResourceNameChunks([{ source: s, entries: entries(3, (i) => `a${i}`) }]);
        const b = planResourceNameChunks([{ source: s, entries: entries(3, (i) => `b${i}`) }]);
        const c = planResourceNameChunks([{ source: s, entries: entries(4, (i) => `a${i}`) }]);
        expect(a.layoutId).toBe(b.layoutId);
        expect(c.layoutId).not.toBe(a.layoutId);
    });

    test("renders JSON a reader parses, escaping a literal", () => {
        const s = stack();
        const plan = planResourceNameChunks([
            {
                source: s,
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
            planResourceNameChunks([{ source: s, entries: [{ key: 'bad"key', value: "x" }] }])
        ).toThrow(/not a valid SSM parameter path/);
        expect(() =>
            planResourceNameChunks([
                { source: s, entries: [{ key: "integration/big", value: "x".repeat(4100) }] },
            ])
        ).toThrow(/no 4096-character chunk holds/);
        const many = Array.from({ length: RESOURCE_NAME_CHUNK_COUNT_LIMIT + 1 }, (_, i) => ({
            source: new cdk.Stack(newTestApp(), `Source${i}`),
            entries: [{ key: `dynamoTables/t${i}`, value: "x" }],
        }));
        expect(() => planResourceNameChunks(many)).toThrow(/exceed the 100/);
    });
});

describe("lane names reach the chunks, which all live in the root stack", () => {
    const config = { resourceNamesSSMParamPrefix: "/vams-test/resourceNames" } as any;

    /** A root stack with a core source, an optional add-on source and a custom source. */
    function app(withAddon: boolean) {
        const root = new cdk.Stack(newTestApp(), "Root");
        const registry = new ResourceNameRegistry();
        ResourceNameRegistry.attach(root, registry);
        const coreSource = new cdk.NestedStack(root, "ResourceNamesBuilder");
        registry.register({ paramKey: "dynamoTables/assetStorage", value: "assets" });
        if (withAddon) {
            const addon = new cdk.NestedStack(root, "AddonBuilder");
            const edh = new cdk.NestedStack(addon, "EdhWorkstationsBuilder");
            publishAddonResourceName(
                edh,
                config,
                "addon/dynamoTables/sessions",
                tokenValue(edh, "S")
            );
        }
        const custom = new cdk.NestedStack(root, "CustomBuilder");
        publishCustomResourceName(
            custom,
            config,
            "custom/dynamoTables/widgetStorage",
            tokenValue(custom, "Widgets")
        );
        const plan = buildResourceNameChunks({ config, registry, scope: root, coreSource });
        return { root, coreSource, custom, plan };
    }

    const chunkParameters = (stack: cdk.Stack) =>
        Object.entries<any>(Template.fromStack(stack).findResources("AWS::SSM::Parameter")).filter(
            ([, r]) => String(r.Properties.Name).startsWith("/vams-test/resourceNamesChunks/")
        );

    test("a custom name published in a nested stack lands in a root-stack chunk after the core", () => {
        const { root, coreSource, custom, plan } = app(false);

        expect(plan.count).toBe(2);
        expect(plan.chunks[1].source).toBe(custom);
        expect(chunkParameters(custom)).toEqual([]);
        expect(chunkParameters(coreSource)).toEqual([]);
        const inRoot = chunkParameters(root);
        expect(inRoot.map(([, r]) => r.Properties.Name).sort()).toEqual([
            "/vams-test/resourceNamesChunks/0",
            "/vams-test/resourceNamesChunks/1",
        ]);
        const [, chunk] = inRoot.find(([, r]) => r.Properties.Name.endsWith("/1")) as [string, any];
        expect(chunk.Properties.Tier).toBe("Standard");
        // The root reads the custom stack's value through that nested stack's output.
        const flat = SynthResult.flatten(chunk.Properties.Value);
        expect(flat).toMatch(
            new RegExp(
                `^\\{"v":1,"n":2,"i":1,"l":"${plan.layoutId}","m":\\{"custom/dynamoTables/widgetStorage":"\\$\\{CustomBuilderNestedStackCustomBuilderNestedStackResource[0-9A-F]{8}\\.Outputs\\.[A-Za-z0-9]+\\}"\\}\\}$`
            )
        );
    });

    test("a source added before the custom one renumbers chunks inside the root stack only", () => {
        const before = app(false);
        const after = app(true);
        expect(after.plan.count).toBe(3);
        // The custom chunk moves from index 1 to 2 and the add-on takes index 1; every index is
        // still a resource of the root stack with the same construct id, so CloudFormation updates
        // its value in place.
        const ids = (root: cdk.Stack) =>
            chunkParameters(root)
                .map(([logicalId, r]) => [String(r.Properties.Name).split("/").pop(), logicalId])
                .sort();
        const beforeIds = new Map(ids(before.root) as [string, string][]);
        for (const [index, logicalId] of ids(after.root) as [string, string][]) {
            if (beforeIds.has(index)) expect(logicalId).toBe(beforeIds.get(index));
        }
        expect([...beforeIds.keys()].sort()).toEqual(["0", "1"]);
    });

    test("refuses to build the chunks in a nested stack", () => {
        const root = new cdk.Stack(newTestApp(), "Root");
        const registry = new ResourceNameRegistry();
        registry.register({ paramKey: "dynamoTables/assetStorage", value: "assets" });
        const nested = new cdk.NestedStack(root, "ResourceNamesBuilder");
        expect(() =>
            buildResourceNameChunks({ config, registry, scope: nested, coreSource: nested })
        ).toThrow(/root core stack/);
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

/** The per-key parameters under the prefix, by key, with the stack and raw value of each. */
function perKeyParametersOf(s: SynthResult): Map<string, { stack: string; value: unknown }> {
    const prefix = `${prefixOf(s)}/`;
    const out = new Map<string, { stack: string; value: unknown }>();
    for (const r of s.ofType("AWS::SSM::Parameter")) {
        const name = SynthResult.flatten(r.properties.Name);
        if (name.startsWith(prefix)) {
            out.set(name.slice(prefix.length), { stack: r.stack, value: r.properties.Value });
        }
    }
    return out;
}

/** A value as a comparable target: the literal itself, or the resource a reference resolves to. */
function targetOf(s: SynthResult, stack: string, value: unknown): string {
    if (typeof value === "string") return `literal:${value}`;
    const resource = resolveReference(s, stack, value);
    return resource
        ? `${resource.stack}/${resource.logicalId}`
        : `unresolved:${JSON.stringify(value)}`;
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
        expect(chunks.length).toBeGreaterThan(0);
        for (const chunk of chunks) {
            expect(worstCaseOf(chunk)).toBeLessThanOrEqual(RESOURCE_NAME_CHUNK_MAX_CHARS);
        }
    });

    test("carry every registered and lane name exactly once, with its per-key parameter's value", () => {
        const perKey = perKeyParametersOf(s);
        const carried = chunks.flatMap((c) =>
            [...rawChunkValues(c)].map(([key, value]) => ({ key, value, chunk: c }))
        );
        const keys = carried.map((c) => c.key);
        expect(new Set(keys).size).toBe(keys.length);
        expect([...keys].sort()).toEqual(chunks.flatMap((c) => Object.keys(c.parsed.m)).sort());
        const expected = [...perKey.keys()].filter((k) => !isLateBound(k)).sort();
        expect([...keys].sort()).toEqual(expected);
        for (const { key, value, chunk } of carried) {
            // The chunk (root stack) and the per-key parameter (its nested stack) name the same
            // resource, or carry the same literal.
            const own = perKey.get(key)!;
            const target = targetOf(s, chunk.stack, value);
            expect({ key, target }).toEqual({ key, target: targetOf(s, own.stack, own.value) });
            expect(target).not.toMatch(/^unresolved:/);
        }
    });

    test("are resources of the root core stack, which takes no new parameter", () => {
        expect(chunks.every((c) => c.stack === rootStackKey(s))).toBe(true);
        expectChunksInRootStack(s);
        expectRootTemplateWithinLimits(s);
        // The root reads every nested value through a nested-stack output.
        expect(chunks.some((c) => c.refs.length > 0)).toBe(true);
        for (const chunk of chunks) {
            for (const ref of chunk.refs) expect(ref).toMatch(/\.Outputs\./);
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

describe("chunk layout per configuration (recorded: a change that adds names shows here)", () => {
    const layout = (s: SynthResult) => {
        const perSource: Record<string, number> = {};
        const stacks = new Set<string>();
        for (const chunk of chunksOf(s)) {
            perSource[sourceOf(chunk)] = (perSource[sourceOf(chunk)] ?? 0) + 1;
            stacks.add(chunk.stack === rootStackKey(s) ? "root" : chunk.stack);
        }
        const names = chunksOf(s).reduce((sum, c) => sum + Object.keys(c.parsed.m).length, 0);
        return { chunks: chunksOf(s).length, names, perSource, stacks: [...stacks] };
    };

    test("commercial template, add-ons off", () => {
        expect(layout(synthFor("commercial", false))).toEqual({
            chunks: 8,
            names: 76,
            perSource: { core: 8 },
            stacks: ["root"],
        });
    });

    test("commercial template, every add-on on", () => {
        const s = synthFor("commercial", true);
        expect(layout(s)).toEqual({
            chunks: 9,
            names: 78,
            perSource: { core: 8, addon: 1 },
            stacks: ["root"],
        });
        // The add-on's names are packed after the core's, in a chunk of their own.
        const edhKeys = Object.values(ADDON_RESOURCE_PARAM_KEYS.dynamoTables);
        const edhChunks = chunksOf(s).filter((c) => edhKeys.some((k) => k in c.parsed.m));
        expect(edhChunks.map((c) => c.index)).toEqual([8]);
    });

    test("one GetParameters call reads every chunk in each shipped template", () => {
        for (const name of ALL_TEMPLATES) {
            expect(chunksOf(synthFor(name, false)).length).toBeGreaterThan(0);
            expect(chunksOf(synthFor(name, false)).length).toBeLessThanOrEqual(10);
        }
        expect(chunksOf(synthFor("commercial", true)).length).toBeLessThanOrEqual(10);
    });
});

describe("turning the add-ons on keeps every chunk on the same root-stack resource", () => {
    test("each index has the same stack and logical id with add-ons off and on", () => {
        const off = expectChunksInRootStack(synthFor("commercial", false));
        const on = expectChunksInRootStack(synthFor("commercial", true));
        const idOf = (chunks: SynthChunk[]) =>
            new Map(
                chunks.map((c) => [
                    c.index,
                    `${c.stack}/${c.logicalId}`.replace(/^vams-t1-\w+/, "root"),
                ])
            );
        const offIds = idOf(off);
        expect(off.length).toBeGreaterThan(0);
        for (const [index, id] of idOf(on)) {
            if (offIds.has(index)) expect({ index, id }).toEqual({ index, id: offIds.get(index) });
        }
        // The core chunks keep their contents too: only the add-on's chunk is new.
        for (const chunk of off) {
            expect(Object.keys(on[chunk.index].parsed.m)).toEqual(Object.keys(chunk.parsed.m));
        }
    });
});

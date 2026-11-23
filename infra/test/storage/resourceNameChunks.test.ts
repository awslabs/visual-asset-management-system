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
    arnMaxLength,
    EVENT_BUS_ARN_MAX_LENGTH,
    HTTPS_HOST_URL_MAX_LENGTH,
    LOCATION_API_KEY_ARN_MAX_LENGTH,
    TOKEN_MAX_LENGTH_BY_CATEGORY,
    TOKEN_MAX_LENGTH_BY_KEY,
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
import {
    chunksOf,
    dependencyCycleIn,
    dependsOnOf,
    stacksReadByChunks,
    expectChunksInRootStack,
    expectRootTemplateWithinLimits,
    prefixOf,
    rawChunkValues,
    rootStackKey,
    SynthChunk,
    sourceOf,
    valueTargetOf,
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
        expect(worstCaseValueChars({ key: "integration/a", value: 'a"b' })).toBe(
            JSON.stringify('a"b').length
        );
        expect(worstCaseValueChars({ key: "lambdaFunctions/a", value: t })).toBe(64 + 2);
        expect(TOKEN_MAX_LENGTH_BY_CATEGORY).toEqual({
            dynamoTables: 255,
            s3Buckets: 63,
            cloudwatchLogGroups: 512,
            lambdaFunctions: 64,
        });
    });

    test("sizes the deploy-time URLs and the map key ARN by their own bounds", () => {
        const t = tokenValue(stack(), "Url");
        // https:// and a DNS name at its 253-character limit.
        expect(HTTPS_HOST_URL_MAX_LENGTH).toBe(261);
        // arn: + partition (16) + : + geo + : + Region (25) + : + account (12) + : + api-key/ + a
        // 100-character key name.
        expect(LOCATION_API_KEY_ARN_MAX_LENGTH).toBe(172);
        expect(arnMaxLength("geo", "api-key/", 100)).toBe(172);
        const longestKeyArn = `arn:aws-us-gov:geo:eusc-de-east-1:123456789012:api-key/${"k".repeat(
            100
        )}`;
        expect(longestKeyArn.length).toBeLessThanOrEqual(LOCATION_API_KEY_ARN_MAX_LENGTH);
        const longestUrl = `https://${"h".repeat(253)}`;
        expect(longestUrl.length).toBe(HTTPS_HOST_URL_MAX_LENGTH);
        for (const key of [
            RESOURCE_PARAM_KEYS.openSearch.endpoint,
            RESOURCE_PARAM_KEYS.web.deployedUrl,
        ]) {
            expect(worstCaseValueChars({ key, value: t })).toBe(261 + 2);
            // A provisioned domain's endpoint is "https://" joined with a deploy-time name.
            expect(worstCaseValueChars({ key, value: `https://${t}` })).toBe(261 + 2);
        }
        expect(worstCaseValueChars({ key: RESOURCE_PARAM_KEYS.location.apiKeyArn, value: t })).toBe(
            172 + 2
        );
        // The index names are literals, sized by their own length.
        expect(
            worstCaseValueChars({
                key: RESOURCE_PARAM_KEYS.openSearch.assetIndexName,
                value: "vams-assets-v3",
            })
        ).toBe('"vams-assets-v3"'.length);
    });

    test("sizes an ARN-valued key by its own bound, not the 2,048 of an unknown category", () => {
        const t = tokenValue(stack(), "Bus");
        const busKey = RESOURCE_PARAM_KEYS.integration.orchestrationBusArn;
        expect(TOKEN_MAX_LENGTH_BY_KEY).toEqual({
            [busKey]: EVENT_BUS_ARN_MAX_LENGTH,
            [RESOURCE_PARAM_KEYS.openSearch.endpoint]: HTTPS_HOST_URL_MAX_LENGTH,
            [RESOURCE_PARAM_KEYS.web.deployedUrl]: HTTPS_HOST_URL_MAX_LENGTH,
            [RESOURCE_PARAM_KEYS.location.apiKeyArn]: LOCATION_API_KEY_ARN_MAX_LENGTH,
        });
        expect(worstCaseValueChars({ key: busKey, value: t })).toBe(EVENT_BUS_ARN_MAX_LENGTH + 2);
        // arn: + partition (16) + : + events + : + Region (25) + : + account (12) + : + event-bus/ +
        // a 256-character name.
        expect(EVENT_BUS_ARN_MAX_LENGTH).toBe(333);
        expect(arnMaxLength("events", "event-bus/", 256)).toBe(333);
        // Holds the longest bus ARN any partition and Region in use today can produce.
        const longest = `arn:aws-us-gov:events:eusc-de-east-1:123456789012:event-bus/${"b".repeat(
            256
        )}`;
        expect(longest.length).toBeLessThanOrEqual(EVENT_BUS_ARN_MAX_LENGTH);
        // A literal is still sized by its own length.
        expect(worstCaseValueChars({ key: busKey, value: "arn:aws:events:x" })).toBe(18);
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

    test("a literal-valued lane chunk waits for the stacks the core chunks read", () => {
        // A chunk whose values are literals reads no stack; without the shared dependency it
        // would update at once while the core chunks wait for storage.
        const root = new cdk.Stack(newTestApp(), "Root");
        const registry = new ResourceNameRegistry();
        ResourceNameRegistry.attach(root, registry);
        const storage = new cdk.NestedStack(root, "StorageResourcesBuilder");
        const coreSource = new cdk.NestedStack(root, "ResourceNamesBuilder");
        registry.register({
            paramKey: "dynamoTables/assetStorage",
            value: tokenValue(storage, "A"),
        });
        const custom = new cdk.NestedStack(root, "CustomBuilder");
        publishCustomResourceName(custom, config, "custom/dynamoTables/widgetStorage", "widgets");

        const plan = buildResourceNameChunks({ config, registry, scope: root, coreSource });

        expect(plan.count).toBe(2);
        const dependsOn = Object.fromEntries(
            chunkParameters(root).map(([, r]) => [
                String(r.Properties.Name).split("/").pop(),
                ([] as string[]).concat(r.DependsOn ?? []),
            ])
        );
        expect(dependsOn["0"]).toHaveLength(1);
        expect(dependsOn["0"][0]).toMatch(/^StorageResourcesBuilderNestedStack/);
        expect(dependsOn["1"]).toEqual(dependsOn["0"]);
    });

    test("the cycle check finds a cycle through DependsOn and references, and none without", () => {
        const resource = (props: object, dependsOn?: string[]) => ({
            Type: "AWS::SSM::Parameter",
            Properties: props,
            ...(dependsOn ? { DependsOn: dependsOn } : {}),
        });
        expect(
            dependencyCycleIn({
                Resources: {
                    A: resource({}, ["B"]),
                    B: resource({ Value: { "Fn::GetAtt": ["A", "x"] } }),
                },
            }).sort()
        ).toEqual(["A", "B"]);
        expect(
            dependencyCycleIn({
                Resources: {
                    A: resource({}, ["B"]),
                    B: resource({ Value: { Ref: "C" } }),
                    C: resource({}),
                },
            })
        ).toEqual([]);
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

/** A value as a comparable target (literal parts and the resource attributes references name). */
const targetOf = valueTargetOf;

/**
 * Keys published per key but deliberately outside the registry, the late core publisher and the
 * lane helpers: tooling-only names no Lambda reads at runtime.
 */
const LATE_BOUND_KEYS = new Set<string>([
    RESOURCE_PARAM_KEYS.lambdaFunctions.crOsReindexer,
    RESOURCE_PARAM_KEYS.lambdaFunctions.vectorReindexer,
    RESOURCE_PARAM_KEYS.lambdaFunctions.importGlobalPipelineWorkflow,
    RESOURCE_PARAM_KEYS.integration.workflowExecutionRoleArn,
]);
const isLateBound = (key: string) => LATE_BOUND_KEYS.has(key) || key.startsWith("securityGroups/");

function worstCaseOf(chunk: SynthChunk): number {
    // Each token stands for its key's bound (exact key, else category), as the planner sized it.
    let chars = chunk.flat.length;
    const pattern = /"([^"]+)":"(\$\{[^}]+\})"/g;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(chunk.flat)) !== null) {
        const category = match[1]
            .split("/")
            .slice(0, -1)
            .find((segment) => segment in TOKEN_MAX_LENGTH_BY_CATEGORY);
        const bound =
            TOKEN_MAX_LENGTH_BY_KEY[match[1]] ??
            (category ? TOKEN_MAX_LENGTH_BY_CATEGORY[category] : UNKNOWN_TOKEN_MAX_LENGTH);
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

    test("size every deploy-time value by a bound of its own key or category", () => {
        // A value sized as an unknown category (2,048 characters) takes half a chunk alone; an
        // ARN-valued key needs its own bound in TOKEN_MAX_LENGTH_BY_KEY.
        const tokenKeys = chunks.flatMap((c) =>
            [...c.flat.matchAll(/"([^"]+)":"\$\{[^}]+\}"/g)].map((m) => m[1])
        );
        expect(tokenKeys.length).toBeGreaterThan(0);
        const unbounded = tokenKeys.filter(
            (key) =>
                !(key in TOKEN_MAX_LENGTH_BY_KEY) &&
                !key
                    .split("/")
                    .slice(0, -1)
                    .some((segment) => segment in TOKEN_MAX_LENGTH_BY_CATEGORY)
        );
        expect(unbounded).toEqual([]);
        expect(tokenKeys).toContain(RESOURCE_PARAM_KEYS.integration.orchestrationBusArn);
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

    test("all wait for the same nested stacks, every one any chunk reads, with no cycle", () => {
        // Same DependsOn on every chunk, so a layout change updates them together.
        const read = stacksReadByChunks(chunks);
        expect(read.length).toBeGreaterThan(0);
        for (const chunk of chunks) {
            expect({ index: chunk.index, dependsOn: dependsOnOf(chunk) }).toEqual({
                index: chunk.index,
                dependsOn: read,
            });
        }
        const root = s.templates[rootStackKey(s)];
        for (const id of read) expect(root.Resources[id].Type).toBe("AWS::CloudFormation::Stack");
        expect(read.some((id) => id.startsWith("StorageResourcesBuilder"))).toBe(true);
        // No dependency cycle in the root template, and no nested stack waits for a chunk.
        expect(dependencyCycleIn(root)).toEqual([]);
        const chunkIds = new Set(chunks.map((c) => c.logicalId));
        const waiting = Object.entries<any>(root.Resources)
            .filter(([, r]) => r.Type === "AWS::CloudFormation::Stack")
            .filter(([, r]) =>
                ([] as string[]).concat(r.DependsOn ?? []).some((d) => chunkIds.has(d))
            )
            .map(([id]) => id);
        expect(waiting).toEqual([]);
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

    const namesPerChunk = (s: SynthResult) =>
        chunksOf(s).map((c) => Object.keys(c.parsed.m).length);

    const unhashed = (ids: string[]) => ids.map((id) => id.replace(/[0-9A-F]{8}$/, "")).sort();
    const nestedStack = (id: string) => `${id}NestedStack${id}NestedStackResource`;

    // The late core names in publication order: ApiBuilder2 (the copy engine) is built before
    // SearchBuilder, StaticWeb and LocationService.
    const LATE_CORE_ORDER = [
        RESOURCE_PARAM_KEYS.lambdaFunctions.largeObjectCopyEngine,
        RESOURCE_PARAM_KEYS.openSearch.endpoint,
        RESOURCE_PARAM_KEYS.openSearch.assetIndexName,
        RESOURCE_PARAM_KEYS.openSearch.fileIndexName,
        RESOURCE_PARAM_KEYS.web.deployedUrl,
        RESOURCE_PARAM_KEYS.location.apiKeyArn,
    ];

    /** The core chunks hold the registry names, then the late core names, in that order. */
    function expectLateCoreNamesLast(s: SynthResult, late: string[]): void {
        const coreKeys = chunksOf(s)
            .filter((c) => sourceOf(c) === "core")
            .flatMap((c) => Object.keys(c.parsed.m));
        const lateCore = new Set<string>(LATE_CORE_ORDER);
        expect(coreKeys.slice(coreKeys.length - late.length)).toEqual(late);
        expect(coreKeys.filter((k) => lateCore.has(k))).toEqual(late);
    }

    test("commercial template, add-ons off", () => {
        const s = synthFor("commercial", false);
        expect(layout(s)).toEqual({
            chunks: 7,
            names: 82,
            perSource: { core: 7 },
            stacks: ["root"],
        });
        // The six late core names fill the last core chunk's spare room: no chunk is added.
        expect(namesPerChunk(s)).toEqual([13, 13, 13, 13, 10, 9, 11]);
        expectLateCoreNamesLast(s, LATE_CORE_ORDER);
        // Every chunk waits for storage and for each stack a late core name is read from. With
        // CloudFront's own domain the web URL is a deploy-time value, so StaticWeb is one of them.
        for (const chunk of chunksOf(s)) {
            expect(unhashed(dependsOnOf(chunk))).toEqual(
                [
                    "ApiBuilder2",
                    "LocationService",
                    "SearchBuilder",
                    "StaticWeb",
                    "StorageResourcesBuilder",
                ].map(nestedStack)
            );
        }
    });

    test("commercial template, every add-on on", () => {
        const s = synthFor("commercial", true);
        expect(layout(s)).toEqual({
            chunks: 8,
            names: 84,
            perSource: { core: 7, addon: 1 },
            stacks: ["root"],
        });
        // The add-on's names are packed after the core's, late core names included, in a chunk of
        // their own.
        const edhKeys = Object.values(ADDON_RESOURCE_PARAM_KEYS.dynamoTables);
        const edhChunks = chunksOf(s).filter((c) => edhKeys.some((k) => k in c.parsed.m));
        expect(edhChunks.map((c) => c.index)).toEqual([7]);
        expectLateCoreNamesLast(s, LATE_CORE_ORDER);
        // Every chunk, the core ones included, waits for the add-on stack as well.
        for (const chunk of chunksOf(s)) {
            expect(unhashed(dependsOnOf(chunk))).toEqual(
                [
                    "AddonBuilder",
                    "ApiBuilder2",
                    "LocationService",
                    "SearchBuilder",
                    "StaticWeb",
                    "StorageResourcesBuilder",
                ].map(nestedStack)
            );
        }
    });

    test.each([
        ["govcloud", [13, 13, 13, 13, 10, 9, 10]],
        ["eusovereign", [13, 13, 13, 13, 10, 10, 8]],
    ] as [TemplateName, number[]][])("%s template", (name, perChunk) => {
        const s = synthFor(name, false);
        expect(namesPerChunk(s)).toEqual(perChunk);
        // Provisioned OpenSearch, an ALB with a configured domain (a literal web URL, read from no
        // stack) and no Amazon Location Service.
        expectLateCoreNamesLast(
            s,
            LATE_CORE_ORDER.filter((k) => k !== RESOURCE_PARAM_KEYS.location.apiKeyArn)
        );
        for (const chunk of chunksOf(s)) {
            expect(unhashed(dependsOnOf(chunk))).toEqual(
                ["ApiBuilder2", "SearchBuilder", "StorageResourcesBuilder"].map(nestedStack)
            );
        }
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

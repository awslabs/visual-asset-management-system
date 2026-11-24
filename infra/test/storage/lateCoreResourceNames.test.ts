/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The core resource names stacks built after ResourceNamesBuilder publish
 * (lib/nestedStacks/resourceNames/lateCoreResourceNames.ts): the registry's refusals, the publisher
 * and the recorder, and how the chunk builder packs the names (after the registry's, in the core
 * chunks) and orders the chunks after the stacks that publish them. Full synths of the shipped
 * configurations are in resourceNameChunks.test.ts and resourceNameChunksDeployTimeValues.test.ts.
 */

import * as cdk from "aws-cdk-lib";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { Template } from "aws-cdk-lib/assertions";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import { publishCustomResourceName } from "../../lib/nestedStacks/custom/customStackHelpers";
import {
    publishLateCoreResourceName,
    recordLateCoreResourceName,
} from "../../lib/nestedStacks/resourceNames/lateCoreResourceNames";
import { ResourceNameRegistry } from "../../lib/nestedStacks/resourceNames/resourceNameRegistry";
import {
    buildResourceNameChunks,
    planResourceNameChunks,
    RESOURCE_NAME_CHUNK_COUNT_LIMIT,
} from "../../lib/nestedStacks/resourceNames/resourceNameChunks";
import { SynthResult } from "../support/templateSynth";
import { newTestApp } from "../support/testApp";

const config = { resourceNamesSSMParamPrefix: "/vams-test/resourceNames" } as any;
const tokenValue = (stack: cdk.Stack, id: string): string =>
    new cdk.CfnResource(stack, id, { type: "AWS::DynamoDB::Table" }).ref;

describe("ResourceNameRegistry.recordLateCore", () => {
    const owner = () => new cdk.Stack(newTestApp(), "Owner");

    test("records core keys in publication order and returns a copy", () => {
        const r = new ResourceNameRegistry();
        const stack = owner();
        r.recordLateCore({
            paramKey: RESOURCE_PARAM_KEYS.web.deployedUrl,
            value: "u",
            owner: stack,
        });
        r.recordLateCore({
            paramKey: RESOURCE_PARAM_KEYS.openSearch.endpoint,
            value: "e",
            owner: stack,
        });
        expect(r.lateCoreEntries().map((e) => e.paramKey)).toEqual([
            "web/deployedUrl",
            "openSearch/endpoint",
        ]);
        r.lateCoreEntries().pop();
        expect(r.lateCoreEntries()).toHaveLength(2);
        // Late core names are not registry descriptors: ResourceNamesBuilder does not publish them.
        expect(r.list()).toEqual([]);
    });

    test("refuses a key that is not a core key, even a well-formed one", () => {
        const r = new ResourceNameRegistry();
        expect(() =>
            r.recordLateCore({ paramKey: "custom/dynamoTables/x", value: "v", owner: owner() })
        ).toThrow(/not a core resource-name key/);
        expect(() =>
            r.recordLateCore({ paramKey: "openSearch/somethingElse", value: "v", owner: owner() })
        ).toThrow(/not a core resource-name key/);
    });

    test("refuses a malformed key and an empty value", () => {
        const r = new ResourceNameRegistry();
        expect(() =>
            r.recordLateCore({ paramKey: "open-search/endpoint", value: "v", owner: owner() })
        ).toThrow(/invalid paramKey/);
        expect(() =>
            r.recordLateCore({
                paramKey: RESOURCE_PARAM_KEYS.location.apiKeyArn,
                value: "",
                owner: owner(),
            })
        ).toThrow(/empty value/);
    });

    test("refuses a key register() took and a key recorded twice", () => {
        const r = new ResourceNameRegistry();
        r.register({ paramKey: RESOURCE_PARAM_KEYS.dynamoTables.assetStorage, value: "t" });
        expect(() =>
            r.recordLateCore({
                paramKey: RESOURCE_PARAM_KEYS.dynamoTables.assetStorage,
                value: "t",
                owner: owner(),
            })
        ).toThrow(/registered with register\(\)/);
        r.recordLateCore({
            paramKey: RESOURCE_PARAM_KEYS.web.deployedUrl,
            value: "u",
            owner: owner(),
        });
        expect(() =>
            r.recordLateCore({
                paramKey: RESOURCE_PARAM_KEYS.web.deployedUrl,
                value: "u2",
                owner: owner(),
            })
        ).toThrow(/published twice/);
    });

    test("register() refuses a key a later stack already recorded", () => {
        const r = new ResourceNameRegistry();
        r.recordLateCore({
            paramKey: RESOURCE_PARAM_KEYS.openSearch.endpoint,
            value: "e",
            owner: owner(),
        });
        expect(() =>
            r.register({ paramKey: RESOURCE_PARAM_KEYS.openSearch.endpoint, value: "e" })
        ).toThrow(/already published by a later stack/);
    });
});

describe("publishLateCoreResourceName and recordLateCoreResourceName", () => {
    function core() {
        const root = new cdk.Stack(newTestApp(), "Root");
        const registry = new ResourceNameRegistry();
        ResourceNameRegistry.attach(root, registry);
        return { root, registry };
    }

    test("creates the per-key parameter in the publishing stack, after its dependency, and records it", () => {
        const { root, registry } = core();
        const search = new cdk.NestedStack(root, "SearchBuilder");
        const schemaDeploy = new cdk.CfnResource(search, "SchemaDeploy", {
            type: "AWS::CloudFormation::CustomResource",
        });
        const endpoint = tokenValue(search, "Collection");
        publishLateCoreResourceName(
            search,
            config,
            RESOURCE_PARAM_KEYS.openSearch.endpoint,
            endpoint,
            {
                dependsOn: [schemaDeploy],
            }
        );

        const params = Template.fromStack(search).findResources("AWS::SSM::Parameter");
        const entries = Object.entries<any>(params);
        expect(entries).toHaveLength(1);
        const [logicalId, param] = entries[0];
        expect(logicalId).toMatch(/^LateCoreResourceNameopenSearchendpoint[0-9A-F]{8}$/);
        expect(param.Properties.Name).toBe("/vams-test/resourceNames/openSearch/endpoint");
        expect(param.Properties.Type).toBe("String");
        expect(param.Properties.Tier).toBe("Standard");
        expect(param.DependsOn).toEqual([expect.stringMatching(/^SchemaDeploy/)]);
        expect(registry.lateCoreEntries()).toEqual([
            { paramKey: "openSearch/endpoint", value: endpoint, owner: search },
        ]);
    });

    test("refuses an add-on or custom key, which its lane publishes", () => {
        const { root } = core();
        expect(() =>
            publishLateCoreResourceName(root, config, "addon/dynamoTables/sessions", "x")
        ).toThrow(/not a core resource-name key/);
        expect(() =>
            publishLateCoreResourceName(root, config, "custom/dynamoTables/widgets", "x")
        ).toThrow(/not a core resource-name key/);
    });

    test("records an existing parameter without creating a second one", () => {
        const { root, registry } = core();
        const api2 = new cdk.NestedStack(root, "ApiBuilder2");
        const name = tokenValue(api2, "Engine");
        const parameter = new ssm.StringParameter(
            api2,
            "LargeObjectCopyEngineFunctionNameParameter",
            {
                parameterName: "/vams-test/resourceNames/lambdaFunctions/largeObjectCopyEngine",
                stringValue: name,
            }
        );
        recordLateCoreResourceName(
            parameter,
            RESOURCE_PARAM_KEYS.lambdaFunctions.largeObjectCopyEngine,
            name
        );
        expect(Object.keys(Template.fromStack(api2).findResources("AWS::SSM::Parameter"))).toEqual([
            expect.stringMatching(/^LargeObjectCopyEngineFunctionNameParameter[0-9A-F]{8}$/),
        ]);
        expect(registry.lateCoreEntries()).toEqual([
            { paramKey: "lambdaFunctions/largeObjectCopyEngine", value: name, owner: api2 },
        ]);
        expect(() => recordLateCoreResourceName(parameter, "custom/dynamoTables/x", name)).toThrow(
            /not a core resource-name key/
        );
    });

    test("a stack outside a VAMS core stack publishes per key only", () => {
        const bare = new cdk.Stack(newTestApp(), "Bare");
        expect(() =>
            publishLateCoreResourceName(
                bare,
                config,
                RESOURCE_PARAM_KEYS.web.deployedUrl,
                "https://x"
            )
        ).not.toThrow();
        expect(ResourceNameRegistry.of(bare)).toBeUndefined();
    });

    test("a key published twice fails at synth", () => {
        const { root } = core();
        const a = new cdk.NestedStack(root, "A");
        const b = new cdk.NestedStack(root, "B");
        publishLateCoreResourceName(a, config, RESOURCE_PARAM_KEYS.web.deployedUrl, "https://a");
        expect(() =>
            publishLateCoreResourceName(b, config, RESOURCE_PARAM_KEYS.web.deployedUrl, "https://b")
        ).toThrow(/published twice/);
    });
});

describe("the chunk builder packs late core names with the core names", () => {
    const chunkParameters = (stack: cdk.Stack) =>
        Object.entries<any>(Template.fromStack(stack).findResources("AWS::SSM::Parameter")).filter(
            ([, r]) => String(r.Properties.Name).startsWith("/vams-test/resourceNamesChunks/")
        );

    function app(opts: { late: boolean; custom: boolean }) {
        const root = new cdk.Stack(newTestApp(), "Root");
        const registry = new ResourceNameRegistry();
        ResourceNameRegistry.attach(root, registry);
        const storage = new cdk.NestedStack(root, "StorageResourcesBuilder");
        const coreSource = new cdk.NestedStack(root, "ResourceNamesBuilder");
        registry.register({
            paramKey: RESOURCE_PARAM_KEYS.dynamoTables.assetStorage,
            value: tokenValue(storage, "Assets"),
        });
        if (opts.late) {
            const search = new cdk.NestedStack(root, "SearchBuilder");
            publishLateCoreResourceName(
                search,
                config,
                RESOURCE_PARAM_KEYS.openSearch.endpoint,
                `https://${tokenValue(search, "Domain")}`
            );
            publishLateCoreResourceName(
                search,
                config,
                RESOURCE_PARAM_KEYS.openSearch.assetIndexName,
                "vams-assets-v3"
            );
            // A literal web URL (an ALB or a CloudFront custom domain) reads no stack.
            const web = new cdk.NestedStack(root, "StaticWeb");
            publishLateCoreResourceName(
                web,
                config,
                RESOURCE_PARAM_KEYS.web.deployedUrl,
                "https://vams.example.com"
            );
        }
        if (opts.custom) {
            const custom = new cdk.NestedStack(root, "CustomBuilder");
            publishCustomResourceName(
                custom,
                config,
                "custom/dynamoTables/widgets",
                tokenValue(custom, "Widgets")
            );
        }
        const plan = buildResourceNameChunks({ config, registry, scope: root, coreSource });
        return { root, plan };
    }

    test("after the registry names, in the core chunk, with no chunk added", () => {
        const without = app({ late: false, custom: true });
        const withLate = app({ late: true, custom: true });
        expect(withLate.plan.count).toBe(without.plan.count);
        expect(withLate.plan.chunks.map((c) => c.entries.map((e) => e.key))).toEqual([
            [
                "dynamoTables/assetStorage",
                "openSearch/endpoint",
                "openSearch/assetIndexName",
                "web/deployedUrl",
            ],
            ["custom/dynamoTables/widgets"],
        ]);
        // The lane chunk keeps its contents; only the core chunk gained names.
        expect(withLate.plan.chunks[1].entries.map((e) => e.key)).toEqual(
            without.plan.chunks[1].entries.map((e) => e.key)
        );
    });

    test("every chunk waits for the stacks a late core value is read from, and no other", () => {
        const { root } = app({ late: true, custom: true });
        const dependsOn = chunkParameters(root).map(([, r]) =>
            ([] as string[])
                .concat(r.DependsOn ?? [])
                .map((id) => id.replace(/[0-9A-F]{8}$/, ""))
                .sort()
        );
        expect(dependsOn).toHaveLength(2);
        // SearchBuilder for the endpoint's deploy-time part; not StaticWeb, whose URL is a literal.
        for (const ids of dependsOn) {
            expect(ids).toEqual([
                "CustomBuilderNestedStackCustomBuilderNestedStackResource",
                "SearchBuilderNestedStackSearchBuilderNestedStackResource",
                "StorageResourcesBuilderNestedStackStorageResourcesBuilderNestedStackResource",
            ]);
        }
        // The endpoint joins the literal scheme with the search stack's output.
        const [, coreChunk] = chunkParameters(root).find(([, r]) =>
            String(r.Properties.Name).endsWith("/0")
        ) as [string, any];
        expect(SynthResult.flatten(coreChunk.Properties.Value)).toMatch(
            /"openSearch\/endpoint":"https:\/\/\$\{SearchBuilderNestedStack[^.]+\.Outputs\.[A-Za-z0-9]+\}"/
        );
        expect(SynthResult.flatten(coreChunk.Properties.Value)).toContain(
            '"web/deployedUrl":"https://vams.example.com"'
        );
    });
});

describe("growth past ten chunks", () => {
    test("plans an eleventh chunk, which a reader fetches in a second GetParameters call", () => {
        // A fork with many custom names, each source in a chunk of its own: 11 chunks.
        const groups = Array.from({ length: 11 }, (_, i) => ({
            source: new cdk.Stack(newTestApp(), `Source${i}`),
            entries: [{ key: `custom/dynamoTables/t${i}`, value: `table-${i}` }],
        }));
        const plan = planResourceNameChunks(groups);
        expect(plan.count).toBe(11);
        expect(plan.chunks.map((c) => c.index)).toEqual(Array.from({ length: 11 }, (_, i) => i));
        // backend CHUNK_BATCH_SIZE: GetParameters takes at most 10 names.
        expect(Math.ceil(plan.count / 10)).toBe(2);
        expect(plan.count).toBeLessThanOrEqual(RESOURCE_NAME_CHUNK_COUNT_LIMIT);
    });
});

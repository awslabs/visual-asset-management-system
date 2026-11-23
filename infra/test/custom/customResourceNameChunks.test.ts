/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A fork's resource name reaches the resource-name chunk parameters with no change on its side.
 *
 * `customResources.ts` ships empty, so this file stands in for a fork: its `buildCustomResources()`
 * creates one table-like resource in the custom nested stack and publishes its name with
 * `publishCustomResourceName()`, exactly as `customizing-vams.md` describes. The full app is then
 * synthesized from the commercial template and the chunk carrying the name is looked for: it is a
 * resource of the root core stack like every chunk, numbered after the core chunks. The mock
 * changes what the synth emits, so this file never shares the per-run synth cache.
 */

import { resolveReference, SynthResult, synthTemplate } from "../support/templateSynth";
import {
    dependencyCycleIn,
    dependsOnOf,
    stacksReadByChunks,
    expectChunksInRootStack,
    expectRootTemplateWithinLimits,
    rawChunkValues,
    rootStackKey,
} from "../storage/support/resourceNameChunkSynth";

const FORK_KEY = "custom/dynamoTables/widgetStorage";

jest.mock("../../lib/nestedStacks/custom/customResources", () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const cdk = require("aws-cdk-lib");
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const helpers = jest.requireActual("../../lib/nestedStacks/custom/customStackHelpers");
    return {
        buildCustomResources: (scope: any, props: any) => {
            const table = new cdk.CfnResource(scope, "ForkWidgetTable", {
                type: "AWS::DynamoDB::Table",
                properties: {},
            });
            helpers.publishCustomResourceName(
                scope,
                props.config,
                "custom/dynamoTables/widgetStorage",
                table.ref
            );
            return { enabledFeatures: [] };
        },
    };
});

jest.setTimeout(900_000);

describe("a fork's custom resource name", () => {
    let s: SynthResult;

    beforeAll(() => {
        s = synthTemplate("commercial", { sharedCache: false });
    });

    const chunkPrefix = "/vams-t1-commercial/resourceNamesChunks/";
    test("is published per key in the custom stack, as before", () => {
        const perKey = s
            .ofType("AWS::SSM::Parameter")
            .filter(
                (r) =>
                    SynthResult.flatten(r.properties.Name) ===
                    `/vams-t1-commercial/resourceNames/${FORK_KEY}`
            );
        expect(perKey).toHaveLength(1);
        expect(perKey[0].stack).toMatch(/CustomBuilder/);
    });

    test("lands in a root-stack chunk numbered after the core chunks, read from the custom stack", () => {
        const all = expectChunksInRootStack(s);
        const carrying = all.filter((c) => FORK_KEY in c.parsed.m);
        expect(carrying).toHaveLength(1);
        const chunk = carrying[0];
        expect(chunk.stack).toBe(rootStackKey(s));
        expect(chunk.parsed.n).toBe(all.length);
        expect(chunk.index).toBe(all.length - 1);
        expect(SynthResult.flatten(chunk.resource.properties.Name)).toBe(
            `${chunkPrefix}${chunk.index}`
        );
        // The root reads the fork's value through the custom stack's output, and it names the fork's
        // own resource.
        const value = rawChunkValues(chunk).get(FORK_KEY);
        expect(JSON.stringify(value)).toMatch(/CustomBuilder.*Outputs\./);
        const target = resolveReference(s, chunk.stack, value);
        expect(target?.logicalId).toMatch(/^ForkWidgetTable/);
        expect(target?.stack).toMatch(/CustomBuilder/);
        // The core chunks before it hold only core names.
        const others = all.filter((c) => c !== chunk);
        expect(others.length).toBeGreaterThan(0);
        expect(
            others.every((c) => Object.keys(c.parsed.m).every((k) => !k.startsWith("custom/")))
        ).toBe(true);
    });

    test("the root template takes no new parameter and stays within the template quotas", () => {
        expectRootTemplateWithinLimits(s);
    });

    test("every chunk, the core ones included, waits for the custom stack and storage", () => {
        const all = expectChunksInRootStack(s);
        const unhashed = (ids: string[]) => ids.map((id) => id.replace(/[0-9A-F]{8}$/, ""));
        for (const chunk of all) {
            expect(unhashed(dependsOnOf(chunk))).toEqual([
                "CustomBuilderNestedStackCustomBuilderNestedStackResource",
                "StorageResourcesBuilderNestedStackStorageResourcesBuilderNestedStackResource",
            ]);
            expect(dependsOnOf(chunk)).toEqual(stacksReadByChunks(all));
        }
        expect(dependencyCycleIn(s.templates[rootStackKey(s)])).toEqual([]);
    });
});

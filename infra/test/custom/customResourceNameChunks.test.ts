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
 * synthesized from the commercial template and the chunk carrying the name is looked for. The mock
 * changes what the synth emits, so this file never shares the per-run synth cache.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

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
    const chunks = () =>
        s
            .ofType("AWS::SSM::Parameter")
            .filter((r) => SynthResult.flatten(r.properties.Name).startsWith(chunkPrefix));

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

    test("lands in a chunk of the custom stack, numbered after the core chunks", () => {
        const all = chunks();
        const carrying = all.filter((r) =>
            SynthResult.flatten(r.properties.Value).includes(`"${FORK_KEY}":"`)
        );
        expect(carrying).toHaveLength(1);
        expect(carrying[0].stack).toMatch(/CustomBuilder/);

        const flat = SynthResult.flatten(carrying[0].properties.Value);
        expect(flat).toContain(`"${FORK_KEY}":"\${ForkWidgetTable}"`);
        const header = JSON.parse(flat.replace(/\$\{[^}]+\}/g, "token"));
        expect(header.n).toBe(all.length);
        expect(header.i).toBe(all.length - 1);
        expect(SynthResult.flatten(carrying[0].properties.Name)).toBe(
            `${chunkPrefix}${all.length - 1}`
        );
        // The core chunks before it are in the ResourceNames stack.
        const others = all.filter((r) => r !== carrying[0]);
        expect(others.length).toBeGreaterThan(0);
        expect(others.every((r) => /ResourceNamesBuilder/.test(r.stack))).toBe(true);
    });
});

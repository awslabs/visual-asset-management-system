/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every deploy-time seed `AwsCustomResource` (a `Custom::AWS` resource whose SDK call writes to DynamoDB:
 * the auth defaults, the system and admin users, the metadata schema defaults and the feature-switch
 * rows) carries a `ServiceTimeout`. Without one, AWS CloudFormation waits its one-hour default for a
 * response it lost before failing the resource and rolling the stack back.
 *
 * The timeout must also exceed the provider function's own timeout, or CloudFormation gives up on a call
 * that would still have answered.
 */

import {
    ALL_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    synthTemplate,
} from "../support/templateSynth";
import { SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT } from "../../lib/helper/seedCustomResource";

// Three full-app synths, ~20-30 s each.
jest.setTimeout(600_000);

const synthed: Partial<Record<TemplateName, SynthResult>> = {};
const synth = (name: TemplateName): SynthResult => (synthed[name] ??= synthTemplate(name));

beforeAll(() => {
    ALL_TEMPLATES.forEach(synth);
});

function sdkCalls(r: Resource): string {
    return SynthResult.flatten(r.properties.Create) + SynthResult.flatten(r.properties.Update);
}

function seedResources(s: SynthResult): Resource[] {
    return s.where("Custom::AWS", (r) => /"service":"DynamoDB"/.test(sdkCalls(r)));
}

/** The singleton provider function a custom resource's `ServiceToken` points at, in its own stack. */
function providerFunction(s: SynthResult, r: Resource): Resource | undefined {
    const getAtt = r.properties.ServiceToken?.["Fn::GetAtt"];
    if (!Array.isArray(getAtt)) return undefined;
    return s.resources.find(
        (f) =>
            f.stack === r.stack && f.logicalId === getAtt[0] && f.type === "AWS::Lambda::Function"
    );
}

describe("deploy-time seed custom resources fail fast when a response is lost", () => {
    test.each(ALL_TEMPLATES)("%s emits the seed custom resources (control)", (name) => {
        const seeds = seedResources(synth(name));
        const calls = seeds.map(sdkCalls).join("\n");
        expect(seeds.length).toBeGreaterThan(10);
        expect(calls).toContain("initial_admin_role_creation");
        expect(calls).toContain("_system_initialization");
        expect(calls).toContain("featureName");
    });

    test.each(ALL_TEMPLATES)(
        "%s gives every seed custom resource the shared ServiceTimeout",
        (name) => {
            const expected = SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT.toSeconds().toString();
            const missing = seedResources(synth(name))
                .filter((r) => r.properties.ServiceTimeout !== expected)
                .map((r) => `${r.stack}/${r.logicalId}: ${r.properties.ServiceTimeout}`);
            expect(missing).toEqual([]);
        }
    );

    test.each(ALL_TEMPLATES)(
        "%s sets each seed ServiceTimeout above its provider function's timeout",
        (name) => {
            const s = synth(name);
            const seeds = seedResources(s);
            const tooShort = seeds
                .map((r) => ({ r, fn: providerFunction(s, r) }))
                .filter(
                    ({ r, fn }) =>
                        !fn ||
                        !(Number(r.properties.ServiceTimeout) > Number(fn.properties.Timeout))
                )
                .map(({ r, fn }) => `${r.stack}/${r.logicalId} vs ${fn?.properties.Timeout}`);
            expect(seeds.length).toBeGreaterThan(0);
            expect(tooShort).toEqual([]);
        }
    );
});

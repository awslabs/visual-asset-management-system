/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The static web nested stack reads the REST API endpoint and the auth resources, so it declares
 * explicit dependencies on the RestApi and AuthBuilder nested stacks (root CLAUDE.md Rule 9) as well
 * as on storage. Checked on the synthesized root template's DependsOn.
 */

import { synthTemplate } from "../support/templateSynth";

describe("StaticWeb nested stack dependencies", () => {
    const nested = synthTemplate("commercial").ofType("AWS::CloudFormation::Stack");
    const stack = (prefix: RegExp) => nested.find((s) => prefix.test(s.logicalId));

    test("the nested stacks under test are emitted (control)", () => {
        ["StaticWeb", "RestApi", "AuthBuilder", "StorageResourcesBuilder"].forEach((name) => {
            expect(stack(new RegExp(`^${name}`))).toBeDefined();
        });
    });

    test.each(["RestApi", "AuthBuilder", "StorageResourcesBuilder"])(
        "StaticWeb depends on %s",
        (name) => {
            const dependsOn: string[] = stack(/^StaticWeb/)?.raw.DependsOn ?? [];
            expect(dependsOn).toContain(stack(new RegExp(`^${name}`))?.logicalId);
        }
    );
});

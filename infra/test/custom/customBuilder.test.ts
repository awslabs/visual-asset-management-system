/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The fork extension point's CDK seam: the custom nested stack and its helpers.
 *
 * `lib/nestedStacks/custom/customResources.ts` ships empty and is filled in by a fork, so nothing here
 * asserts what the custom stack holds beyond its placeholder. What holds for any fork content is
 * asserted: the stack is built, it is valid while empty, it deploys after the storage and
 * resource-name stacks it reads from, and the REST API (which renders the shared route registry,
 * custom routes included) deploys after it. The two helpers a fork calls are unit-tested here too.
 */

import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import {
    loadCustomConfig,
    publishCustomResourceName,
} from "../../lib/nestedStacks/custom/customStackHelpers";
import { SynthResult, synthTemplate } from "../support/templateSynth";
import { newTestApp } from "../support/testApp";

jest.setTimeout(600_000);

/** The root-template logical id of the nested stack resource whose id starts with `prefix`. */
function nestedStackResourceId(synth: SynthResult, prefix: string): string {
    const found = synth
        .ofType("AWS::CloudFormation::Stack")
        .filter((r) => r.logicalId.startsWith(`${prefix}NestedStack`));
    expect(found).toHaveLength(1);
    return found[0].logicalId;
}

function dependsOn(synth: SynthResult, logicalId: string): string[] {
    const resource = synth
        .ofType("AWS::CloudFormation::Stack")
        .find((r) => r.logicalId === logicalId);
    return (resource?.raw.DependsOn as string[] | undefined) ?? [];
}

describe("custom nested stack", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial");
    });

    test("is built and carries its permissionless placeholder", () => {
        const id = nestedStackResourceId(synth, "CustomBuilder");
        expect(id).toBeTruthy();
        const placeholders = synth
            .ofType("AWS::CloudFormation::WaitConditionHandle")
            .filter((r) => r.logicalId.startsWith("CustomBuilderPlaceholder"));
        expect(placeholders).toHaveLength(1);
        expect(placeholders[0].stack).toMatch(/CustomBuilder/);
    });

    test("deploys after the storage and resource-name stacks", () => {
        const deps = dependsOn(synth, nestedStackResourceId(synth, "CustomBuilder"));
        expect(deps).toContain(nestedStackResourceId(synth, "StorageResourcesBuilder"));
        expect(deps).toContain(nestedStackResourceId(synth, "ResourceNamesBuilder"));
    });

    test("the REST API deploys after it", () => {
        // The API renders the shared route registry, so custom routes are only complete once the
        // custom stack has been built.
        const deps = dependsOn(synth, nestedStackResourceId(synth, "RestApi"));
        expect(deps).toContain(nestedStackResourceId(synth, "CustomBuilder"));
        // Control: the same lookup finds the add-on stack the API already depended on.
        expect(deps).toContain(nestedStackResourceId(synth, "AddonBuilder"));
    });
});

describe("publishCustomResourceName", () => {
    const config = { resourceNamesSSMParamPrefix: "/vams-test/resourceNames" } as any;

    const stackFor = () => new cdk.Stack(newTestApp(), "CustomNameStack");

    test("publishes the value under the deployment's resource-name prefix", () => {
        const stack = stackFor();
        publishCustomResourceName(stack, config, "custom/dynamoTables/widgetStorage", "widgets");
        Template.fromStack(stack).hasResourceProperties("AWS::SSM::Parameter", {
            Name: "/vams-test/resourceNames/custom/dynamoTables/widgetStorage",
            Value: "widgets",
        });
    });

    test("accepts a key outside custom/ (the prefix is a recommendation, not a rule)", () => {
        const stack = stackFor();
        expect(() =>
            publishCustomResourceName(stack, config, "dynamoTables/widgetStorage", "widgets")
        ).not.toThrow();
    });

    test("rejects a key equal to a core resource-name key", () => {
        const stack = stackFor();
        expect(() =>
            publishCustomResourceName(
                stack,
                config,
                RESOURCE_PARAM_KEYS.dynamoTables.assetStorage,
                "shadow"
            )
        ).toThrow(/core VAMS resource-name key/);
        expect(() =>
            publishCustomResourceName(
                stack,
                config,
                RESOURCE_PARAM_KEYS.integration.orchestrationBusArn,
                "shadow"
            )
        ).toThrow(/core VAMS resource-name key/);
    });

    test("rejects an empty key", () => {
        expect(() => publishCustomResourceName(stackFor(), config, "", "x")).toThrow(/non-empty/);
    });
});

describe("loadCustomConfig", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-custom-config-"));
    afterAll(() => fs.rmSync(dir, { recursive: true, force: true }));

    const write = (name: string, body: string): string => {
        const file = path.join(dir, name);
        fs.writeFileSync(file, body);
        return file;
    };

    test("reads the shipped file as a JSON object", () => {
        const shipped = loadCustomConfig();
        expect(typeof shipped).toBe("object");
        expect(Array.isArray(shipped)).toBe(false);
    });

    test("returns an empty object when the file is absent", () => {
        expect(loadCustomConfig(path.join(dir, "missing.json"))).toEqual({});
    });

    test("returns the parsed object", () => {
        expect(loadCustomConfig(write("ok.json", '{"widgets": {"enabled": true}}'))).toEqual({
            widgets: { enabled: true },
        });
    });

    test("fails synth on invalid JSON rather than deploying without the fork's settings", () => {
        expect(() => loadCustomConfig(write("bad.json", "{ not json"))).toThrow(/not valid JSON/);
    });

    test("fails synth on a JSON value that is not an object", () => {
        expect(() => loadCustomConfig(write("array.json", "[]"))).toThrow(/JSON object/);
    });
});

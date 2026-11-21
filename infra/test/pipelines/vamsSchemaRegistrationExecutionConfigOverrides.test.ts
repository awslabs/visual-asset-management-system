/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `executionConfigOverrides` lets a deployment register an existing vamsSchema bundle with a different
 * execution config (callback mode, timeouts, Lambda name) without a second bundle.
 *
 *   1. Absent (or all-undefined) emits no property and leaves schemaHash exactly as it was, so no
 *      deployed registration re-runs because of this property.
 *   2. Set, it reaches the custom resource as one JSON property.
 *   3. Changing a value changes schemaHash, so CloudFormation re-invokes the import lambda.
 */

import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import {
    ExecutionConfigOverrides,
    VamsSchemaRegistration,
} from "../../lib/nestedStacks/pipelines/constructs/vamsSchemaRegistration-construct";
import { newTestApp } from "../support/testApp";

function writeBundle(): string {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vamsSchemaExecOverrides-"));
    fs.writeFileSync(
        path.join(dir, "pipeline.json"),
        JSON.stringify({
            pipelineId: "p",
            pipelineName: "P",
            executionConfig: { executionType: "Lambda", waitForCallback: "Disabled" },
        })
    );
    return dir;
}

interface Registration {
    schemaHash: string;
    executionConfigOverrides?: string;
    propertyNames: string[];
}

function synthRegistration(
    bundleDir: string,
    executionConfigOverrides?: ExecutionConfigOverrides
): Registration {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "S", {
        env: { account: "111122223333", region: "us-west-2" },
    });
    new VamsSchemaRegistration(stack, "Reg", {
        importFunctionName: "importer",
        artefactsBucket: s3.Bucket.fromBucketName(stack, "Artefacts", "artefacts-bucket"),
        vamsSchemaDir: bundleDir,
        executionConfigOverrides,
    });
    const resources = app.synth().getStackByName("S").template.Resources as {
        [key: string]: { Properties?: { [key: string]: unknown } };
    };
    const found = Object.values(resources).filter((r) => r.Properties?.schemaHash !== undefined);
    expect(found).toHaveLength(1);
    const props = found[0].Properties as { [key: string]: unknown };
    return {
        schemaHash: props.schemaHash as string,
        executionConfigOverrides: props.executionConfigOverrides as string | undefined,
        propertyNames: Object.keys(props),
    };
}

describe("VamsSchemaRegistration executionConfigOverrides", () => {
    let bundleDir: string;

    beforeAll(() => {
        bundleDir = writeBundle();
    });

    afterAll(() => {
        fs.rmSync(bundleDir, { recursive: true, force: true });
    });

    test("absent emits no property", () => {
        const reg = synthRegistration(bundleDir);
        expect(reg.executionConfigOverrides).toBeUndefined();
        expect(reg.propertyNames).not.toContain("executionConfigOverrides");
    });

    test("an empty or all-undefined map equals absent, property and hash", () => {
        const absent = synthRegistration(bundleDir);
        for (const overrides of [{}, { waitForCallback: undefined, taskTimeout: undefined }]) {
            const reg = synthRegistration(bundleDir, overrides);
            expect(reg.propertyNames).toEqual(absent.propertyNames);
            expect(reg.schemaHash).toEqual(absent.schemaHash);
        }
    });

    test("set values reach the custom resource as one JSON property", () => {
        const reg = synthRegistration(bundleDir, {
            waitForCallback: "Enabled",
            taskTimeout: 14400,
            lambdaName: "my-fn",
        });
        expect(JSON.parse(reg.executionConfigOverrides as string)).toEqual({
            waitForCallback: "Enabled",
            taskTimeout: "14400",
            lambdaName: "my-fn",
        });
    });

    test("setting overrides changes the hash", () => {
        const absent = synthRegistration(bundleDir);
        const enabled = synthRegistration(bundleDir, { waitForCallback: "Enabled" });
        expect(enabled.schemaHash).not.toEqual(absent.schemaHash);
    });

    test("changing a value changes the hash, in both directions", () => {
        const enabled = synthRegistration(bundleDir, { waitForCallback: "Enabled" });
        const disabled = synthRegistration(bundleDir, { waitForCallback: "Disabled" });
        const timeout = synthRegistration(bundleDir, {
            waitForCallback: "Enabled",
            taskTimeout: "3600",
        });
        expect(disabled.schemaHash).not.toEqual(enabled.schemaHash);
        expect(timeout.schemaHash).not.toEqual(enabled.schemaHash);
        // Same inputs, same hash.
        expect(synthRegistration(bundleDir, { waitForCallback: "Enabled" }).schemaHash).toEqual(
            enabled.schemaHash
        );
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Locks in two properties of the vamsSchema registration hash. Both fail in ways a deploy reports
 * as success.
 *
 *   1. schemaHash must not move when an unrelated construct shifts the global token index. Override
 *      values like a lambda's functionName are CloudFormation tokens whose synth-time text is
 *      "${Token[TOKEN.n]}", and n depends on allocation order across the whole app. Hashing that
 *      text makes the hash differ on a deploy where no schema file changed, which re-runs every
 *      registration custom resource and overwrites operator edits to built-in pipelines (a rename,
 *      a retuned systemConfig, a deliberate archive) with the values from the schema files.
 *   2. A subdirectory under a bundle's templates/ must not abort synth. The hash walk reads each
 *      entry, so an unfiltered readFileSync on a directory throws EISDIR and fails the entire app
 *      synth rather than that one pipeline.
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { VamsSchemaRegistration } from "../lib/nestedStacks/pipelines/constructs/vamsSchemaRegistration-construct";

/** A minimal on-disk bundle: the two required files plus one template and its webform companion. */
function writeBundle(): string {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vamsSchemaHash-"));
    fs.writeFileSync(path.join(dir, "pipeline.json"), JSON.stringify({ pipelineId: "p" }));
    fs.writeFileSync(path.join(dir, "workflow.json"), JSON.stringify({ workflowId: "w" }));
    fs.mkdirSync(path.join(dir, "templates"));
    fs.writeFileSync(path.join(dir, "templates", "a.json"), JSON.stringify({ t: 1 }));
    fs.writeFileSync(path.join(dir, "templates", "a.webform.json"), JSON.stringify({ ui: 1 }));
    return dir;
}

/** Synthesizes one registration and returns the schemaHash CloudFormation would diff. */
function synthHash(bundleDir: string, fillerFunctions: number): string {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, "S", {
        env: { account: "111122223333", region: "us-west-2" },
    });

    // Consuming a filler function's name advances the global token counter, standing in for an
    // unrelated construct added elsewhere in the app between two deploys.
    for (let i = 0; i < fillerFunctions; i++) {
        new lambda.Function(stack, `Filler${i}`, {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "index.handler",
            code: lambda.Code.fromInline("x=1"),
        }).functionName;
    }

    const target = new lambda.Function(stack, "Target", {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: "index.handler",
        code: lambda.Code.fromInline("x=1"),
    });

    new VamsSchemaRegistration(stack, "Reg", {
        importFunctionName: "importer",
        artefactsBucket: s3.Bucket.fromBucketName(stack, "Artefacts", "artefacts-bucket"),
        vamsSchemaDir: bundleDir,
        resourceOverrides: { lambdaName: target.functionName },
    });

    const resources = app.synth().getStackByName("S").template.Resources as {
        [key: string]: { Properties?: { schemaHash?: string } };
    };
    const hashes = Object.values(resources)
        .map((r) => r.Properties?.schemaHash)
        .filter((h): h is string => h !== undefined);
    expect(hashes).toHaveLength(1);
    return hashes[0];
}

describe("VamsSchemaRegistration schemaHash", () => {
    let bundleDir: string;

    beforeAll(() => {
        bundleDir = writeBundle();
    });

    afterAll(() => {
        fs.rmSync(bundleDir, { recursive: true, force: true });
    });

    test("a token-index shift alone leaves the hash unchanged", () => {
        expect(synthHash(bundleDir, 0)).toEqual(synthHash(bundleDir, 3));
    });

    test("the raw token text does drift, so the check above can detect a regression", () => {
        // Positive control. Without it, a hash that ignored the overrides entirely would also pass.
        const tokenText = (fillers: number) => {
            const app = new cdk.App();
            const stack = new cdk.Stack(app, "C");
            for (let i = 0; i < fillers; i++) {
                new lambda.Function(stack, `F${i}`, {
                    runtime: lambda.Runtime.PYTHON_3_12,
                    handler: "index.handler",
                    code: lambda.Code.fromInline("x=1"),
                }).functionName;
            }
            return new lambda.Function(stack, "T", {
                runtime: lambda.Runtime.PYTHON_3_12,
                handler: "index.handler",
                code: lambda.Code.fromInline("x=1"),
            }).functionName;
        };
        expect(tokenText(0)).not.toEqual(tokenText(3));
    });

    test("an edit to a template file does change the hash", () => {
        const before = synthHash(bundleDir, 0);
        const template = path.join(bundleDir, "templates", "a.json");
        const original = fs.readFileSync(template);
        try {
            fs.writeFileSync(template, JSON.stringify({ t: 2 }));
            expect(synthHash(bundleDir, 0)).not.toEqual(before);
        } finally {
            fs.writeFileSync(template, original);
        }
    });

    test("a subdirectory under templates/ synthesizes and does not affect the hash", () => {
        const before = synthHash(bundleDir, 0);
        const nested = path.join(bundleDir, "templates", "shared");
        fs.mkdirSync(nested);
        fs.writeFileSync(path.join(nested, "fragment.json"), JSON.stringify({ f: 1 }));
        try {
            // The import lambda reads only top-level templates, so a nested file is out of scope for
            // the hash as well — but it must not throw EISDIR.
            expect(synthHash(bundleDir, 0)).toEqual(before);
        } finally {
            fs.rmSync(nested, { recursive: true, force: true });
        }
    });
});

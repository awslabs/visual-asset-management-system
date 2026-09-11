/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `workflow.json` is optional in a vamsSchema bundle, so a pipeline can be registered on its own for use
 * as a step in an operator-authored workflow. Two things have to hold together for that to work, and
 * they fail at different times:
 *
 *   1. Synth must not reject the bundle. `VamsSchemaRegistration` previously required both
 *      `pipeline.json` and `workflow.json`, so a workflow-less bundle threw and took the whole app synth
 *      down — while `backendPipelines/CLAUDE.md` documented the file as optional.
 *   2. The emitted `bundleS3Keys` must OMIT the `workflow` key rather than point at a key that was never
 *      uploaded. The import lambda reads every key it is handed
 *      (`importGlobalPipelineWorkflow.py:200-201`, `if keys.get("workflow"): _read_s3_json(...)`) and
 *      raises on a missing object, so an unconditional key converts the synth failure into a
 *      CREATE_FAILED custom resource halfway through a deploy — strictly worse, and invisible to a
 *      test that only checks synth succeeds.
 *
 * The bundle assembler and descriptor builder on the backend already tolerate the absence
 * (`assemble_bundle` gates on `keys.get("workflow")`; `vamsSchemaImport.py:356` gates the workflow and
 * trigger descriptors on `if workflow:`), so the omitted key is all that is required of this side.
 */

import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { VamsSchemaRegistration } from "../../lib/nestedStacks/pipelines/constructs/vamsSchemaRegistration-construct";
import { newTestApp } from "../support/testApp";

interface BundleOptions {
    withWorkflow: boolean;
    withTemplate?: boolean;
}

function writeBundle(options: BundleOptions): string {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vamsSchemaOptWorkflow-"));
    fs.writeFileSync(
        path.join(dir, "pipeline.json"),
        JSON.stringify({ pipelineId: "p", pipelineName: "P" })
    );
    if (options.withWorkflow) {
        fs.writeFileSync(
            path.join(dir, "workflow.json"),
            JSON.stringify({
                workflowId: "w",
                workflowName: "W",
                triggers: [{ type: "fileUpload" }],
            })
        );
    }
    if (options.withTemplate) {
        fs.mkdirSync(path.join(dir, "templates"));
        fs.writeFileSync(
            path.join(dir, "templates", "t.json"),
            JSON.stringify({ templateId: "t" })
        );
    }
    return dir;
}

interface RegistrationProperties {
    bundleS3Keys: { pipeline?: string; workflow?: string; templates?: string[] };
    triggerEnabled?: string;
    schemaHash: string;
}

/** Synthesizes one registration and returns the custom resource's parsed properties. */
function synthRegistration(bundleDir: string, triggerEnabled?: boolean): RegistrationProperties {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "S", {
        env: { account: "111122223333", region: "us-west-2" },
    });

    new VamsSchemaRegistration(stack, "Reg", {
        importFunctionName: "importer",
        artefactsBucket: s3.Bucket.fromBucketName(stack, "Artefacts", "artefacts-bucket"),
        vamsSchemaDir: bundleDir,
        resourceOverrides: { lambdaName: "some-pipeline-lambda" },
        triggerEnabled,
    });

    const resources = app.synth().getStackByName("S").template.Resources as {
        [key: string]: {
            Properties?: { bundleS3Keys?: string; triggerEnabled?: string; schemaHash?: string };
        };
    };
    const registrations = Object.values(resources).filter(
        (r) => r.Properties?.bundleS3Keys !== undefined
    );
    expect(registrations).toHaveLength(1);
    const properties = registrations[0].Properties!;
    return {
        bundleS3Keys: JSON.parse(properties.bundleS3Keys!),
        triggerEnabled: properties.triggerEnabled,
        schemaHash: properties.schemaHash!,
    };
}

describe("VamsSchemaRegistration accepts a bundle with no workflow.json", () => {
    const dirs: string[] = [];
    const bundle = (options: BundleOptions) => {
        const dir = writeBundle(options);
        dirs.push(dir);
        return dir;
    };

    afterAll(() => {
        for (const dir of dirs) fs.rmSync(dir, { recursive: true, force: true });
    });

    test("a pipeline-only bundle registers the pipeline and emits no workflow key", () => {
        const properties = synthRegistration(bundle({ withWorkflow: false }));

        expect(properties.bundleS3Keys.pipeline).toMatch(/\/pipeline\.json$/);
        // The load-bearing assertion: the key is absent, not present-and-dangling. `undefined` and a
        // dangling string are indistinguishable at synth but not at deploy.
        expect(properties.bundleS3Keys).not.toHaveProperty("workflow");
        expect(Object.keys(properties.bundleS3Keys)).toEqual(["pipeline"]);
        // No workflow means no triggers to enable, so nothing requests one.
        expect(properties.triggerEnabled).toBeUndefined();
    });

    test("control: a bundle WITH workflow.json still emits the workflow key", () => {
        // Positive control for the negative above. Without it, a construct that dropped the workflow key
        // unconditionally — silently un-registering every built-in workflow — would also pass.
        const properties = synthRegistration(bundle({ withWorkflow: true }), true);

        expect(properties.bundleS3Keys.workflow).toMatch(/\/workflow\.json$/);
        expect(properties.triggerEnabled).toEqual("true");
    });

    test("a pipeline-only bundle still carries its templates", () => {
        const properties = synthRegistration(bundle({ withWorkflow: false, withTemplate: true }));

        expect(properties.bundleS3Keys.templates).toHaveLength(1);
        expect(properties.bundleS3Keys.templates![0]).toMatch(/\/templates\/t\.json$/);
        expect(properties.bundleS3Keys).not.toHaveProperty("workflow");
    });

    test("adding a workflow.json changes the schemaHash, so the CR re-runs", () => {
        const withoutWorkflow = bundle({ withWorkflow: false });
        const before = synthRegistration(withoutWorkflow).schemaHash;
        fs.writeFileSync(
            path.join(withoutWorkflow, "workflow.json"),
            JSON.stringify({ workflowId: "w", workflowName: "W" })
        );
        const after = synthRegistration(withoutWorkflow);
        expect(after.schemaHash).not.toEqual(before);
        expect(after.bundleS3Keys.workflow).toMatch(/\/workflow\.json$/);
    });

    test("pipeline.json is still required", () => {
        // The one file that has no optional reading: without it the import lambda has nothing to
        // register, so this must stay a synth-time throw rather than becoming a deploy-time one.
        const empty = fs.mkdtempSync(path.join(os.tmpdir(), "vamsSchemaOptWorkflow-empty-"));
        dirs.push(empty);
        expect(() => synthRegistration(empty)).toThrow(/required pipeline\.json not found/);
    });
});

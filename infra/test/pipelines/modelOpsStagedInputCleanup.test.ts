/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The ModelOps staged input copy does not outlive its run.
 *
 * The ModelOps handler reads Amazon S3 by bucket and key and cannot take a version, so when a run's
 * launch version is no longer current, constructPipeline copies that version into the auxiliary
 * bucket under `Config.MODELOPS_STAGED_INPUT_PREFIX` and points the handler at the copy. The copy is
 * the customer's file, so it must go when the run ends:
 *
 *   * pipelineEnd deletes every version of it on both routes into PipelineEndTask. On the success
 *     route the ECS task's result used to REPLACE the state, which would drop the copy's location;
 *     the task's ResultPath keeps constructPipeline's output as the state instead.
 *   * pipelineEnd's role may delete versions and list versions on that prefix only.
 *   * The auxiliary bucket expires a copy a crashed run leaves (current and noncurrent versions,
 *     and the expired delete markers), only when ModelOps is enabled.
 *
 * The Python handlers key the copy under the same literal, which the last test reads from source.
 * Asserted on the emitted templates; the control is the ModelOps state machine in the synth.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

const PREFIX = Config.MODELOPS_STAGED_INPUT_PREFIX;
const LAMBDA_DIR = path.resolve(__dirname, "../../../backendPipelines/multi/modelOps/lambda");

function enableModelOps(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useModelOps.enabled = true;
    c.app.pipelines.useModelOps.ecrContainerImageURI =
        "123456789012.dkr.ecr.us-east-1.amazonaws.com/modelops:latest";
}

function auxiliaryBucket(synth: SynthResult): Resource {
    const buckets = synth
        .ofType("AWS::S3::Bucket")
        .filter(
            (b) =>
                /StorageResourcesBuilder/.test(b.stack) &&
                b.logicalId.startsWith("AssetAuxiliaryBucket")
        );
    expect(buckets).toHaveLength(1);
    return buckets[0];
}

const stagedRules = (bucket: Resource): any[] =>
    (bucket.properties.LifecycleConfiguration?.Rules ?? []).filter(
        (rule: any) => rule.Prefix === PREFIX
    );

function modelOpsStates(synth: SynthResult): Record<string, any> {
    const definition = synth
        .ofType("AWS::StepFunctions::StateMachine")
        .map((r) => SynthResult.flatten(r.properties.DefinitionString))
        .find((d) => d.includes('"ModelOpsRunFargate"'));
    if (!definition) throw new Error("no emitted state machine carries ModelOpsRunFargate");
    return JSON.parse(definition).States;
}

/** Every statement of every policy attached to the ModelOps pipelineEnd function's role. */
function pipelineEndStatements(synth: SynthResult): any[] {
    const policies = synth
        .ofType("AWS::IAM::Policy")
        .filter((p) => /pipelineEnd/.test(p.logicalId) && /ModelOps/i.test(p.stack));
    expect(policies.length).toBeGreaterThan(0);
    return policies.flatMap((p) => p.properties.PolicyDocument.Statement);
}

const actionsOf = (statement: any): string[] => [].concat(statement.Action);
const resourcesOf = (statement: any): string[] =>
    [].concat(statement.Resource).map((r) => SynthResult.flatten(r));

describe("ModelOps staged input cleanup", () => {
    let synth: SynthResult;
    let states: Record<string, any>;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableModelOps,
            mutateKey: "modelops-staged-input-cleanup",
        });
        states = modelOpsStates(synth);
    });

    test("the ECS task keeps constructPipeline's output as the state for PipelineEndTask", () => {
        expect(states.ModelOpsRunFargate.ResultPath).toBe("$.ModelOpsTaskResult");
        expect(states.ModelOpsRunFargate.Next).toBe("PipelineEndTask");
        // The error route keeps the state too.
        const catcher = states.ModelOpsRunFargate.Catch[0];
        expect(catcher.ResultPath).toBe("$.error");
        expect(states[catcher.Next].Next).toBe("PipelineEndTask");
    });

    test("pipelineEnd may delete versions on the staged-inputs prefix only", () => {
        const statements = pipelineEndStatements(synth);
        const deletes = statements.filter((s) =>
            actionsOf(s).some((a) => a === "s3:DeleteObjectVersion" || a === "s3:DeleteObject")
        );
        expect(deletes).toHaveLength(1);
        expect(actionsOf(deletes[0]).sort()).toEqual(["s3:DeleteObject", "s3:DeleteObjectVersion"]);
        const resources = resourcesOf(deletes[0]);
        expect(resources).toHaveLength(1);
        expect(resources[0]).toMatch(
            /AssetAuxiliaryBucket.*\/pipelines\/modelops\.staged-inputs\/\*$/
        );

        const lists = statements.filter((s) => actionsOf(s).includes("s3:ListBucketVersions"));
        expect(lists).toHaveLength(1);
        expect(lists[0].Condition).toEqual({ StringLike: { "s3:prefix": [`${PREFIX}*`] } });
    });

    test("the auxiliary bucket expires a copy a crashed run leaves", () => {
        const rules = stagedRules(auxiliaryBucket(synth));
        expect(rules).toHaveLength(2);
        const expiry = rules.find((rule: any) => rule.ExpirationInDays !== undefined);
        expect(expiry).toMatchObject({
            Status: "Enabled",
            ExpirationInDays: 1,
            NoncurrentVersionExpiration: { NoncurrentDays: 1 },
        });
        const markers = rules.find((rule: any) => rule.ExpiredObjectDeleteMarker !== undefined);
        expect(markers).toMatchObject({ Status: "Enabled", ExpiredObjectDeleteMarker: true });
    });

    test("without ModelOps the bucket carries no staged-inputs rule", () => {
        const plain = synthTemplate("commercial");
        const bucket = auxiliaryBucket(plain);
        // Control: the bucket's own rules are emitted.
        expect(bucket.properties.LifecycleConfiguration.Rules.length).toBeGreaterThan(0);
        expect(stagedRules(bucket)).toEqual([]);
    });

    test("constructPipeline and pipelineEnd key the copy under the same prefix", () => {
        for (const file of ["constructPipeline.py", "pipelineEnd.py"]) {
            const source = fs.readFileSync(path.join(LAMBDA_DIR, file), "utf-8");
            expect(source).toContain(`STAGED_INPUT_PREFIX = "${PREFIX}"`);
        }
    });
});

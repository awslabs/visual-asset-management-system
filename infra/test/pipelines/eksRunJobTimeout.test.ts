/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline EKS RunJob task waits longer than the Lambda invocation it starts can run.
 *
 * When a Task state's TimeoutSeconds expires, Step Functions stops waiting, but the Lambda invocation
 * carries on. RunJob's States.Timeout handler sends PipelineEnd the job name from the manifest, and
 * PipelineEnd deletes that job. An invocation still running at that point can then create the job
 * after the delete, and nothing is left to clean it up before `activeDeadlineSeconds`.
 *
 * A task timeout longer than the function's own timeout means the invocation has always ended, by
 * returning or by Lambda stopping it, before any RunJob Catch routes the run to PipelineEnd. A Lambda
 * timeout reaches Step Functions as `Sandbox.Timedout` (or `Lambda.Unknown` on older runtimes), which
 * RunJob's States.ALL catcher routes and none of its service-exception retriers resends.
 *
 * Asserted on the emitted template, resolving the function from the RunJob state's own reference, so
 * the comparison holds against whichever function the state actually invokes.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

/** The errors a Lambda function timeout can surface as through the lambda:invoke integration. */
const LAMBDA_TIMEOUT_ERRORS = ["Sandbox.Timedout", "Lambda.Unknown"];

/** Retrier entries that would match either of those errors. */
const WILDCARDS_MATCHING_LAMBDA_TIMEOUT = ["States.ALL", "States.TaskFailed"];

function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

/**
 * The EKS state machine's states and the template that emits it. The definition is an `Fn::Join` of
 * literal fragments around CloudFormation tokens, flattened so each token becomes `${LogicalId...}`.
 */
function eksStateMachine(synth: SynthResult): { stack: string; states: Record<string, any> } {
    for (const [stack, template] of Object.entries<any>(synth.templates)) {
        for (const resource of Object.values<any>(template.Resources ?? {})) {
            if (resource.Type !== "AWS::StepFunctions::StateMachine") continue;
            const definition = SynthResult.flatten(resource.Properties?.DefinitionString);
            if (definition.includes('"RunJob"') && definition.includes('"InitializeCounter"')) {
                return { stack, states: JSON.parse(definition).States };
            }
        }
    }
    throw new Error("no emitted state machine carries the EKS RunJob state");
}

/**
 * The function a Lambda Task state invokes: `Parameters.FunctionName` for the lambda:invoke
 * integration, otherwise the function ARN in `Resource`. The reference must be a GetAtt or Ref to a
 * function in the state machine's own template, which is what the flattened token names.
 */
function invokedFunction(
    synth: SynthResult,
    stack: string,
    state: any
): { logicalId: string; resource: any } {
    const reference: string = String(state.Resource).endsWith(":states:::lambda:invoke")
        ? state.Parameters?.FunctionName
        : state.Resource;
    const match = /^\$\{([A-Za-z0-9]+)(?:\.Arn)?\}$/.exec(String(reference));
    if (!match) throw new Error(`cannot resolve the invoked function from ${reference}`);
    const logicalId = match[1];
    const resource = synth.templates[stack].Resources?.[logicalId];
    if (!resource) throw new Error(`${logicalId} is not a resource in ${stack}`);
    return { logicalId, resource };
}

describe("EKS RunJob outlives the Lambda invocation it starts", () => {
    let synth: SynthResult;
    let runJob: any;
    let fn: { logicalId: string; resource: any };

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-run-job-timeout",
        });
        const { stack, states } = eksStateMachine(synth);
        runJob = states.RunJob;
        fn = invokedFunction(synth, stack, runJob);
    });

    test("the state machine and the function RunJob invokes are both in this synth", () => {
        // The control for the comparison below: the EKS pipeline ships disabled, and a missing
        // timeout on either side would make the comparison meaningless.
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
        expect(runJob.Type).toBe("Task");
        expect(runJob.Parameters.Payload.operation).toBe("RUN_JOB");
        expect(fn.resource.Type).toBe("AWS::Lambda::Function");
        expect(typeof fn.resource.Properties.Timeout).toBe("number");
        expect(typeof runJob.TimeoutSeconds).toBe("number");
    });

    test("RunJob's TimeoutSeconds exceeds the invoked function's Timeout", () => {
        expect(runJob.TimeoutSeconds).toBeGreaterThan(fn.resource.Properties.Timeout);
    });

    test("a Lambda timeout on RunJob is caught by States.ALL and not retried", () => {
        const retried = (runJob.Retry ?? []).flatMap((r: any) => r.ErrorEquals);
        expect(retried.length).toBeGreaterThan(0); // control: the service-exception retrier exists
        for (const error of [...LAMBDA_TIMEOUT_ERRORS, ...WILDCARDS_MATCHING_LAMBDA_TIMEOUT]) {
            expect(retried).not.toContain(error);
        }
        const caught = (runJob.Catch ?? []).flatMap((c: any) => c.ErrorEquals);
        expect(caught).toContain("States.ALL");
    });
});

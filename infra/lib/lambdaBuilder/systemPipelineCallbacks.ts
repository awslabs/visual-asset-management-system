/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Stack } from "aws-cdk-lib";
import { Construct } from "constructs";

/**
 * Terminal-callback registry for system pipelines (backend common/workflows/systemPipelines.py).
 *
 * A system pipeline run (a run VAMS launches for a feature, recorded under a system trigger value) has
 * its terminal status pushed to the feature's callback function by whichever terminal writer records
 * it: the end-state lambda, the error handler, and the execution service (abort, group abort and the
 * read-path reconcile of a run stopped outside VAMS). The registry maps each stored system trigger
 * value to that function. The workflow core never imports the feature: it reads the registry from the
 * CALLBACK_FUNCTIONS_BY_SYSTEM_TRIGGER environment variable on those three functions.
 */
export const SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV = "CALLBACK_FUNCTIONS_BY_SYSTEM_TRIGGER";

/** The stored system trigger values, mirroring SYSTEM_TRIGGER_TYPE_TO_STORED in systemPipelines.py. */
export const SYSTEM_PIPELINE_TRIGGER_TYPES: readonly string[] = ["Zip-Download"];

/** Stored system trigger value -> the function its terminal callback invokes. */
export type SystemPipelineCallbackRegistry = Record<string, lambda.IFunction>;

/** Construct id of the managed policy that carries the callback invoke grant. */
export const SYSTEM_PIPELINE_CALLBACK_POLICY_ID = "SystemPipelineCallbackInvokePolicy";

/**
 * Sets the registry on every terminal-writer function and grants each one `lambda:InvokeFunction` on
 * exactly the registered callback functions (their unqualified ARNs, no wildcard): the callback is an
 * asynchronous invoke of the unqualified function.
 *
 * The grant is ONE customer managed policy in `scope`, attached to every writer's role, not a
 * statement in each role's default (inline) policy. The writers' roles are among the largest in the
 * API stacks and the end-state role is already near IAM's 10,240-character inline quota in the
 * GovCloud and EU Sovereign templates, which a managed policy does not count against (it has its own
 * 6,144). The role's ManagedPolicyArns names the policy, so CloudFormation creates and attaches it
 * before the role updates, and each function waits for it, the same ordering as the workflow builders'
 * per-handler managed policies.
 *
 * An empty registry adds nothing (no variable, no policy), so a deployment with no system pipeline
 * synthesizes the same template as before the seam existed. The permanent-delete function is not a
 * terminal writer and must not be passed here.
 */
export function attachSystemPipelineCallbacks(
    scope: Construct,
    terminalWriters: lambda.Function[],
    registry: SystemPipelineCallbackRegistry
): void {
    const entries = Object.entries(registry);
    if (entries.length === 0) {
        return;
    }
    for (const [triggerType] of entries) {
        if (!SYSTEM_PIPELINE_TRIGGER_TYPES.includes(triggerType)) {
            throw new Error(
                `System pipeline callback registered for unknown trigger type "${triggerType}". ` +
                    `Known: ${SYSTEM_PIPELINE_TRIGGER_TYPES.join(", ")}.`
            );
        }
    }
    const policy = new iam.ManagedPolicy(scope, SYSTEM_PIPELINE_CALLBACK_POLICY_ID, {
        description: "Invoke the registered system pipeline terminal callback functions",
        statements: [
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["lambda:InvokeFunction"],
                resources: entries.map(([, fn]) => fn.functionArn),
            }),
        ],
    });
    for (const writer of terminalWriters) {
        const value = Stack.of(writer).toJsonString(
            Object.fromEntries(entries.map(([triggerType, fn]) => [triggerType, fn.functionName]))
        );
        writer.addEnvironment(SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV, value);
        if (!writer.role) {
            throw new Error(
                `System pipeline terminal writer ${writer.node.path} has no role to attach the ` +
                    `callback invoke policy to.`
            );
        }
        writer.role.addManagedPolicy(policy);
        writer.node.defaultChild!.node.addDependency(policy);
    }
}

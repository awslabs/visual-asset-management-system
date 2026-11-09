/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The system-pipeline terminal-callback registry (`attachSystemPipelineCallbacks` in
 * lib/lambdaBuilder/workflowFunctions.ts).
 *
 * The registry is how the workflow core reaches a feature's callback without importing it, so it has
 * four contracts to hold: an empty registry leaves the terminal-writer functions exactly as they were
 * (no variable, no IAM policy), a registered callback is invokable only by its exact ARN (no
 * wildcard), that grant is one customer managed policy and never a statement in the writers' inline
 * policies (they are near IAM's inline quota in GovCloud and EU Sovereign), and the names on both sides of the seam match the backend helper
 * (backend/backend/common/workflows/systemPipelines.py), which reads the variable and the values.
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Match, Template } from "aws-cdk-lib/assertions";
import {
    attachSystemPipelineCallbacks,
    SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV,
    SYSTEM_PIPELINE_CALLBACK_POLICY_ID,
    SYSTEM_PIPELINE_TRIGGER_TYPES,
} from "../../lib/lambdaBuilder/workflowFunctions";
import { newTestApp } from "../support/testApp";

const BACKEND_HELPER = path.join(
    __dirname,
    "..",
    "..",
    "..",
    "backend",
    "backend",
    "common",
    "workflows",
    "systemPipelines.py"
);

function fn(stack: cdk.Stack, id: string): lambda.Function {
    return new lambda.Function(stack, id, {
        code: lambda.Code.fromInline("def handler(event, context):\n    return {}\n"),
        handler: "index.handler",
        runtime: lambda.Runtime.PYTHON_3_12,
    });
}

function writersAndCallback() {
    const stack = new cdk.Stack(newTestApp(), "SystemPipelineCallbackStack");
    const writers = [
        fn(stack, "EndState"),
        fn(stack, "ErrorHandler"),
        fn(stack, "ExecutionService"),
    ];
    const callback = fn(stack, "FeatureCallback");
    return { stack, writers, callback };
}

describe("attachSystemPipelineCallbacks", () => {
    test("an empty registry adds no variable and no statement", () => {
        const { stack, writers } = writersAndCallback();
        attachSystemPipelineCallbacks(stack, writers, {});
        const template = Template.fromStack(stack);
        template.resourceCountIs("AWS::IAM::Policy", 0);
        template.resourceCountIs("AWS::IAM::ManagedPolicy", 0);
        for (const resource of Object.values(template.findResources("AWS::Lambda::Function"))) {
            const variables = resource.Properties?.Environment?.Variables ?? {};
            expect(variables[SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV]).toBeUndefined();
        }
    });

    test("a registered callback sets the variable and grants invoke on its exact ARN only", () => {
        const { stack, writers, callback } = writersAndCallback();
        attachSystemPipelineCallbacks(stack, writers, { "Zip-Download": callback });
        const template = Template.fromStack(stack);

        // Every writer carries the registry.
        const withVariable = Object.values(template.findResources("AWS::Lambda::Function")).filter(
            (resource) =>
                resource.Properties?.Environment?.Variables?.[
                    SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV
                ] !== undefined
        );
        expect(withVariable).toHaveLength(writers.length);

        // One managed policy, one statement, the callback's ARN and nothing broader.
        const managed = template.findResources("AWS::IAM::ManagedPolicy");
        expect(Object.keys(managed)).toHaveLength(1);
        const [policyId, policy] = Object.entries(managed)[0];
        expect(policyId.startsWith(SYSTEM_PIPELINE_CALLBACK_POLICY_ID)).toBe(true);
        const statements = policy.Properties.PolicyDocument.Statement;
        expect(statements).toHaveLength(1);
        expect(statements[0].Action).toBe("lambda:InvokeFunction");
        expect(statements[0].Resource).toEqual(stack.resolve(callback.functionArn));
        expect(JSON.stringify(statements[0].Resource)).not.toContain("*");
        template.hasResourceProperties("AWS::Lambda::Function", {
            Environment: {
                Variables: {
                    [SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV]: Match.anyValue(),
                },
            },
        });
    });

    test("the grant is attached to every writer's role and never lands in an inline policy", () => {
        const { stack, writers, callback } = writersAndCallback();
        attachSystemPipelineCallbacks(stack, writers, { "Zip-Download": callback });
        const template = Template.fromStack(stack);
        const policyId = Object.keys(template.findResources("AWS::IAM::ManagedPolicy"))[0];

        for (const writer of writers) {
            const roleId = stack.getLogicalId(writer.role!.node.defaultChild as cdk.CfnElement);
            const role = template.findResources("AWS::IAM::Role")[roleId];
            expect(JSON.stringify(role.Properties.ManagedPolicyArns)).toContain(
                JSON.stringify({ Ref: policyId })
            );
            // The function is created after the policy, so it never runs without the grant.
            const fnId = stack.getLogicalId(writer.node.defaultChild as cdk.CfnElement);
            expect(template.findResources("AWS::Lambda::Function")[fnId].DependsOn).toEqual(
                expect.arrayContaining([policyId])
            );
        }
        // No inline (default) policy carries the invoke action.
        expect(JSON.stringify(template.findResources("AWS::IAM::Policy"))).not.toContain(
            "lambda:InvokeFunction"
        );
    });

    test("an unknown trigger value is refused at synth", () => {
        const { stack, writers, callback } = writersAndCallback();
        expect(() => attachSystemPipelineCallbacks(stack, writers, { Manual: callback })).toThrow(
            /unknown trigger type/
        );
    });
});

describe("system pipeline seam parity with the backend helper", () => {
    const source = fs.readFileSync(BACKEND_HELPER, "utf-8");

    test("the registry variable name matches", () => {
        expect(source).toContain(
            `CALLBACK_REGISTRY_ENV = "${SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV}"`
        );
    });

    test("the stored system trigger values match", () => {
        const mapping = source.match(/SYSTEM_TRIGGER_TYPE_TO_STORED = \{([^}]*)\}/);
        expect(mapping).not.toBeNull();
        const stored = Array.from(mapping![1].matchAll(/"[^"]+":\s*"([^"]+)"/g)).map((m) => m[1]);
        expect([...stored].sort()).toEqual([...SYSTEM_PIPELINE_TRIGGER_TYPES].sort());
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The system-pipeline terminal-callback registry (lib/lambdaBuilder/systemPipelineCallbacks.ts).
 *
 * The registry is how the workflow core reaches a feature's callback without importing it, so it has
 * three contracts to hold: an empty registry leaves the terminal-writer functions exactly as they were
 * (no variable, no IAM statement), a registered callback is invokable only by its exact ARN (no
 * wildcard), and the names on both sides of the seam match the backend helper
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
    SYSTEM_PIPELINE_TRIGGER_TYPES,
} from "../../lib/lambdaBuilder/systemPipelineCallbacks";
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
        attachSystemPipelineCallbacks(writers, {});
        const template = Template.fromStack(stack);
        template.resourceCountIs("AWS::IAM::Policy", 0);
        for (const resource of Object.values(template.findResources("AWS::Lambda::Function"))) {
            const variables = resource.Properties?.Environment?.Variables ?? {};
            expect(variables[SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV]).toBeUndefined();
        }
    });

    test("a registered callback sets the variable and grants invoke on its exact ARN only", () => {
        const { stack, writers, callback } = writersAndCallback();
        attachSystemPipelineCallbacks(writers, { "Zip-Download": callback });
        const template = Template.fromStack(stack);

        // Every writer carries the registry.
        const withVariable = Object.values(template.findResources("AWS::Lambda::Function")).filter(
            (resource) =>
                resource.Properties?.Environment?.Variables?.[
                    SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV
                ] !== undefined
        );
        expect(withVariable).toHaveLength(writers.length);

        // Each writer's policy names the callback's ARN and nothing broader.
        const policies = Object.values(template.findResources("AWS::IAM::Policy"));
        expect(policies).toHaveLength(writers.length);
        const callbackArnRef = stack.resolve(callback.functionArn);
        for (const policy of policies) {
            const statements = policy.Properties.PolicyDocument.Statement;
            const invoke = statements.filter(
                (s: { Action: string | string[] }) =>
                    s.Action === "lambda:InvokeFunction" ||
                    (Array.isArray(s.Action) && s.Action.includes("lambda:InvokeFunction"))
            );
            expect(invoke).toHaveLength(1);
            expect(invoke[0].Resource).toEqual(callbackArnRef);
            expect(JSON.stringify(invoke[0].Resource)).not.toContain("*");
        }
        template.hasResourceProperties("AWS::Lambda::Function", {
            Environment: {
                Variables: {
                    [SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV]: Match.anyValue(),
                },
            },
        });
    });

    test("an unknown trigger value is refused at synth", () => {
        const { writers, callback } = writersAndCallback();
        expect(() => attachSystemPipelineCallbacks(writers, { Manual: callback })).toThrow(
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

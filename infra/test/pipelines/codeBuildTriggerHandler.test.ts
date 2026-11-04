/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every pipeline built with `useCodeBuild: true` starts its image build through the one shared
 * build-trigger handler, which can fail the deployment.
 *
 * Each `*CodeBuild` construct carried its own inline copy of a handler that called
 * `cfnresponse.send(...)` behind `cr.Provider`. Behind the provider framework that call reports to
 * nothing, and the handler swallowed every exception, so a build that could not be started was reported
 * to AWS CloudFormation as a success. The handler now lives in one file
 * (`lib/nestedStacks/pipelines/constructs/codeBuildTrigger/index.py`), which raises on a failure; its
 * behaviour is exercised in `backendPipelines/tests/test_codebuild_build_trigger_handler.py`. This file
 * holds every construct to that one handler, so a copy cannot drift back in. The GenAI CAD STEP agent's
 * construct uses it as the onEvent of a provider that also has an isComplete waiter, because its Amazon
 * Bedrock AgentCore Runtime validates the image when it is created; the waiter reads the `BuildId` the
 * handler returns (`backendPipelines/genAi/cadStepAgent/lambda/imageBuildCustomResource.py`).
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Template } from "aws-cdk-lib/assertions";
import {
    CODEBUILD_TRIGGER_HANDLER_PATH,
    codeBuildTriggerCode,
} from "../../lib/nestedStacks/pipelines/constructs/codeBuildTrigger";
import { newTestApp } from "../support/testApp";

const PIPELINES_DIR = path.join(__dirname, "..", "..", "lib", "nestedStacks", "pipelines");

/** Every `*CodeBuild-construct.ts` under the pipelines directory. */
const codeBuildConstructs = (dir: string): string[] =>
    fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return codeBuildConstructs(full);
        return entry.name.endsWith("CodeBuild-construct.ts") ? [full] : [];
    });

const CONSTRUCTS = codeBuildConstructs(PIPELINES_DIR);
const HANDLER_SOURCE = fs.readFileSync(CODEBUILD_TRIGGER_HANDLER_PATH, "utf8");

describe("CodeBuild build triggers", () => {
    test("[control] the six CodeBuild constructs are found", () => {
        expect(CONSTRUCTS.map((f) => path.basename(f)).sort()).toEqual([
            "cadStepAgentCodeBuild-construct.ts",
            "coordinateTransformCodeBuild-construct.ts",
            "cosmosCodeBuild-construct.ts",
            "gr00tCodeBuild-construct.ts",
            "imageTo3dCodeBuild-construct.ts",
            "isaacLabCodeBuild-construct.ts",
            "splatToolboxCodeBuild-construct.ts",
            "videoSopBomCodeBuild-construct.ts",
        ]);
    });

    test.each(CONSTRUCTS.map((f) => [path.relative(PIPELINES_DIR, f), f]))(
        "%s uses the shared handler and carries no inline copy",
        (_name, file) => {
            const source = fs.readFileSync(file, "utf8");
            expect(source).toContain("code: codeBuildTriggerCode(),");
            expect(source).not.toContain("Code.fromInline(");
            expect(source).not.toContain("cfnresponse");
        }
    );

    test("the handler does not use cfnresponse and fits inline function code", () => {
        expect(HANDLER_SOURCE).not.toContain("cfnresponse");
        expect(HANDLER_SOURCE).toMatch(/^def handler\(event, context\):$/m);
        expect(Buffer.byteLength(HANDLER_SOURCE, "utf8")).toBeLessThanOrEqual(4096);
    });

    test("the synthesized trigger function's ZipFile is the handler file", () => {
        const stack = new cdk.Stack(newTestApp(), "TriggerCodeStack");
        new lambda.Function(stack, "Trigger", {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "index.handler",
            code: codeBuildTriggerCode(),
        });
        Template.fromStack(stack).hasResourceProperties("AWS::Lambda::Function", {
            Code: { ZipFile: HANDLER_SOURCE },
            Handler: "index.handler",
        });
    });
});

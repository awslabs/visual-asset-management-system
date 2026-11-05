/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shared Fargate pipeline construct's optional job retry (issue 1074). Without the setting the job definition
 * keeps one attempt and no evaluateOnExit rules, which is what every pipeline but the SYSTEM GenAI metadata render
 * job synthesizes. The infrastructure-fault retry retries an attempt whose task failed to start (storage or network
 * interface provisioning, the image pull) and ends the job on any other failure.
 */

import * as fs from "fs";
import * as path from "path";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as batch from "aws-cdk-lib/aws-batch";
import * as iam from "aws-cdk-lib/aws-iam";
import { Template } from "aws-cdk-lib/assertions";
import {
    BatchFargatePipelineConstruct,
    FARGATE_INFRASTRUCTURE_CONTAINER_REASONS,
    FARGATE_INFRASTRUCTURE_STATUS_REASONS,
    infrastructureFaultRetry,
    jobDefinitionRetry,
} from "../../lib/nestedStacks/pipelines/constructs/batch-fargate-pipeline";
import {
    FARGATE_RENDER_ATTEMPTS,
    SystemGenAiMetadataConstruct,
} from "../../lib/nestedStacks/pipelines/system/genAiMetadata/constructs/systemGenAiMetadata-construct";
import { makePipelineHarness } from "../support/pipelineConstructHarness";
import { Service } from "../../lib/helper/service-helper";
import * as cdk from "aws-cdk-lib";

const PIPELINES_DIR = path.resolve(__dirname, "../../lib/nestedStacks/pipelines");

const EXPECTED_RULES = [
    { Action: "RETRY", OnStatusReason: "Timeout waiting for EphemeralStorage*" },
    { Action: "RETRY", OnStatusReason: "Timeout waiting for network interface provisioning*" },
    { Action: "RETRY", OnReason: "CannotPullContainerError*" },
    { Action: "EXIT", OnExitCode: "*" },
    { Action: "EXIT", OnStatusReason: "*" },
];

/** The one Fargate job definition a synthesized construct carries. */
const jobDefinition = (template: Template): any => {
    const definitions = Object.values(template.findResources("AWS::Batch::JobDefinition"));
    expect(definitions).toHaveLength(1);
    return (definitions[0] as any).Properties;
};

const fargateConstruct = (
    id: string,
    retry?: ReturnType<typeof infrastructureFaultRetry>
): Template => {
    const h = makePipelineHarness(id);
    const role = (name: string) =>
        new iam.Role(h.stack, name, { assumedBy: Service("ECS_TASKS").Principal });
    new BatchFargatePipelineConstruct(h.stack, "Pipeline", {
        config: h.config,
        vpc: h.vpc,
        subnets: h.subnets,
        securityGroups: h.securityGroups,
        jobRole: role("JobRole"),
        executionRole: role("ExecutionRole"),
        imageAssetPath: "unused",
        dockerfileName: "Dockerfile",
        ecrImage: h.codeBuildImage,
        batchJobDefinitionName: "RetryTestJob",
        attemptDuration: cdk.Duration.hours(1),
        ...(retry ? { retry } : {}),
    });
    return Template.fromStack(h.stack);
};

/** AWS Batch's evaluateOnExit glob: the pattern matches exactly, or as a prefix when it ends in `*`. */
const globMatches = (pattern: string, value: string | undefined): boolean => {
    if (value === undefined) return false;
    return pattern.endsWith("*") ? value.startsWith(pattern.slice(0, -1)) : value === pattern;
};

/** The action `rules` take on an attempt, or RETRY when none matches (AWS Batch's rule for no match). */
const actionFor = (
    rules: any[],
    attempt: { statusReason?: string; reason?: string; exitCode?: string }
): string => {
    for (const rule of rules) {
        const conditions: [string | undefined, string | undefined][] = [
            [rule.OnStatusReason, attempt.statusReason],
            [rule.OnReason, attempt.reason],
            [rule.OnExitCode, attempt.exitCode],
        ];
        const set = conditions.filter(([pattern]) => pattern !== undefined);
        if (set.every(([pattern, value]) => globMatches(pattern as string, value))) {
            return rule.Action;
        }
    }
    return "RETRY";
};

describe("Fargate pipeline job retry", () => {
    test("without the setting a job definition keeps one attempt and no rules", () => {
        expect(jobDefinitionRetry(undefined)).toEqual({ retryAttempts: 1 });
        expect(jobDefinition(fargateConstruct("NoRetry")).RetryStrategy).toEqual({ Attempts: 1 });
    });

    test("the infrastructure-fault retry renders its attempts and rules in order", () => {
        const properties = jobDefinition(
            fargateConstruct("InfraRetry", infrastructureFaultRetry(3))
        );
        expect(properties.RetryStrategy).toEqual({ Attempts: 3, EvaluateOnExit: EXPECTED_RULES });
    });

    test("the rules retry the task-start faults and end the job on its own failures", () => {
        // The rules as the job definition renders them, matched the way AWS Batch matches them.
        const rules = jobDefinition(fargateConstruct("Rules", infrastructureFaultRetry(3)))
            .RetryStrategy.EvaluateOnExit;
        const actionOf = (attempt: { statusReason?: string; reason?: string; exitCode?: string }) =>
            actionFor(rules, attempt);
        // Observed on a test deployment (issue 1074), and the other start faults the rules name.
        expect(
            actionOf({
                statusReason: "Timeout waiting for EphemeralStorage provisioning to complete.",
            })
        ).toBe("RETRY");
        expect(
            actionOf({
                statusReason: "Timeout waiting for network interface provisioning to complete.",
            })
        ).toBe("RETRY");
        expect(
            actionOf({
                statusReason: "Task failed to start",
                reason: "CannotPullContainerError: pull image manifest has been retried 5 time(s)",
            })
        ).toBe("RETRY");
        // The job's own work failed, ran past its duration, or the task stopped for another reason.
        expect(
            actionOf({
                statusReason: "Essential container in task exited",
                reason: "",
                exitCode: "1",
            })
        ).toBe("EXIT");
        expect(actionOf({ statusReason: "Job attempt duration exceeded timeout" })).toBe("EXIT");
        expect(
            actionOf({ statusReason: "ResourceInitializationError: unable to pull secrets" })
        ).toBe("EXIT");
    });

    test("every pattern is one AWS Batch accepts", () => {
        // Letters, numbers, periods, colons and white space, optionally ending in an asterisk; at most 512.
        const allowed = /^[A-Za-z0-9.: \t]*\*?$/;
        for (const pattern of [
            ...FARGATE_INFRASTRUCTURE_STATUS_REASONS,
            ...FARGATE_INFRASTRUCTURE_CONTAINER_REASONS,
        ]) {
            expect(pattern).toMatch(allowed);
            expect(pattern.length).toBeLessThanOrEqual(512);
        }
    });

    test("a retry outside what AWS Batch takes is refused at synth", () => {
        const rule = batch.RetryStrategy.of(batch.Action.EXIT, batch.Reason.NON_ZERO_EXIT_CODE);
        expect(() => jobDefinitionRetry({ attempts: 0, strategies: [] })).toThrow(
            /1 to 10 attempts/
        );
        expect(() => jobDefinitionRetry({ attempts: 11, strategies: [] })).toThrow(
            /1 to 10 attempts/
        );
        expect(() =>
            jobDefinitionRetry({ attempts: 2, strategies: [rule, rule, rule, rule, rule, rule] })
        ).toThrow(/at most 5/);
    });

    test("the SYSTEM GenAI metadata render job retries infrastructure faults only", () => {
        const h = makePipelineHarness("SgmRetry", (c) => {
            c.app.pipelines.useSystemGenAiMetadata.enabled = true;
            c.app.pipelines.useSystemGenAiMetadata.useFargateRenderer = true;
            c.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS = false;
        });
        const construct = new SystemGenAiMetadataConstruct(h.stack, "Sgm", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer as lambda.LayerVersion,
            importGlobalPipelineWorkflowV2FunctionName: "vams-test-importFunction",
        });
        expect(jobDefinition(Template.fromStack(construct)).RetryStrategy).toEqual({
            Attempts: FARGATE_RENDER_ATTEMPTS,
            EvaluateOnExit: EXPECTED_RULES,
        });
    });

    test("no other pipeline passes a retry to the shared construct", () => {
        // The other Fargate pipelines keep today's job definition until each one opts in; the synth diff of every
        // job definition is the deployment-level proof, this is the source-level one.
        const users: string[] = [];
        const walk = (dir: string) => {
            for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
                const full = path.join(dir, entry.name);
                if (entry.isDirectory()) walk(full);
                else if (entry.name.endsWith(".ts")) {
                    const source = fs.readFileSync(full, "utf8");
                    if (source.includes("new BatchFargatePipelineConstruct(")) users.push(full);
                }
            }
        };
        walk(PIPELINES_DIR);
        expect(users.length).toBeGreaterThanOrEqual(6);
        const retrying = users
            .filter((file) =>
                /infrastructureFaultRetry\(|\bretry:/.test(fs.readFileSync(file, "utf8"))
            )
            .map((file) => path.relative(PIPELINES_DIR, file));
        expect(retrying).toEqual([
            path.join("system", "genAiMetadata", "constructs", "systemGenAiMetadata-construct.ts"),
        ]);
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The one place the Batch default container log group is named for the registering lambdas and the
 * executionService grant. Two shapes are pinned because each fails only at read time: the log-group
 * ARN must be the colon-separated form the backend validator accepts, and a job definition NAME must
 * come out of a CfnJobDefinition Ref (which is the ARN with a revision).
 */

import * as cdk from "aws-cdk-lib";
import * as batch from "aws-cdk-lib/aws-batch";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import {
    BATCH_JOB_LOG_GROUP_NAME,
    batchJobLogGroupEnvironment,
    jobDefinitionNameFromRef,
} from "../../lib/helper/batchJobLogGroup";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    return config;
};

describe("batchJobLogGroupEnvironment", () => {
    test("names the Batch default group and its colon-separated log-group ARN", () => {
        Service.SetConfig(createMockConfig());
        const env = batchJobLogGroupEnvironment();
        expect(env).toEqual({
            BATCH_JOB_LOG_GROUP_NAME: "/aws/batch/job",
            BATCH_JOB_LOG_GROUP_ARN: `arn:aws:logs:${REGION}:${ACCOUNT}:log-group:/aws/batch/job`,
        });
        // `log-group//aws/batch/job` is what formatArn(SLASH_RESOURCE_NAME) renders for a name
        // starting with '/', and the backend validator rejects it; the separator is pinned.
        expect(env.BATCH_JOB_LOG_GROUP_ARN).toContain(`:log-group:${BATCH_JOB_LOG_GROUP_NAME}`);
        expect(env.BATCH_JOB_LOG_GROUP_ARN).not.toContain("log-group//");
    });
});

describe("jobDefinitionNameFromRef", () => {
    test("selects the name component of the job definition ARN and drops the revision", () => {
        Service.SetConfig(createMockConfig());
        const stack = new cdk.Stack(newTestApp(), "HelperStack", {
            env: { account: ACCOUNT, region: REGION },
        });
        const jobDefinition = new batch.CfnJobDefinition(stack, "JobDef", {
            type: "container",
            containerProperties: { image: "public.ecr.aws/docker/library/busybox:latest" },
        });
        // Same shape EcsJobDefinition.jobDefinitionName renders for the Fargate pipelines, so both
        // families prefix their Batch log streams with the same string.
        expect(stack.resolve(jobDefinitionNameFromRef(jobDefinition.ref))).toEqual({
            "Fn::Select": [
                1,
                {
                    "Fn::Split": [
                        "/",
                        {
                            "Fn::Select": [
                                5,
                                { "Fn::Split": [":", { Ref: stack.getLogicalId(jobDefinition) }] },
                            ],
                        },
                    ],
                },
            ],
        });
    });
});

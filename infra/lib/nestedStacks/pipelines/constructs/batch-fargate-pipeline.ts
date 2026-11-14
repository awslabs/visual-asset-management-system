/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as batch from "aws-cdk-lib/aws-batch";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as logs from "aws-cdk-lib/aws-logs";
import * as cdk from "aws-cdk-lib";
import * as Config from "../../../../config/config";
import { Construct } from "constructs";
import { CfnJobDefinition } from "aws-cdk-lib/aws-batch";
import { generateUniqueNameHash } from "../../../helper/security";
import path = require("path");

export interface BatchFargatePipelineConstructProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
    jobRole: iam.Role;
    executionRole: iam.Role;
    imageAssetPath: string;
    dockerfileName: string;
    batchJobDefinitionName: string;
    /**
     * Ephemeral storage size in GiB for the Fargate container.
     * Fargate supports 21-200 GiB. Default is 60 GiB.
     */
    ephemeralStorageGiB?: number;
    /**
     * vCPU reserved for the container. Default 16, the sizing the point-cloud and rendering pipelines
     * were built against; a caller that names a smaller figure must pair it with a Fargate-valid
     * `memoryMiB`.
     */
    cpu?: number;
    /** Memory reserved for the container, in MiB. Default 65536. */
    memoryMiB?: number;
    /**
     * Log group the container's stdout and stderr are written to through the `awslogs` driver, as
     * `<jobDefinitionName>/default/<ecs-task-id>`. Every Fargate pipeline passes its own VAMS-owned
     * `/aws/vendedlogs/Pipelines/<Name><hash>` group and registers that same group as the Batch
     * state's log source. When absent, AWS Batch writes to its default `/aws/batch/job` group, which
     * carries neither a KMS key nor a retention policy.
     */
    logGroup?: logs.ILogGroup;
    /**
     * Additional environment variables for the container, merged over the AWS_REGION / AWS_ACCOUNT
     * pair every job receives. Configuration pointers only (a model id, a secret ARN), never a
     * credential value.
     */
    environment?: { [key: string]: string };
    /**
     * Hard limit on a single job attempt, after which AWS Batch terminates the job itself.
     *
     * Required rather than optional so a new pipeline has to state its own bound: with no attempt
     * duration a wedged 16 vCPU / 64 GiB container runs until someone notices. The orchestration's
     * timeout is not a substitute — a pipeline that submits its job from a Lambda under
     * `WAIT_FOR_TASK_TOKEN` (coordinate transform) owns the job itself, so Step Functions giving up
     * bounds only the token wait, not the container.
     *
     * Set it to the enclosing orchestration bound (the task timeout, or the state machine timeout for
     * a `.sync` submission). Equal is correct here: the orchestration clock starts first, so it still
     * gives up before Batch does on a live execution, and this limit only takes effect once the
     * orchestration is no longer watching.
     */
    attemptDuration: cdk.Duration;
    /**
     * Optional CodeBuild-produced ECR image to use instead of a local Docker build. When provided,
     * imageAssetPath is ignored and the image is pulled from this repository at this tag.
     *
     * The tag travels with the repository in one prop rather than as a separate optional value: the
     * tag the job definition names and the tag CodeBuild pushes have to be the same string, and a
     * caller that supplies the repository alone would silently fall back to a mutable alias.
     */
    ecrImage?: { repository: ecr.IRepository; tag: string };
    /**
     * Optional retry of a failed job attempt by AWS Batch: the attempts (1-10) and the `evaluateOnExit` rules
     * (at most 5, matched in order) that decide whether a failed attempt is retried or the job exits. Batch retries
     * an attempt no rule matches, so a rule list ends with an EXIT rule that matches everything else.
     *
     * Absent, the job definition keeps one attempt and no rules: a failed attempt fails the job. Use
     * `infrastructureFaultRetry` to retry only the faults of starting the task, never a failure of the job's own
     * work.
     */
    retry?: BatchJobRetry;
}

/** The retry setting of a Batch job definition: total attempts and the evaluateOnExit rules. */
export interface BatchJobRetry {
    attempts: number;
    strategies: batch.RetryStrategy[];
}

/**
 * The status reasons of a Fargate job attempt that failed while AWS Batch and Amazon ECS were starting its task,
 * before the container ran: storage or network interface provisioning that timed out. A relaunch of the same job
 * succeeds once the fault clears (issue 1074).
 */
export const FARGATE_INFRASTRUCTURE_STATUS_REASONS = [
    "Timeout waiting for EphemeralStorage*",
    "Timeout waiting for network interface provisioning*",
];
/** The container reason of an attempt whose image could not be pulled. */
export const FARGATE_INFRASTRUCTURE_CONTAINER_REASONS = ["CannotPullContainerError*"];

/**
 * A retry of infrastructure faults only: an attempt that failed to start its task for one of the reasons above
 * is retried, up to `attempts` attempts in all; any other failure, a non-zero exit of the job's own work or an
 * attempt that ran past its duration included, ends the job on that attempt.
 */
export function infrastructureFaultRetry(attempts: number): BatchJobRetry {
    return {
        attempts: attempts,
        strategies: [
            ...FARGATE_INFRASTRUCTURE_STATUS_REASONS.map((onStatusReason) =>
                batch.RetryStrategy.of(batch.Action.RETRY, batch.Reason.custom({ onStatusReason }))
            ),
            ...FARGATE_INFRASTRUCTURE_CONTAINER_REASONS.map((onReason) =>
                batch.RetryStrategy.of(batch.Action.RETRY, batch.Reason.custom({ onReason }))
            ),
            // An attempt whose container ran and exited, and then any other attempt: both end the job.
            batch.RetryStrategy.of(batch.Action.EXIT, batch.Reason.custom({ onExitCode: "*" })),
            batch.RetryStrategy.of(batch.Action.EXIT, batch.Reason.custom({ onStatusReason: "*" })),
        ],
    };
}

/** The job definition's retry properties: one attempt and no rules unless a retry is given. */
export function jobDefinitionRetry(
    retry: BatchJobRetry | undefined
): Pick<batch.EcsJobDefinitionProps, "retryAttempts" | "retryStrategies"> {
    if (!retry) {
        return { retryAttempts: 1 };
    }
    if (!Number.isInteger(retry.attempts) || retry.attempts < 1 || retry.attempts > 10) {
        throw new Error(`A Batch job retry takes 1 to 10 attempts; ${retry.attempts} was given.`);
    }
    if (retry.strategies.length > 5) {
        throw new Error(
            `A Batch job definition takes at most 5 evaluateOnExit rules; ${retry.strategies.length} were given.`
        );
    }
    return { retryAttempts: retry.attempts, retryStrategies: retry.strategies };
}

const defaultProps: Partial<BatchFargatePipelineConstructProps> = {
    //stackName: "",
    //env: {},
};

export class BatchFargatePipelineConstruct extends Construct {
    public readonly batchJobDefinition: batch.IJobDefinition;
    /**
     * The job definition's physical name (base name plus hash) as a literal string. A submit by this
     * name, without a revision, runs the latest ACTIVE revision, so a run that spans a deploy which
     * replaced the definition still submits its later jobs.
     */
    public readonly batchJobDefinitionPhysicalName: string;
    public readonly batchJobQueue: batch.JobQueue;

    constructor(parent: Construct, name: string, props: BatchFargatePipelineConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };
        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;

        const batchEnvironment = new batch.FargateComputeEnvironment(
            this,
            "PipelineBatchComputeEnvironment",
            {
                vpc: props.vpc,
                vpcSubnets: props.vpc.selectSubnets({
                    subnets: props.subnets,
                }),
                securityGroups: props.securityGroups,
            }
        );

        // Container image: use ECR repository if provided, otherwise build locally
        const containerImage = props.ecrImage
            ? ecs.ContainerImage.fromEcrRepository(props.ecrImage.repository, props.ecrImage.tag)
            : // Synth-time asset path built from __dirname and a construct prop that the calling
              // construct hard-codes; CDK resolves it on the operator's machine, never from request
              // input.
              // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
              ecs.AssetImage.fromAsset(path.join(__dirname, props.imageAssetPath), {
                  file: props.dockerfileName,
                  platform: cdk.aws_ecr_assets.Platform.LINUX_AMD64,
              });

        const batchJobName =
            props.batchJobDefinitionName +
            generateUniqueNameHash(
                props.config.env.coreStackName,
                props.config.env.account,
                props.batchJobDefinitionName,
                10
            );
        this.batchJobDefinitionPhysicalName = batchJobName;

        this.batchJobDefinition = new batch.EcsJobDefinition(this, "PipelineBatchJobDefinition", {
            jobDefinitionName: batchJobName,
            ...jobDefinitionRetry(props.retry),
            timeout: props.attemptDuration,
            container: new batch.EcsFargateContainerDefinition(this, "PipelineBatchContainer", {
                cpu: props.cpu ?? 16,
                memory: cdk.Size.mebibytes(props.memoryMiB ?? 65536),
                ephemeralStorageSize: cdk.Size.gibibytes(props.ephemeralStorageGiB ?? 60),
                image: containerImage,
                environment: {
                    AWS_REGION: region,
                    AWS_ACCOUNT: account,
                    ...(props.environment ?? {}),
                },
                jobRole: props.jobRole,
                executionRole: props.executionRole,
                // The stream is `<awslogs-stream-prefix>/default/<ecs-task-id>` (Batch names the
                // container `default`). Prefixing with the PHYSICAL job definition name -- base name
                // plus hash, the string `jobDefinition.jobDefinitionName` resolves to -- keeps the
                // stream under the `<jobDefinitionName>/default/` prefix the pipeline's registering
                // lambda derives from that same property, and matches the shape Batch's default
                // group gives the GPU pipelines. With the bare base name the resolved stream falls
                // outside the registered prefix and the execution log view filters every line out.
                logging: props.logGroup
                    ? ecs.LogDrivers.awsLogs({
                          logGroup: props.logGroup,
                          streamPrefix: batchJobName,
                      })
                    : undefined,
                // No `user` override: the job runs as whatever the image's own USER declares. An
                // override here replaces it, so a container that drops privileges in its Dockerfile
                // would still run as uid 0.
            }),
        });

        this.batchJobQueue = new batch.JobQueue(this, "BatchJobQueue", {
            computeEnvironments: [
                {
                    computeEnvironment: batchEnvironment,
                    order: 1,
                },
            ],
        });
    }
}

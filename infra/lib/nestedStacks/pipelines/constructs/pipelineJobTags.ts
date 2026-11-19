/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Tags on every job a VAMS pipeline starts: AWS Batch jobs, Amazon ECS tasks and (as a label)
 * Kubernetes Jobs.
 *
 * Two tags, both fixed at synth time:
 *
 * - `vams:stackname`: the deployment's core stack name, the value the core stack already tags its
 *   CloudFormation resources with. Several deployments can share an account and Region, and job ARNs
 *   share one space there, so the deployment has to be part of any match.
 * - `vams:pipeline`: the pipeline that started the job, one value per pipeline construct.
 *
 * They exist so a later release can scope each pipeline role's `batch:TerminateJob` (and
 * `ecs:StopTask`) to its own deployment's and pipeline's jobs with `aws:ResourceTag` conditions. That
 * release imports the same constants, so the submit and the condition render the identical strings.
 * Nothing here restricts a permission yet.
 *
 * Tagging on create is authorized as a second action: AWS Batch evaluates `batch:TagResource` when
 * `SubmitJob` carries tags, so each submitting principal gets a grant that allows only these two tags
 * with its pipeline's values, on jobs. Amazon ECS tasks get the tags by propagation from their task
 * definition, which needs no grant ({@link tagEcsTaskDefinition}). A Kubernetes Job gets the
 * `vams:pipeline` value as a label ({@link pipelineJobLabels}).
 */

import { Stack, Tags } from "aws-cdk-lib";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../config/config";
import * as ServiceHelper from "../../../helper/service-helper";

/** Tag key naming the deployment. The same key the core stack tags its own resources with. */
export const VAMS_STACK_NAME_TAG_KEY = "vams:stackname";

/** Tag key naming the pipeline that started the job. */
export const VAMS_PIPELINE_TAG_KEY = "vams:pipeline";

/**
 * Kubernetes label key carrying the `vams:pipeline` value. A label key's name part cannot contain
 * `:`, so the label uses the `vams` prefix form instead.
 */
export const VAMS_PIPELINE_LABEL_KEY = "vams/pipeline";

/**
 * Environment variable carrying the tags, as a JSON object, to a Lambda that submits a job itself.
 */
export const PIPELINE_JOB_TAGS_ENVIRONMENT_VARIABLE = "PIPELINE_JOB_TAGS";

/**
 * The `vams:pipeline` value of each pipeline that starts jobs. Keep each value stable: a later
 * release matches IAM conditions on it. Every value is also a valid Kubernetes label value
 * (letters and digits only, under 63 characters).
 */
export const VAMS_PIPELINE_TAG_VALUES = {
    preview3dThumbnail: "preview3dThumbnail",
    pcPotreeViewer: "pcPotreeViewer",
    imageTo3d: "imageTo3d",
    videoSopBom: "videoSopBom",
    splatToolbox: "splatToolbox",
    metadata3dLabeling: "metadata3dLabeling",
    nvidiaCosmos3: "nvidiaCosmos3",
    nvidiaCosmosPredict: "nvidiaCosmosPredict",
    nvidiaCosmosReason: "nvidiaCosmosReason",
    nvidiaCosmosTransfer: "nvidiaCosmosTransfer",
    nvidiaGr00tFinetune: "nvidiaGr00tFinetune",
    coordinateTransform: "coordinateTransform",
    cadStepAgent: "cadStepAgent",
    isaacLabTraining: "isaacLabTraining",
    zipArchive: "zipArchive",
    rapidPipelineEcs: "rapidPipelineEcs",
    modelOps: "modelOps",
    rapidPipelineEks: "rapidPipelineEks",
} as const;

export type VamsPipelineTagValue =
    (typeof VAMS_PIPELINE_TAG_VALUES)[keyof typeof VAMS_PIPELINE_TAG_VALUES];

/** The tags one pipeline puts on its jobs. */
export type PipelineJobTags = Readonly<Record<string, string>>;

/** The two job tags of `pipeline` in this deployment. */
export function pipelineJobTags(
    config: Config.Config,
    pipeline: VamsPipelineTagValue
): PipelineJobTags {
    const stackName = config.env.coreStackName;
    if (!stackName) {
        throw new Error(
            `pipelineJobTags(${pipeline}): config.env.coreStackName is not set, so the ${VAMS_STACK_NAME_TAG_KEY} job tag has no value`
        );
    }
    return {
        [VAMS_STACK_NAME_TAG_KEY]: stackName,
        [VAMS_PIPELINE_TAG_KEY]: pipeline,
    };
}

/** The Kubernetes labels for a pipeline's jobs on Amazon EKS (`vams/pipeline` only; see the key). */
export function pipelineJobLabels(pipeline: VamsPipelineTagValue): PipelineJobTags {
    return { [VAMS_PIPELINE_LABEL_KEY]: pipeline };
}

/** Conditions that allow exactly these tags, with these values, in a tag-on-create request. */
function requestTagConditions(tags: PipelineJobTags): Record<string, unknown> {
    const requestTags: Record<string, string> = {};
    for (const [key, value] of Object.entries(tags)) {
        requestTags[`aws:RequestTag/${key}`] = value;
    }
    return {
        StringEquals: requestTags,
        "ForAllValues:StringEquals": { "aws:TagKeys": Object.keys(tags) },
    };
}

/**
 * `batch:TagResource` for the tags `SubmitJob` carries: on jobs in this account and Region (a job id
 * is generated at submit, so `job/*` is the narrowest ARN), and only for these two tags with this
 * pipeline's values.
 */
export function batchJobTaggingStatement(
    scope: Construct,
    tags: PipelineJobTags
): iam.PolicyStatement {
    const stack = Stack.of(scope);
    return new iam.PolicyStatement({
        actions: ["batch:TagResource"],
        resources: [
            `arn:${ServiceHelper.Partition()}:batch:${stack.region}:${stack.account}:job/*`,
        ],
        conditions: requestTagConditions(tags),
    });
}

const BATCH_JOB_TAGGING_NAG_REASON =
    "batch:TagResource is evaluated when SubmitJob carries tags. A job id is generated at submit, so job/* in this account and Region is the narrowest ARN; conditions on aws:RequestTag and aws:TagKeys allow only the vams:stackname and vams:pipeline tags with this pipeline's own values.";

/** cdk-nag `appliesTo` entry for the `job/*` resource of {@link batchJobTaggingStatement}. */
export const BATCH_JOB_TAGGING_NAG_APPLIES_TO = {
    regex: "/^Resource::arn:(?:aws(?:-[a-z-]+)?|<AWS::Partition>):batch:(?:[a-z0-9-]+|<AWS::Region>):(?:\\d{12}|<AWS::AccountId>):job\\/\\*$/g",
};

/**
 * Lets a Lambda that submits an AWS Batch job itself tag it: the tags as
 * `PIPELINE_JOB_TAGS` (JSON), and {@link batchJobTaggingStatement} on its role.
 */
export function configureLambdaBatchJobTagging(fn: lambda.Function, tags: PipelineJobTags): void {
    fn.addEnvironment(PIPELINE_JOB_TAGS_ENVIRONMENT_VARIABLE, JSON.stringify(tags));
    fn.addToRolePolicy(batchJobTaggingStatement(fn, tags));
    NagSuppressions.addResourceSuppressions(
        fn,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: BATCH_JOB_TAGGING_NAG_REASON,
                appliesTo: [BATCH_JOB_TAGGING_NAG_APPLIES_TO],
            },
        ],
        true
    );
}

export interface TaggedBatchSubmitJobProps extends Omit<tasks.BatchSubmitJobProps, "tags"> {
    /** The pipeline's job tags, from {@link pipelineJobTags}. */
    readonly jobTags: PipelineJobTags;
}

/**
 * A BatchSubmitJob that tags its job with the pipeline's job tags and grants the state machine role
 * the `batch:TagResource` that tagging needs. aws-cdk-lib renders `tags` as the SubmitJob `Tags`
 * parameter but grants only `batch:SubmitJob`, so a tagged submit without this grant is refused.
 *
 * The grant is not wildcard-free: the state machine's own construct suppresses AwsSolutions-IAM5
 * for {@link BATCH_JOB_TAGGING_NAG_APPLIES_TO} with {@link suppressBatchJobTaggingNag}.
 */
export class TaggedBatchSubmitJob extends tasks.BatchSubmitJob {
    private readonly jobTags: PipelineJobTags;

    constructor(scope: Construct, id: string, props: TaggedBatchSubmitJobProps) {
        const { jobTags, ...submitJobProps } = props;
        super(scope, id, { ...submitJobProps, tags: { ...jobTags } });
        this.jobTags = jobTags;
    }

    protected whenBoundToGraph(graph: sfn.StateGraph): void {
        super.whenBoundToGraph(graph);
        graph.registerPolicyStatement(batchJobTaggingStatement(this, this.jobTags));
    }
}

/** Suppresses AwsSolutions-IAM5 for the `job/*` tagging grant on a state machine and its role. */
export function suppressBatchJobTaggingNag(stateMachine: Construct): void {
    NagSuppressions.addResourceSuppressions(
        stateMachine,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: BATCH_JOB_TAGGING_NAG_REASON,
                appliesTo: [BATCH_JOB_TAGGING_NAG_APPLIES_TO],
            },
        ],
        true
    );
}

/**
 * Tags an Amazon ECS task definition with the pipeline's job tags. The pipeline's RunTask state
 * propagates the task definition's tags to each task it starts (`propagatedTagSource:
 * TASK_DEFINITION`, as the RapidPipeline and ModelOps states already set), so every task carries
 * them. Propagation sends no tags in the RunTask request, so no `ecs:TagResource` grant is needed;
 * the same propagation already carries the core stack's own tags to these tasks. Sending the tags in
 * RunTask as well would repeat keys the task definition already propagates, and a task's tag keys
 * must be unique.
 */
export function tagEcsTaskDefinition(
    taskDefinition: ecs.TaskDefinition,
    tags: PipelineJobTags
): void {
    for (const [key, value] of Object.entries(tags)) {
        Tags.of(taskDefinition).add(key, value, {
            includeResourceTypes: ["AWS::ECS::TaskDefinition"],
        });
    }
}

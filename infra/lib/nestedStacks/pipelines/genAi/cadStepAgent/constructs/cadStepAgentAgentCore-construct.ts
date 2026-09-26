/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as bedrockagentcore from "aws-cdk-lib/aws-bedrockagentcore";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../../config/config";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { generateUniqueNameHash } from "../../../../../helper/security";

export interface CadStepAgentAgentCoreConstructProps extends cdk.StackProps {
    config: Config.Config;
    /** The role the runtime's container assumes; the S3, Bedrock, Step Functions and secret grants are on it. */
    executionRole: iam.Role;
    /**
     * The CodeBuild-built arm64 image (repository + content-addressed tag) and the custom resource that
     * completes once that tag has been pushed. AgentCore validates the image when the runtime is created.
     */
    image: { repository: ecr.IRepository; tag: string; build: cdk.CustomResource };
    /** Environment the agent container reads (model ids, secret ARN, region); never a credential value. */
    environment: { [key: string]: string };
}

/**
 * Amazon Bedrock AgentCore Runtime hosting the CAD STEP agent container.
 *
 * The service provisions the runtime's `DEFAULT` endpoint together with the runtime itself, so no
 * endpoint resource is declared here; the invoke Lambda addresses the runtime through that qualifier.
 * The runtime is created in PUBLIC network mode: the agent's research tools reach the internet directly,
 * and the runtime's own calls to Amazon Bedrock, Amazon S3 and AWS Step Functions travel over the
 * service's managed network. The lifecycle configuration is what "warm sessions" means here: a runtime
 * session stays alive for `idleRuntimeSessionTimeoutSeconds` between invocations, up to
 * `maxLifetimeSeconds`, so the fixed session ids the invoke Lambda hands out land on a warm container.
 */
export class CadStepAgentAgentCoreConstruct extends Construct {
    public readonly runtime: bedrockagentcore.CfnRuntime;
    public readonly runtimeArn: string;

    constructor(parent: Construct, name: string, props: CadStepAgentAgentCoreConstructProps) {
        super(parent, name);

        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;
        const cad = props.config.app.pipelines.useGenAiCadStepAgent;

        // The runtime name takes letters, digits and underscores only, and is unique per deployment
        // through the stack-derived hash so two stacks in one account do not collide.
        const runtimeName =
            "vams_cad_step_agent_" +
            generateUniqueNameHash(
                props.config.env.coreStackName,
                props.config.env.account,
                "CadStepAgentAgentCoreRuntime",
                10
            ).replace(/[^A-Za-z0-9_]/g, "");

        // The runtime pulls its image and writes its logs under the execution role; the pipeline's own
        // grants (asset buckets, Bedrock model, task callbacks, OpenAI secret) are attached by the parent.
        props.image.repository.grantPull(props.executionRole);
        props.executionRole.addToPolicy(
            new iam.PolicyStatement({
                actions: ["ecr:GetAuthorizationToken"],
                resources: ["*"],
            })
        );
        props.executionRole.addToPolicy(
            new iam.PolicyStatement({
                actions: [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                ],
                resources: [
                    `arn:${ServiceHelper.Partition()}:logs:${region}:${account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
                ],
            })
        );
        // The runtime emits its own operational telemetry through these; both are account-level.
        props.executionRole.addToPolicy(
            new iam.PolicyStatement({
                actions: ["cloudwatch:PutMetricData"],
                resources: ["*"],
                conditions: { StringEquals: { "cloudwatch:namespace": "bedrock-agentcore" } },
            })
        );
        props.executionRole.addToPolicy(
            new iam.PolicyStatement({
                actions: ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                resources: ["*"],
            })
        );
        props.executionRole.addToPolicy(
            new iam.PolicyStatement({
                actions: ["bedrock-agentcore:GetWorkloadAccessToken"],
                resources: [
                    `arn:${ServiceHelper.Partition()}:bedrock-agentcore:${region}:${account}:workload-identity-directory/default`,
                    `arn:${ServiceHelper.Partition()}:bedrock-agentcore:${region}:${account}:workload-identity-directory/default/workload-identity/${runtimeName}-*`,
                ],
            })
        );

        this.runtime = new bedrockagentcore.CfnRuntime(this, "Runtime", {
            agentRuntimeName: runtimeName,
            description: "VAMS GenAI CAD STEP agent (Strands) runtime",
            agentRuntimeArtifact: {
                containerConfiguration: {
                    containerUri: `${props.image.repository.repositoryUri}:${props.image.tag}`,
                },
            },
            networkConfiguration: { networkMode: "PUBLIC" },
            protocolConfiguration: "HTTP",
            roleArn: props.executionRole.roleArn,
            environmentVariables: props.environment,
            lifecycleConfiguration: {
                idleRuntimeSessionTimeout: cad.agentCore.idleRuntimeSessionTimeoutSeconds,
                maxLifetime: cad.agentCore.maxLifetimeSeconds,
            },
        });
        // The trust policy must exist before the runtime validates it.
        this.runtime.node.addDependency(props.executionRole);
        // The image tag must exist in the repository before the runtime validates it, on creation and
        // on every update that names a new tag.
        this.runtime.node.addDependency(props.image.build);

        this.runtimeArn = this.runtime.attrAgentRuntimeArn;

        NagSuppressions.addResourceSuppressions(
            props.executionRole,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "The AgentCore runtime's log streams, ECR authorization token, X-Ray segments and namespaced CloudWatch metrics are account-level or runtime-generated resources that cannot be named ahead of time.",
                    appliesTo: [
                        "Resource::*",
                        `Resource::arn:<AWS::Partition>:logs:${region}:${account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
                        {
                            regex: "/^Resource::arn:<AWS::Partition>:bedrock-agentcore:.*workload-identity/.*$/g",
                        },
                    ],
                },
            ],
            true
        );
    }
}

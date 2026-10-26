/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Peer, Port } from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Duration, Stack, Names, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import {
    buildConstructPipelineFunction,
    buildOpenPipelineFunction,
    buildVamsExecuteRapidPipelineFunction,
    buildPipelineEndFunction,
} from "../lambdaBuilder/rapidPipelineFunctions";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as ServiceHelper from "../../../../../helper/service-helper";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import { Service } from "../../../../../helper/service-helper";
import * as Config from "../../../../../../config/config";
import {
    NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
    generateUniqueNameHash,
} from "../../../../../helper/security";
import { kmsKeyPolicyStatementGenerator } from "../../../../../helper/security";
import { grantExternalAssetBucketKmsKeys } from "../../../../../helper/security";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";

import { LogGroup, RetentionDays } from "aws-cdk-lib/aws-logs"; // remove once ECS cluster is moved to VAMS vpc

export interface RapidPipelineConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnetsPrivate: ec2.ISubnet[];
    pipelineSubnetsIsolated: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowV2FunctionName: string;
}

/**
 * Default input properties
 */
const defaultProps: Partial<RapidPipelineConstructProps> = {
    //stackName: "",
    //env: {},
};

/**
 * An EcsRunTask whose RunTask call also sets the task's group to the Step Functions execution name.
 *
 * Step Functions stops the Fargate task as the state reaches its task-level timeout, but its
 * documentation does not list that case, and the timed-out state's error output carries no task
 * ARN. Labelling the task with the execution name is what lets the timeout route find a task still
 * running: the route lists the cluster's running tasks, describes them, and stops the one whose
 * group is this execution's name. aws-cdk-lib's EcsRunTask has no group prop, so this
 * adds the field to the rendered Parameters and leaves everything else EcsRunTask renders and grants
 * as it is. The RunTask API reference documents no length or character limit for group; the Amazon
 * ECS Developer Guide ("Group related Amazon ECS tasks") limits a task group name to 255 characters.
 * openPipeline names each execution `PipelineJob_<timestamp>_<8 hex>`, within Step Functions'
 * 80-character execution name limit, and without the `:` of the `family:` and `service:` groups ECS
 * gives tasks by default.
 */
class GroupLabelledEcsRunTask extends tasks.EcsRunTask {
    public toStateJson(topLevelQueryLanguage?: sfn.QueryLanguage): object {
        const state = super.toStateJson(topLevelQueryLanguage) as {
            Parameters?: Record<string, unknown>;
        };
        if (state.Parameters === undefined) {
            // A JSONata state renders Arguments instead, which takes a different expression syntax.
            throw new Error(
                "RapidPipelineRunFargate rendered no Parameters; the Group label needs the JSONPath query language"
            );
        }
        return {
            ...state,
            Parameters: { ...state.Parameters, "Group.$": "$$.Execution.Name" },
        };
    }
}

/**
 * Deploys a Step Function for ECS RunTask workflow
 * Creates:
 * - SFN
 * - ECS
 * - IAM Roles / Policy Documents for permissions to S3 / Lambda
 * On redeployment, will automatically invalidate the CloudFront distribution cache
 */
export class RapidPipelineConstruct extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;
    constructor(parent: Construct, name: string, props: RapidPipelineConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        // const vpcSubnets = props.vpc.selectSubnets({
        //     subnets: props.pipelineSubnets,
        // });

        /**
         * ECS Task Resources
         */
        const inputBucketPolicy = new iam.PolicyDocument({
            statements: [
                // Add permissions for all asset buckets from the global array
                ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                    const prefix = record.prefix || "/";
                    // Build the object-level resource as {bucketArn}/{prefix}*. Strip any
                    // leading slash from the prefix so the '/' separator after the bucket
                    // ARN is always present (root prefix yields {bucketArn}/*).
                    const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
                    const objectPrefix = normalizedPrefix.replace(/^\/+/, "");

                    return new iam.PolicyStatement({
                        effect: iam.Effect.ALLOW,
                        actions: [
                            "s3:PutObject",
                            "s3:GetObject",
                            "s3:ListBucket",
                            "s3:DeleteObject",
                            "s3:GetObjectVersion",
                        ],
                        resources: [
                            record.bucket.bucketArn,
                            `${record.bucket.bucketArn}/${objectPrefix}*`,
                        ],
                    });
                }),
            ],
        });

        const outputBucketPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    resources: [
                        props.storageResources.s3.assetAuxiliaryBucket.bucketArn,
                        `${props.storageResources.s3.assetAuxiliaryBucket.bucketArn}/*`,
                    ],
                }),
                new iam.PolicyStatement({
                    actions: ["s3:ListBucket"],
                    resources: [props.storageResources.s3.assetAuxiliaryBucket.bucketArn],
                }),
            ],
        });

        //Add KMS key use if provided
        if (props.storageResources.encryption.kmsKey) {
            inputBucketPolicy.addStatements(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );

            outputBucketPolicy.addStatements(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
        }

        const stateTaskPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
                    resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
                }),
            ],
        });

        // Allows AWS Marketplace to track container usage
        const marketplaceUsagePolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["aws-marketplace:RegisterUsage", "aws-marketplace:MeterUsage"],
                    resources: ["*"],
                }),
            ],
        });

        const containerExecutionRole = new iam.Role(this, "RapidPipelineContainerExecutionRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: {
                InputBucketPolicy: inputBucketPolicy,
                OutputBucketPolicy: outputBucketPolicy,
                StateTaskPolicy: stateTaskPolicy,
                MarketplaceUsagePolicy: marketplaceUsagePolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        const containerJobRole = new iam.Role(this, "RapidPipelineContainerJobRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: {
                InputBucketPolicy: inputBucketPolicy,
                OutputBucketPolicy: outputBucketPolicy,
                StateTaskPolicy: stateTaskPolicy,
                MarketplaceUsagePolicy: marketplaceUsagePolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        // Grant access to any external asset bucket customer managed KMS keys so the
        // container can read/write objects in cross-account encrypted buckets
        // (no-op when no external keys are configured)
        grantExternalAssetBucketKmsKeys(containerJobRole);

        /**
         * SFN States
         */

        // connect pipeline lambda function
        // transforms data input for AWS ECS RunTask
        const constructPipelineFunction = buildConstructPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.config,
            props.vpc,
            props.pipelineSubnetsIsolated,
            props.pipelineSecurityGroups,
            props.storageResources.s3.assetAuxiliaryBucket,
            props.storageResources.encryption.kmsKey
        );

        // creates pipeline definition based on event notification input
        const constructPipelineTask = new tasks.LambdaInvoke(this, "ConstructPipelineTask", {
            lambdaFunction: constructPipelineFunction,
            outputPath: "$.Payload",
        });

        // end state: success
        const successState = new sfn.Succeed(this, "SuccessState", {
            comment: "Pipeline returned SUCCESS",
        });

        // end state: failure
        const failState = new sfn.Fail(this, "FailState", {
            causePath: sfn.JsonPath.stringAt("$.error.Cause"),
            errorPath: sfn.JsonPath.stringAt("$.error.Error"),
        });

        // end state evaluation: success or failure
        const endStatesChoice = new sfn.Choice(this, "EndStatesChoice")
            .when(sfn.Condition.isPresent("$.error"), failState)
            .otherwise(successState);

        // final lambda called on pipeline end to close out the statemachine run
        const pipelineEndFunction = buildPipelineEndFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources.s3.assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnetsIsolated,
            props.pipelineSecurityGroups,
            props.storageResources.encryption.kmsKey
        );

        const pipeLineEndTask = new tasks.LambdaInvoke(this, "PipelineEndTask", {
            lambdaFunction: pipelineEndFunction,
            inputPath: "$",
            outputPath: "$.Payload",
        }).next(endStatesChoice);

        // error handler passthrough - from RapidPipeline ECS Task
        const handleRapidPipelineError = new sfn.Pass(this, "HandleRapidPipelineError", {
            resultPath: "$",
        }).next(pipeLineEndTask);

        // ConstructPipelineTask is the first state, so a failure there ends the execution before
        // PipelineEndTask runs -- and PipelineEndTask is the only state that reports on the parent
        // workflow's callback token, which then pends for its full taskTimeout. The handler reports the
        // token for the errors it raises itself; this covers the failures where it never runs at all:
        // the function timeout, an out-of-memory kill, an import failure, or an invoke fault that
        // exhausts the task's service-exception retries.
        const handleConstructPipelineError = new sfn.Pass(this, "HandleConstructPipelineError", {
            resultPath: "$",
        }).next(pipeLineEndTask);

        // resultPath keeps the state and appends the error, so pipelineEnd still finds
        // externalSfnTaskToken alongside it.
        constructPipelineTask.addCatch(handleConstructPipelineError, {
            resultPath: "$.error",
        });

        /**
         * RapidPipeline Container Setup
         */

        const containerName =
            "RapidPipelineContainer" +
            generateUniqueNameHash(
                props.config.env.coreStackName,
                props.config.env.account,
                "VAMS-ECR-Container",
                10
            );

        // Note: temporary resource until cluster is moved to VAMS vpc
        // const vpcLogsGroups = new LogGroup(this, "CloudWatchVAMSVpc", {
        //     logGroupName:
        //         "/aws/vendedlogs/VAMSCloudWatchVPCLogs" +
        //         generateUniqueNameHash(
        //             props.config.env.coreStackName,
        //             props.config.env.account,
        //             "VAMSCloudWatchVPCLogsRapidPipeline",
        //             10
        //         ),
        //     retention: RetentionDays.ONE_YEAR,
        //     removalPolicy: cdk.RemovalPolicy.DESTROY,
        // });

        // Note: temporary resource until cluster is moved to VAMS vpc
        // const vpc = new ec2.Vpc(this, "vpc", {
        //     subnetConfiguration: [{
        //           cidrMask: 24,
        //           name: 'public',
        //           subnetType: ec2.SubnetType.PUBLIC,
        //     },
        //     {
        //           cidrMask: 24,
        //           name: 'private',
        //           subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        //     },
        // ],
        //     flowLogs: {
        //         "vpc-logs": {
        //             destination: ec2.FlowLogDestination.toCloudWatchLogs(vpcLogsGroups),
        //             trafficType: ec2.FlowLogTrafficType.ALL,
        //         },
        //     },
        // })

        const containerImage = ecs.ContainerImage.fromRegistry(
            props.config.app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI
        );

        // Overriding cluster name bc AWS Marketplace team is having issues with long cluster names
        const cluster = new ecs.Cluster(this, "RapidPipelineEcsCluster", {
            clusterName:
                "rapid-cluster" +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "VAMS-ECS-Cluster",
                    10
                ),
            // vpc, // temporarily placed in separate VPC with public subnet until VAMS vpc can handle private subnet with egress
            vpc: props.vpc,
            containerInsights: true,
        });

        const logGroup = new cdk.aws_logs.LogGroup(this, "RapidPipelineLogGroup", {
            // Encrypted with the shared VAMS CMK when the deployment enables one; undefined
            // leaves the CloudWatch Logs AWS-managed key.
            encryptionKey: props.storageResources.encryption.kmsKey,
            logGroupName: "/aws/vendedlogs/Pipelines/" + containerName,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            retention: logs.RetentionDays.ONE_MONTH,
        });

        const taskDefinition = new ecs.FargateTaskDefinition(this, "RapidPipelineTaskDefinition", {
            executionRole: containerExecutionRole,
            taskRole: containerJobRole,
            memoryLimitMiB: 16384, // 16 GB
            cpu: 2048, // 2 vCPU
        });

        const containerDefinition = taskDefinition.addContainer(
            "RapidPipelineContainerDefinition",
            {
                image: containerImage,
                logging: ecs.LogDrivers.awsLogs({
                    logGroup: logGroup,
                    streamPrefix: "ecs",
                }),
                memoryLimitMiB: 16384,
                cpu: 2048,
            }
        );

        // ECS cluster needs to be in private subnet (with egress) to connect to AWS Marketplace API
        const subnetSelection: ec2.SubnetSelection = {
            subnets: props.pipelineSubnetsPrivate,
        };

        // Note: temporary resource until cluster is moved to VAMS vpc
        // const securityGroup = new cdk.aws_ec2.SecurityGroup(this, "RapidPipelineTaskRunSG", { vpc: props.vpc, allowAllOutbound: true });
        // securityGroup.addIngressRule(Peer.anyIpv4(), Port.tcp(443), "Allow HTTPS access");

        /**
         * Timeout route: stop a Fargate task that is still running at its task-level timeout
         */

        // A .sync ECS state that reaches its TimeoutSeconds fails with States.Timeout, and its error
        // output carries no task ARN. Step Functions stops the task as it ends the state, but its
        // documentation does not list a state timeout among the cases where it stops a task, so
        // these states are a fallback that stops the task if it is still running. The Fargate state
        // labels the task with its group set to the execution name. ecs:ListTasks cannot filter by
        // group, so these states list the cluster's running tasks, describe them, keep the ARNs of
        // the task(s) whose group is this execution's name, stop those, and go on to
        // PipelineEndTask. By then the list often holds no task of this execution, because Step
        // Functions has already stopped it. Each call is bounded and best-effort: a failure is
        // caught into $.stopError and also goes on to PipelineEndTask, so $.error stays
        // States.Timeout and the parent workflow is told of the timeout whether or not the stop
        // succeeds. The bounds are counted in the chain in config.ts.
        //
        // One page only: ListTasks returns at most 100 ARNs per call, and DescribeTasks takes at most
        // 100. The cluster is this pipeline's own; when more than 100 of its tasks are running and
        // this run's task is not on the first page, or the listed tasks' descriptions exceed the
        // 256 KiB payload quota, these states do not stop the task. The parent workflow is told of
        // the timeout either way.
        const stopCallRetry: sfn.RetryProps = {
            // AWS SDK integration errors are named <service prefix>.<exception>; the ECS prefix is Ecs.
            errors: ["Ecs.ThrottlingException", "Ecs.ServerException"],
            interval: Duration.seconds(1),
            maxAttempts: Config.RAPID_PIPELINE_ECS_STOP_CALL_RETRIES,
            backoffRate: 2,
        };
        const stopCallCatch: sfn.CatchProps = { resultPath: "$.stopError" };

        const listTimedOutTask = new sfn.CustomState(this, "ListTimedOutTask", {
            stateJson: {
                Type: "Task",
                Comment:
                    "List the cluster's running tasks; the timed-out one is matched by group next",
                Resource: `arn:${ServiceHelper.Partition()}:states:::aws-sdk:ecs:listTasks`,
                TimeoutSeconds: Config.RAPID_PIPELINE_ECS_STOP_CALL_TIMEOUT_SECONDS,
                Parameters: {
                    Cluster: cluster.clusterArn,
                    DesiredStatus: "RUNNING",
                    MaxResults: Config.RAPID_PIPELINE_ECS_STOP_ROUTE_PAGE_SIZE,
                },
                // The whole response is kept: a ResultSelector path the response lacks would fail
                // the execution with an uncatchable States.Runtime before PipelineEndTask ran.
                ResultPath: "$.timedOutTasks",
            },
        })
            .addRetry(stopCallRetry)
            .addCatch(pipeLineEndTask, stopCallCatch);

        // JSONata catchers have no ResultPath; this Output does what ResultPath "$.stopError" does
        // for the JSONPath states: it keeps the state input and adds the error output beside it.
        // These catchers are written into the state JSON, because CustomState renders addCatch()
        // catchers in JSONPath form; PipelineEndTask is in the graph through the other routes.
        const keepInputAndAddStopError =
            "{% $merge([$states.input, {'stopError': $states.errorOutput}]) %}";
        const pipelineEndStateId = pipeLineEndTask.startState.stateId;

        // DescribeTasks is a JSONata state so that its response, up to 100 full task descriptions,
        // never becomes state data: its Output keeps the state input and adds only the ARNs of the
        // described tasks whose group is this execution's name, as a list that is empty when none is.
        // A JSONPath filter cannot compare with the execution name, and keeping the descriptions in
        // the state would carry up to 256 KiB to every later state. A task without a group is not
        // matched; an expression that cannot be evaluated raises States.QueryEvaluationError, which
        // the States.ALL catcher takes. States.ALL does not match States.DataLimitExceeded (a
        // response over the 256 KiB payload quota), so that error has a catcher of its own.
        const describeTimedOutTasks = new sfn.CustomState(this, "DescribeTimedOutTasks", {
            stateJson: {
                Type: "Task",
                QueryLanguage: "JSONata",
                Comment: "Keep the ARN of the listed task whose group is this execution's name",
                Resource: `arn:${ServiceHelper.Partition()}:states:::aws-sdk:ecs:describeTasks`,
                TimeoutSeconds: Config.RAPID_PIPELINE_ECS_STOP_CALL_TIMEOUT_SECONDS,
                Arguments: {
                    Cluster: cluster.clusterArn,
                    Tasks: "{% $states.input.timedOutTasks.TaskArns %}",
                },
                Output:
                    "{% $merge([$states.input, {'timedOutTaskArns': " +
                    "[$states.result.Tasks[Group = $states.context.Execution.Name].TaskArn]}]) %}",
                Catch: [
                    {
                        ErrorEquals: ["States.DataLimitExceeded"],
                        Output: keepInputAndAddStopError,
                        Next: pipelineEndStateId,
                    },
                    {
                        ErrorEquals: ["States.ALL"],
                        Output: keepInputAndAddStopError,
                        Next: pipelineEndStateId,
                    },
                ],
            },
        }).addRetry(stopCallRetry);

        const stopTimedOutTask = new sfn.CustomState(this, "StopTimedOutTask", {
            stateJson: {
                Type: "Task",
                Comment: "Stop the timed-out Fargate task",
                Resource: `arn:${ServiceHelper.Partition()}:states:::aws-sdk:ecs:stopTask`,
                TimeoutSeconds: Config.RAPID_PIPELINE_ECS_STOP_CALL_TIMEOUT_SECONDS,
                Parameters: {
                    Cluster: cluster.clusterArn,
                    "Task.$": "$.taskArn",
                    Reason: "VAMS RapidPipeline task timeout",
                },
                ResultPath: null,
            },
        }).addRetry(stopCallRetry);

        // A failed iteration fails the Map, whose catch takes the run on to PipelineEndTask. The
        // iterations run concurrently; the group is unique to the execution, so there is one at most.
        const stopTimedOutTasks = new sfn.Map(this, "StopTimedOutTasks", {
            comment: "Stop the task DescribeTimedOutTasks matched to this execution",
            // Always present when this state runs: DescribeTimedOutTasks' Output sets it to a list.
            itemsPath: sfn.JsonPath.stringAt("$.timedOutTaskArns"),
            itemSelector: { taskArn: sfn.JsonPath.stringAt("$$.Map.Item.Value") },
            resultPath: sfn.JsonPath.DISCARD,
        });
        stopTimedOutTasks.itemProcessor(stopTimedOutTask);
        stopTimedOutTasks.addCatch(pipeLineEndTask, stopCallCatch).next(pipeLineEndTask);
        describeTimedOutTasks.next(stopTimedOutTasks);

        // DescribeTasks rejects an empty task list (InvalidParameterException), so it runs only when
        // ListTasks listed a task. With none running there is nothing to stop, and the run goes
        // straight on to PipelineEndTask without a $.stopError.
        listTimedOutTask.next(
            new sfn.Choice(this, "TimedOutTaskListed")
                .when(sfn.Condition.isPresent("$.timedOutTasks.TaskArns[0]"), describeTimedOutTasks)
                .otherwise(pipeLineEndTask)
        );

        // Step function task for ECS. The .sync pattern waits for the task to stop however long it
        // runs, so the state carries a task-level timeout: a task that reaches it fails the state with
        // States.Timeout, which the first catch below routes through the timeout route above to
        // PipelineEndTask, and the run fits in the state machine timeout (the chain in config.ts). The
        // task is started with its group set to the execution name, which is how that route finds it.
        const runRapidPipelineJob = new GroupLabelledEcsRunTask(this, "RapidPipelineRunFargate", {
            integrationPattern: sfn.IntegrationPattern.RUN_JOB,
            taskTimeout: sfn.Timeout.duration(
                Duration.seconds(Config.RAPID_PIPELINE_ECS_FARGATE_TASK_TIMEOUT_SECONDS)
            ),
            cluster,
            taskDefinition,
            assignPublicIp: false,
            containerOverrides: [
                {
                    containerDefinition,
                    command: sfn.JsonPath.listAt("$.commands"),
                    environment: [
                        {
                            name: "externalSfnTaskToken",
                            value: sfn.JsonPath.stringAt("$.externalSfnTaskToken"),
                        },
                    ],
                },
            ],
            launchTarget: new tasks.EcsFargateLaunchTarget(),
            propagatedTagSource: ecs.PropagatedTagSource.TASK_DEFINITION,
            // securityGroups: [securityGroup], // temporary until ECS cluster is moved to VAMS vpc
            securityGroups: props.pipelineSecurityGroups,
            subnets: subnetSelection,
        })
            // Catchers match in order, and the general catch matches States.Timeout too, so the
            // timeout catch must render first. CDK renders a States.ALL catcher last whatever the
            // addCatch order; ecsTimeoutStopTask.test.ts pins the emitted order. Both keep the state
            // input and add the error at $.error.
            .addCatch(listTimedOutTask, {
                errors: [sfn.Errors.TIMEOUT],
                resultPath: "$.error",
            })
            .addCatch(handleRapidPipelineError, {
                resultPath: "$.error",
            })
            .next(pipeLineEndTask);

        /**
         * SFN Definition
         */
        const sfnPipelineDefinition = sfn.Chain.start(
            constructPipelineTask.next(runRapidPipelineJob)
        );

        /**
         * CloudWatch Log Group
         */
        const stateMachineLogGroup = new logs.LogGroup(
            this,
            "RapidPipelineProcessing-StateMachineLogGroup",
            {
                encryptionKey: props.storageResources.encryption.kmsKey,
                logGroupName:
                    "/aws/vendedlogs/VAMSStateMachine-RapidPipeline" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "RapidPipelineProcessing-StateMachineLogGroup",
                        10
                    ),
                retention: logs.RetentionDays.ONE_YEAR,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        /**
         * SFN State Machine
         */
        const pipelineStateMachine = new sfn.StateMachine(
            this,
            "RapidPipelineProcessing-StateMachine",
            {
                definitionBody: sfn.DefinitionBody.fromChainable(sfnPipelineDefinition),
                // The middle link of the timeout chain in config.ts: the Fargate task bound and the
                // two Lambda steps fit inside it, and the registered pipeline's taskTimeout outlasts it.
                timeout: Duration.seconds(Config.RAPID_PIPELINE_ECS_STATE_MACHINE_TIMEOUT_SECONDS),
                logs: {
                    destination: stateMachineLogGroup,
                    includeExecutionData: true,
                    level: sfn.LogLevel.ALL,
                },
                tracingEnabled: true,
            }
        );

        // The timeout route's three ECS calls. The RunTask state's generated policy is left as CDK
        // emits it; these statements are added beside it.
        //
        // ecs:ListTasks: its only resource type is a container instance (Service Authorization
        // Reference), and a Fargate cluster has none, so the action is evaluated against "*". The
        // ecs:cluster condition limits it to this pipeline's cluster.
        pipelineStateMachine.addToRolePolicy(
            new iam.PolicyStatement({
                sid: "ListTimedOutRapidPipelineTask",
                actions: ["ecs:ListTasks"],
                resources: ["*"],
                conditions: { ArnEquals: { "ecs:cluster": cluster.clusterArn } },
            })
        );
        // ecs:DescribeTasks and ecs:StopTask on this cluster's tasks, under the same condition. The
        // RunTask state's generated policy already holds both on "*" (Step Functions' .sync
        // integration uses them to watch and cancel a task), so these statements record what the
        // timeout route needs rather than narrowing what the role can do.
        const clusterTasksArn = `arn:${ServiceHelper.Partition()}:ecs:${region}:${account}:task/${
            cluster.clusterName
        }/*`;
        pipelineStateMachine.addToRolePolicy(
            new iam.PolicyStatement({
                sid: "DescribeTimedOutRapidPipelineTask",
                actions: ["ecs:DescribeTasks"],
                resources: [clusterTasksArn],
                conditions: { ArnEquals: { "ecs:cluster": cluster.clusterArn } },
            })
        );
        pipelineStateMachine.addToRolePolicy(
            new iam.PolicyStatement({
                sid: "StopTimedOutRapidPipelineTask",
                actions: ["ecs:StopTask"],
                resources: [clusterTasksArn],
                conditions: { ArnEquals: { "ecs:cluster": cluster.clusterArn } },
            })
        );

        /**
         * Lambda Resources
         */

        //Build Lambda Pipeline Resources to Open the Pipeline
        // ******  See RapidPipeline documentation for available file extensions *******
        const allowedInputFileExtensions = ".glb,.gltf,.fbx,.obj,.stl,.ply,.usd,.usdz,.dae,.abc";
        const openPipelineFunction = buildOpenPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources.s3.assetAuxiliaryBucket,
            pipelineStateMachine,
            allowedInputFileExtensions,
            props.config,
            props.vpc,
            props.pipelineSubnetsIsolated,
            props.storageResources.eventBridge.orchestrationBus,
            stateMachineLogGroup,
            logGroup,
            props.storageResources.encryption.kmsKey
        );

        //Build Lambda VAMS Execution Function (as an optional pipeline execution action)
        const rapidPipelineExecuteFunction = buildVamsExecuteRapidPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources.s3.assetAuxiliaryBucket,
            openPipelineFunction,
            props.config,
            props.vpc,
            props.pipelineSubnetsIsolated,
            props.storageResources.encryption.kmsKey
        );

        this.pipelineVamsLambdaFunctionName = rapidPipelineExecuteFunction.functionName;

        // Auto-register with VAMS (V2 vamsSchema bundle -> V2 pipeline/workflow/template tables). The
        // former per-output-format registrations (glb / gltf, with duplicates) collapse to ONE pipeline +
        // two templates (rapid-pipeline-to-glb / rapid-pipeline-to-gltf) selected per execution.
        if (props.config.app.pipelines.useRapidPipeline.useEcs.autoRegisterWithVAMS === true) {
            new VamsSchemaRegistration(this, "RapidPipelineRegistration", {
                importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
                artefactsBucket: props.storageResources.s3.artefactsBucket,
                vamsSchemaDir: path.join(
                    __dirname,
                    "..",
                    "..",
                    "..",
                    "..",
                    "..",
                    "..",
                    "..",
                    "backendPipelines",
                    "multi",
                    "rapidPipeline",
                    "vamsSchema"
                ),
                resourceOverrides: {
                    lambdaName: rapidPipelineExecuteFunction.functionName,
                },
                idOverrides: {
                    pipelineId: "rapid-pipeline",
                    workflowId: "rapid-pipeline",
                },
            });
        }

        //Output VAMS Pipeline Execution Function name
        new CfnOutput(this, "RapidPipelineLambdaExecutionFunctionName", {
            value: rapidPipelineExecuteFunction.functionName,
            description: "The RapidPipeline Lambda Function Name to use in a VAMS Pipeline",
        });

        //Nag Supressions
        const reason =
            "Intended Solution. The pipeline lambda functions need appropriate access to S3.";
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "/Action::s3:.*/g",
                        },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                            regex: "/^Resource::.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                            regex: "/^Resource::.*openPipeline/ServiceRole/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                            regex: "/^Resource::.*RapidPipelineProcessing-StateMachine/Role/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                            regex: "/^Resource::.*pipelineEnd/ServiceRole/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                            regex: "/^Resource::.*vamsExecuteRapidPipeline/ServiceRole/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerExecutionRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in the DataBucket",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerJobRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in the DataBucket",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/RapidPipelineProcessing-StateMachine/Role/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "PipelineProcessingStateMachine uses default policy that contains wildcard",
                    appliesTo: [
                        "Resource::*",
                        "Action::kms:GenerateDataKey*",
                        {
                            regex: "/^Resource::arn:<AWS::Partition>:batch:(?:[a-z0-9-]+|<AWS::Region>):(?:\\d{12}|<AWS::AccountId>):job-definition\\/\\*$/g",
                        },
                        {
                            regex: "/^Resource::<.*Function.*.Arn>:.*$/g",
                        },
                        {
                            regex: "/^Action::s3:.*$/g",
                        },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "ecs:ListTasks is granted on * because its only resource type is a container " +
                        "instance and a Fargate cluster has none; an ArnEquals ecs:cluster condition " +
                        "limits it to this pipeline's cluster. ecs:DescribeTasks and ecs:StopTask are " +
                        "scoped to this cluster's tasks (task/<cluster>/*) under the same condition, " +
                        "because the task ID is only known once the task runs. All three find and stop " +
                        "a Fargate task still running at its task-level timeout.",
                    appliesTo: [
                        "Resource::*",
                        {
                            regex: "/^Resource::arn:.+:ecs:.+:task\\/<RapidPipelineEcsCluster[A-Za-z0-9]+>\\/\\*$/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/openPipeline/ServiceRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "openPipeline requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "openPipeline uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/constructPipeline/ServiceRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "constructPipeline requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "openPipeline uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/vamsExecuteRapidPipeline/ServiceRole/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "vamsExecuteRapidPipeline requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "vamsExecuteRapidPipeline uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "vamsExecuteRapidPipeline uses default policy that contains wildcard",
                    appliesTo: [
                        "Action::kms:GenerateDataKey*",
                        {
                            regex: "/^Resource::<.*Function.*.Arn>:.*$/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/pipelineEnd/ServiceRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "pipelineEnd requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "pipelineEnd uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );

        // temporary until ECS cluster is moved into VAMS vpc
        // NagSuppressions.addResourceSuppressionsByPath(
        //     Stack.of(this),
        //     `/${this.toString()}/RapidPipelineTaskRunSG/Resource`,
        //     [
        //         {
        //             id: "AwsSolutions-EC23",
        //             reason: "Inbound is limited to port 443 (HTTPS).",
        //         },
        //     ],
        //     true
        // );
    }
}

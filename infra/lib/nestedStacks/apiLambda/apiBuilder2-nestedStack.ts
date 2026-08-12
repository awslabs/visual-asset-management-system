/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as iam from "aws-cdk-lib/aws-iam";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import { storageResources } from "../storage/storageBuilder-nestedStack";
import { generateUniqueNameHash } from "../../helper/security";
import { buildTagService, buildCreateTagFunction } from "../../lambdaBuilder/tagFunctions";
import {
    buildTagTypeService,
    buildCreateTagTypeFunction,
} from "../../lambdaBuilder/tagTypeFunctions";
import {
    buildAuthConstraintsFunction,
    buildAuthConstraintsTemplateFunction,
} from "../../lambdaBuilder/authFunctions";
import { buildAssetHistoryFunction } from "../../lambdaBuilder/assetFunctions";
import {
    buildDeadlineCloudJobCallbackFunction,
    buildExecuteWorkflowV2Function,
    buildWorkflowServiceV2Function,
    buildWorkflowTriggerServiceFunction,
    buildWorkflowTriggerDispatchFunction,
    buildImportGlobalPipelineWorkflowFunction,
    buildExecutionServiceFunction,
    buildProcessWorkflowExecutionOutputFunction,
    buildInterimPipelineTrackingFunction,
    buildHandleExecutionErrorFunction,
    buildRegisterPipelineExecutionFunction,
} from "../../lambdaBuilder/workflowFunctions";
import {
    buildPipelineServiceV2Function,
    buildPipelineTemplateServiceFunction,
} from "../../lambdaBuilder/pipelineFunctions";
import { RouteRegistry, attachFunctionToApi } from "./apiRouteRegistry";
import * as Config from "../../../config/config";

/**
 * Properties for the secondary API builder nested stack.
 */
export interface ApiBuilder2NestedStackProps {
    config: Config.Config;
    registry: RouteRegistry;
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    // Shared functions built in ApiBuilder that the workflow/execution lambdas here depend on: the
    // metadata service (invoked by execute + process-output) and the upload-file lambda (invoked by
    // process-output to write outputs back to the asset).
    metadataServiceFunction: lambda.Function;
    uploadFileFunction: lambda.Function;
}

/**
 * ApiBuilder2NestedStack
 *
 * Secondary backend API nested stack. The primary ApiBuilderNestedStack is approaching the
 * CloudFormation per-stack resource limit (500 resources), so some API domains are
 * relocated here to free up headroom. New API endpoints should be added to this stack going
 * forward until it too approaches the limit.
 *
 */
export class ApiBuilder2NestedStack extends NestedStack {
    // Name of the V2 vamsSchema import custom-resource lambda. Consumed by pipeline nested stacks to
    // register their built-in pipeline/workflow into the V2 tables at deploy (via VamsSchemaRegistration).
    public importGlobalPipelineWorkflowV2FunctionName = "";

    constructor(parent: Construct, name: string, props: ApiBuilder2NestedStackProps) {
        super(parent, name);

        const {
            config,
            registry,
            storageResources,
            lambdaCommonBaseLayer,
            vpc,
            subnets,
            metadataServiceFunction,
            uploadFileFunction,
        } = props;

        //Tags Resources
        const tagService = buildTagService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, tagService, {
            routePath: "/tags",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, tagService, {
            routePath: "/tags/{tagId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        const createTagFunction = buildCreateTagFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, createTagFunction, {
            routePath: "/tags",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, createTagFunction, {
            routePath: "/tags",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        //Tag Types Resources
        const tagTypeService = buildTagTypeService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, tagTypeService, {
            routePath: "/tag-types",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, tagTypeService, {
            routePath: "/tag-types/{tagTypeId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        const createTagTypeFunction = buildCreateTagTypeFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, createTagTypeFunction, {
            routePath: "/tag-types",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, createTagTypeFunction, {
            routePath: "/tag-types",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        // Auth constraints service and its routes (relocated here from ApiBuilder to keep
        // the primary stack under the CFN per-stack resource limit).
        const authConstraintsService = buildAuthConstraintsFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        // permissionObjects must be registered before the {constraintId} route so the
        // literal path is not captured by the {constraintId} template.
        attachFunctionToApi(this, authConstraintsService, {
            routePath: "/auth/constraints/permissionObjects",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, authConstraintsService, {
            routePath: "/auth/constraints",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        const constraintMethods = [
            apigateway.HttpMethod.GET,
            apigateway.HttpMethod.POST,
            apigateway.HttpMethod.PUT,
            apigateway.HttpMethod.DELETE,
        ];
        for (let i = 0; i < constraintMethods.length; i++) {
            attachFunctionToApi(this, authConstraintsService, {
                routePath: "/auth/constraints/{constraintId}",
                method: constraintMethods[i],
                registry: registry,
            });
        }

        const authConstraintsTemplateService = buildAuthConstraintsTemplateFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, authConstraintsTemplateService, {
            routePath: "/auth/constraintsTemplateImport",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        //Asset History Resources
        const assetHistoryFunction = buildAssetHistoryFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, assetHistoryFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/assetHistory",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        // ---------------------------------------------------------------------------
        // Workflow / execution Step Functions lambdas + the execution service.
        // ---------------------------------------------------------------------------
        // Single shared CloudWatch log group for all workflow Step Functions executions. Every
        // workflow state machine logs here (includeExecutionData enabled), so per-execution logs are
        // isolated by execution ARN/name within this one group. Shared by the execution service, the
        // process-output / interim / error-handler lambdas, and the workflow-service SFN deploy.
        const workflowsLogGroup = new logs.LogGroup(this, "vamsPipelineWorkflows", {
            logGroupName:
                "/aws/vendedlogs/vamsPipelineWorkflows" + //important to have 'vams' in the name as resource access looks for this
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "vamsPipelineWorkflowsV2",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        // Execution service: asset-scoped + global list, details/traceability, paged detail
        // metadata, logs, abort, abort-by-group, re-run, permanent delete.
        const executionServiceFunction = buildExecutionServiceFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            workflowsLogGroup,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/workflows/executions",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        // Abort a running workflow execution (execution-keyed; executions may span multiple assets).
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/workflows/executions/{executionId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/workflows/executions/{executionId}/details",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/workflows/executions/{executionId}/details/metadata",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/workflows/executions/{executionId}/logs",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        // Global execution ops on the same handler: asset-less list, re-run, permanent delete.
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/workflows/executions",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/workflows/executions/{executionId}/rerun",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/workflows/executions/{executionId}/permanent",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        // Workflow Step Functions execution lambdas wired into every workflow's generated ASL
        // (end-state process-output; interim pipeline-tracking between pipelines; error-handler
        // catch state). Their names are embedded in the ASL by the workflow-service SFN deploy.
        const processWorkflowExecutionOutputFunction = buildProcessWorkflowExecutionOutputFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            uploadFileFunction,
            metadataServiceFunction,
            workflowsLogGroup,
            config,
            vpc,
            subnets
        );
        const interimPipelineTrackingFunction = buildInterimPipelineTrackingFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            workflowsLogGroup,
            config,
            vpc,
            subnets
        );
        const handleExecutionErrorFunction = buildHandleExecutionErrorFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            workflowsLogGroup,
            config,
            vpc,
            subnets
        );

        // Pipeline sub-process registration lambda + its standing EventBridge rule on the
        // orchestration bus (pipelines optionally PutEvents to register sub-SFN / log ARNs).
        buildRegisterPipelineExecutionFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        // Pipeline V2 resources: create (POST), update (PUT), list/get/delete, and the template +
        // tag-schema sub-resources — the whole pipeline CRUD surface.
        const pipelineServiceV2 = buildPipelineServiceV2Function(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        // List all pipelines (Casbin-filtered).
        attachFunctionToApi(this, pipelineServiceV2, {
            routePath: "/pipelines",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        // Database-scoped collection: list (GET) + create (POST).
        for (const method of [apigateway.HttpMethod.GET, apigateway.HttpMethod.POST]) {
            attachFunctionToApi(this, pipelineServiceV2, {
                routePath: "/database/{databaseId}/pipelines",
                method,
                registry: registry,
            });
        }
        // Single pipeline: details (GET) + update (PUT) + archive (DELETE).
        for (const method of [
            apigateway.HttpMethod.GET,
            apigateway.HttpMethod.PUT,
            apigateway.HttpMethod.DELETE,
        ]) {
            attachFunctionToApi(this, pipelineServiceV2, {
                routePath: "/database/{databaseId}/pipelines/{pipelineId}",
                method,
                registry: registry,
            });
        }

        const pipelineTemplateService = buildPipelineTemplateServiceFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        for (const method of [apigateway.HttpMethod.GET, apigateway.HttpMethod.POST]) {
            attachFunctionToApi(this, pipelineTemplateService, {
                routePath: "/database/{databaseId}/pipelines/{pipelineId}/templates",
                method,
                registry: registry,
            });
        }
        for (const method of [
            apigateway.HttpMethod.GET,
            apigateway.HttpMethod.PUT,
            apigateway.HttpMethod.DELETE,
        ]) {
            attachFunctionToApi(this, pipelineTemplateService, {
                routePath: "/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}",
                method,
                registry: registry,
            });
        }
        for (const method of [apigateway.HttpMethod.GET, apigateway.HttpMethod.PUT]) {
            attachFunctionToApi(this, pipelineTemplateService, {
                routePath:
                    "/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}/tagSchema",
                method,
                registry: registry,
            });
        }

        // Workflow V2 resources: create (POST), update (PUT), list/get/delete, and the trigger
        // sub-resources — the whole workflow CRUD surface.
        const workflowServiceV2 = buildWorkflowServiceV2Function(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            processWorkflowExecutionOutputFunction,
            interimPipelineTrackingFunction,
            handleExecutionErrorFunction,
            workflowsLogGroup,
            config.env.coreStackName,
            config,
            vpc,
            subnets
        );
        // List all workflows (Casbin-filtered).
        attachFunctionToApi(this, workflowServiceV2, {
            routePath: "/workflows",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        // Database-scoped collection: list (GET) + create (POST).
        for (const method of [apigateway.HttpMethod.GET, apigateway.HttpMethod.POST]) {
            attachFunctionToApi(this, workflowServiceV2, {
                routePath: "/database/{databaseId}/workflows",
                method,
                registry: registry,
            });
        }
        // Single workflow: details (GET) + update (PUT) + archive (DELETE).
        for (const method of [
            apigateway.HttpMethod.GET,
            apigateway.HttpMethod.PUT,
            apigateway.HttpMethod.DELETE,
        ]) {
            attachFunctionToApi(this, workflowServiceV2, {
                routePath: "/database/{databaseId}/workflows/{workflowId}",
                method,
                registry: registry,
            });
        }

        // Asset-less multi-file execute handler (WB5.2). Serves the new asset-less execute route
        // that replaces the removed V1 asset-scoped execute route.
        const executeWorkflowV2 = buildExecuteWorkflowV2Function(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            metadataServiceFunction,
            workflowsLogGroup,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, executeWorkflowV2, {
            routePath: "/workflows/{workflowDatabaseId}/{workflowId}/execute",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Wire the execution service's re-run path: it invokes executeWorkflowV2 (as the calling
        // user) to launch a fresh execution reconstructed from the stored records. A scoped
        // InvokeFunction statement on the exact function ARN is used instead of grantInvoke() so the
        // policy carries no {functionArn}:* version wildcard (avoids an AwsSolutions-IAM5 finding on
        // the overflow policy this grant would otherwise tip the role's inline policy into).
        executionServiceFunction.addEnvironment(
            "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME",
            executeWorkflowV2.functionName
        );
        executionServiceFunction.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["lambda:InvokeFunction"],
                resources: [executeWorkflowV2.functionArn],
            })
        );

        // File-upload trigger dispatcher (WB5b): orchestration-bus rule -> SQS buffer -> this lambda,
        // which matches fileUpload triggers and invokes executeWorkflowV2 per firing workflow.
        buildWorkflowTriggerDispatchFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            executeWorkflowV2,
            config,
            vpc,
            subnets
        );

        const workflowTriggerService = buildWorkflowTriggerServiceFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, workflowTriggerService, {
            routePath: "/database/{databaseId}/workflows/{workflowId}/triggers",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        for (const method of [
            apigateway.HttpMethod.GET,
            apigateway.HttpMethod.PUT,
            apigateway.HttpMethod.DELETE,
        ]) {
            attachFunctionToApi(this, workflowTriggerService, {
                routePath: "/database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}",
                method,
                registry: registry,
            });
        }

        // V2 vamsSchema import custom-resource lambda (WB6). Registers built-in pipelines/workflows
        // into the V2 tables at deploy by upserting via SYSTEM_USER cross-calls to the four V2
        // service functions above. Pipeline nested stacks consume its name to wire a
        // VamsSchemaRegistration custom resource per built-in.
        const importGlobalPipelineWorkflow = buildImportGlobalPipelineWorkflowFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            pipelineServiceV2,
            pipelineTemplateService,
            workflowServiceV2,
            workflowTriggerService,
            config,
            vpc,
            subnets
        );
        this.importGlobalPipelineWorkflowV2FunctionName = importGlobalPipelineWorkflow.functionName;

        // Deadline Cloud job-callback lambda + its rules on the DEFAULT bus (Deadline
        // publishes job status events there). Resolves workflow task tokens for
        // DeadlineCloud pipeline task states and registers the job as the pipeline
        // execution's sub-process. EventBridge-invoked; no API route.
        if (config.app.pipelines.deadlineCloudExecutionTypeEnabled) {
            buildDeadlineCloudJobCallbackFunction(
                this,
                lambdaCommonBaseLayer,
                storageResources,
                config,
                vpc,
                subnets
            );
        }

        // Nag suppressions. Scoped with appliesTo so this does NOT blanket-waive every IAM5 wildcard
        // in the stack: it only covers the constraint/auth-table read wildcards and the CDK-generated
        // BucketNotifications/LogRetention custom-resource roles. Per-function log/S3 wildcards are
        // suppressed at their own builder (suppressCdkNagErrorsByGrantReadWrite(fun)).
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Auth/constraint-table reads and CDK-generated custom-resource roles use " +
                        "action/resource wildcards scoped to VAMS resources.",
                    appliesTo: [
                        { regex: "/Action::(dynamodb|logs|s3):.*/g" },
                        { regex: "/^Resource::.*/g" },
                    ],
                },
            ],
            true
        );
    }
}

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
import * as ssm from "aws-cdk-lib/aws-ssm";
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
import {
    buildAssetHistoryFunction,
    buildZipDownloadServiceFunction,
    buildZipDownloadSweep,
    grantZipDownloadLaunch,
    ZIP_DOWNLOAD_ROUTES,
    ZIP_DOWNLOAD_SYSTEM_TRIGGER,
} from "../../lambdaBuilder/assetFunctions";
import {
    buildDeadlineCloudJobCallbackFunction,
    buildExecuteWorkflowV2Function,
    buildWorkflowServiceV2Function,
    buildWorkflowTriggerServiceFunction,
    buildWorkflowTriggerDispatchFunction,
    buildImportGlobalPipelineWorkflowFunction,
    buildExecutionServiceFunction,
    buildExecutionPermanentDeleteFunction,
    buildProcessWorkflowExecutionOutputFunction,
    buildInterimPipelineTrackingFunction,
    buildHandleExecutionErrorFunction,
    buildRegisterPipelineExecutionFunction,
    buildWorkflowRole,
    grantWorkflowServiceInternalInvoke,
} from "../../lambdaBuilder/workflowFunctions";
import {
    attachSystemPipelineCallbacks,
    SystemPipelineCallbackRegistry,
} from "../../lambdaBuilder/systemPipelineCallbacks";
import { buildWorkflowStatusChangeRule } from "../../lambdaBuilder/workflowStatusEvents";
import {
    buildLargeObjectCopyEngine,
    grantLargeObjectCopyCoordination,
    grantLargeObjectCopyHandOffByParameter,
    largeObjectCopyEnvironment,
} from "../../lambdaBuilder/largeObjectCopyEngine";
import {
    buildPipelineServiceV2Function,
    buildPipelineTemplateServiceFunction,
} from "../../lambdaBuilder/pipelineFunctions";
import { RouteRegistry, attachFunctionToApi } from "./apiRouteRegistry";
import * as Config from "../../../config/config";
import { RESOURCE_PARAM_KEYS } from "../../../common/resourceParamKeys";

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
    // Invoked by the large object copy engine built here once a large file has landed.
    sendEmailFunction: lambda.Function;
}

/**
 * ApiBuilder2NestedStack
 *
 * Secondary backend API nested stack. API domains are split across this stack and the primary
 * ApiBuilderNestedStack so each carries its own budget against the two per-template CloudFormation
 * ceilings: 500 resources and a 1 MB template body. Resource counts are comfortable (the primary
 * stack emits 106, this one 69), but the primary template is already ~0.40 MB, so body size fills
 * well ahead of resource count and is what the split actually buys headroom against. New API
 * endpoints belong here.
 *
 * The REST API is materialized once, from what both stacks register into RouteRegistry, so its path
 * tree counts against a single API Gateway quota — resources per REST API, 122 of a default 300 —
 * that splitting the CDK stacks does not relieve. That quota is adjustable; the per-template
 * CloudFormation ceilings are not. Figures are asserted in test/apiStackCeilings.test.ts.
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

        // Auth constraints service and its routes.
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
            encryptionKey: props.storageResources.encryption.kmsKey,
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        // Execution service: asset-scoped + global list, details/traceability, paged detail
        // metadata, logs, abort, abort-by-group, re-run. Permanent delete has its own function below.
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
            routePath: "/workflows/executions/{executionId}/details/files",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/workflows/executions/{executionId}/details/results",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, executionServiceFunction, {
            routePath: "/workflows/executions/{executionId}/logs",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        // Global execution ops on the same handler: asset-less list, re-run.
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
        // Permanent delete runs the same handler on its own function and role, the only one that can
        // delete execution rows.
        const executionPermanentDeleteFunction = buildExecutionPermanentDeleteFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, executionPermanentDeleteFunction, {
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

        // Terminal callbacks of system pipelines (runs VAMS launches for a feature under a system
        // trigger value). The three functions that run the five terminal writers (end state, error
        // handler, and the execution service's abort, group abort and read-path reconcile) get the
        // registry and an invoke grant on exactly the registered callback functions, as one managed
        // policy attached to all three roles (kept out of their inline policies, which are near IAM's
        // quota). A feature that launches system pipelines adds its stored trigger value and callback
        // function here. Empty until one does, which adds no variable and no policy.
        const systemPipelineCallbacks: SystemPipelineCallbackRegistry = {};

        // Download as Zip: one function, the zip download service, serves the six routes, the
        // zipArchive pipeline's terminal callback and the delayed sweep's passes. It deploys whatever
        // app.zipDownloads.enabled says, so turning the feature off keeps existing jobs readable and
        // the routes answering 404. Only with it on is it registered as the Zip-Download callback,
        // given the sweep state machine, and granted its launch and abort cross-calls (below, once
        // executeWorkflowV2 exists).
        const zipDownloadServiceFunction = buildZipDownloadServiceFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        for (const route of ZIP_DOWNLOAD_ROUTES) {
            attachFunctionToApi(this, zipDownloadServiceFunction, {
                routePath: route.routePath,
                method: route.method,
                registry: registry,
            });
        }
        if (config.app.zipDownloads.enabled) {
            systemPipelineCallbacks[ZIP_DOWNLOAD_SYSTEM_TRIGGER] = zipDownloadServiceFunction;
            // The delayed sweep of a stopped job's archive upload (#1125 D16).
            buildZipDownloadSweep(this, zipDownloadServiceFunction, storageResources, config);
        }

        attachSystemPipelineCallbacks(
            this,
            [
                processWorkflowExecutionOutputFunction,
                handleExecutionErrorFunction,
                executionServiceFunction,
            ],
            systemPipelineCallbacks
        );
        // A run that ends outside VAMS (stopped from the console or API, timed out or failed at the
        // state machine level) is reconciled when Step Functions reports it, through the execution
        // service's read-path reconcile, so its terminal callback does not wait for a read.
        buildWorkflowStatusChangeRule(this, executionServiceFunction, storageResources);

        // Large object copy engine: copies workflow outputs over 1 GiB (and API uploads over the
        // engine threshold) into their asset as resumable multipart copies. The output step waits
        // on a run's copies and finalizes it; the abort route and the error handler stop them. Each
        // of the three gets its grants as one managed policy (their inline policies are near the
        // IAM quota).
        const largeObjectCopyEngine = buildLargeObjectCopyEngine(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            props.sendEmailFunction,
            processWorkflowExecutionOutputFunction,
            config,
            vpc,
            subnets
        );
        grantLargeObjectCopyCoordination(
            processWorkflowExecutionOutputFunction,
            storageResources,
            config,
            { answersTaskToken: true }
        );
        // Each stopper hands the engine the copies it stopped whose outcome the engine's reconcile
        // finishes; the output step through an SSM parameter (the engine references it already).
        grantLargeObjectCopyHandOffByParameter(
            this,
            processWorkflowExecutionOutputFunction,
            largeObjectCopyEngine.engineFunction,
            config
        );
        grantLargeObjectCopyCoordination(handleExecutionErrorFunction, storageResources, config, {
            answersTaskToken: false,
            handsOffTo: largeObjectCopyEngine.engineFunction,
        });
        // The details read also hands the engine a copy whose execution ended unreported.
        grantLargeObjectCopyCoordination(executionServiceFunction, storageResources, config, {
            answersTaskToken: false,
            reconcileWith: largeObjectCopyEngine.engineFunction,
        });

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

        // SFN execution role every generated workflow state machine assumes. Built here rather than
        // inside the workflow service builder so its ARN can be published to SSM below for externally
        // owned pipeline bridges, which trust it for task-token callbacks.
        const workflowRole = buildWorkflowRole(
            this,
            processWorkflowExecutionOutputFunction,
            config,
            storageResources.encryption.kmsKey
        );

        // Workflow V2 resources: create (POST), update (PUT), list/get/delete, and the trigger
        // sub-resources — the whole workflow CRUD surface.
        const workflowServiceV2 = buildWorkflowServiceV2Function(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            workflowRole,
            processWorkflowExecutionOutputFunction,
            interimPipelineTrackingFunction,
            handleExecutionErrorFunction,
            workflowsLogGroup,
            config.env.coreStackName,
            config,
            vpc,
            subnets
        );
        // The generated definitions wait this long for a run's large outputs (AwaitOutputIngestion).
        workflowServiceV2.addEnvironment(
            "LARGE_OBJECT_COPY_MAX_WAIT_HOURS",
            largeObjectCopyEnvironment(config).LARGE_OBJECT_COPY_MAX_WAIT_HOURS
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
        // The launch-time deployment check compares the wait a workflow was deployed with to this.
        executeWorkflowV2.addEnvironment(
            "LARGE_OBJECT_COPY_MAX_WAIT_HOURS",
            largeObjectCopyEnvironment(config).LARGE_OBJECT_COPY_MAX_WAIT_HOURS
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

        if (config.app.zipDownloads.enabled) {
            grantZipDownloadLaunch(
                zipDownloadServiceFunction,
                executeWorkflowV2,
                executionServiceFunction
            );
        }

        // Keep workflow state machines current with their pipelines: a pipeline save schedules an
        // asynchronous redeploy of every referencing workflow, and a launch redeploys a stale workflow
        // synchronously before starting it. Both invoke the workflow service, which holds the deploy
        // permissions, so each caller gets only lambda:InvokeFunction on its exact function ARN.
        grantWorkflowServiceInternalInvoke(pipelineServiceV2, workflowServiceV2);
        grantWorkflowServiceInternalInvoke(executeWorkflowV2, workflowServiceV2);

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

        // Publish the workflow role ARN and the import function name for externally owned pipeline
        // bridge stacks. Created here rather than through the resource-name registry because this
        // stack builds after the ResourceNames stack materializes the registry.
        new ssm.StringParameter(this, "ResourceNameParamWorkflowExecutionRoleArn", {
            parameterName: `${config.resourceNamesSSMParamPrefix}/${RESOURCE_PARAM_KEYS.integration.workflowExecutionRoleArn}`,
            stringValue: workflowRole.roleArn,
        });
        new ssm.StringParameter(this, "ResourceNameParamImportGlobalPipelineWorkflow", {
            parameterName: `${config.resourceNamesSSMParamPrefix}/${RESOURCE_PARAM_KEYS.lambdaFunctions.importGlobalPipelineWorkflow}`,
            stringValue: importGlobalPipelineWorkflow.functionName,
        });

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

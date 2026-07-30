/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../config/config";
import { NagSuppressions } from "cdk-nag";
import { storageResources } from "../storage/storageBuilder-nestedStack";
import { RouteRegistry, attachFunctionToApi } from "../apiLambda/apiRouteRegistry";
import * as iam from "aws-cdk-lib/aws-iam";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import {
    buildFMMSchemaService,
    buildFMMEvaluateService,
    buildFMMQuarantineService,
    buildFMMCascadeService,
    buildFMMAuditService,
    buildFMMComplianceTrigger,
    buildFMMSchemaBindingService,
    buildFMMPipelineCallback,
} from "../../lambdaBuilder/fmmFunctions";
import { DynamoDbFmmSchemaDefaultsConstruct } from "./dynamodb-fmm-schema-defaults-construct";

export interface FMMBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    registry: RouteRegistry;
}

export class FMMBuilderNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: FMMBuilderNestedStackProps) {
        super(parent, name);

        const { config, lambdaCommonBaseLayer, storageResources, vpc, subnets, registry } = props;

        // Schema Management
        const fmmSchemaService = buildFMMSchemaService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, fmmSchemaService, {
            routePath: "/compliance/schemas",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, fmmSchemaService, {
            routePath: "/compliance/schemas",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, fmmSchemaService, {
            routePath: "/compliance/schemas/{schemaName}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, fmmSchemaService, {
            routePath: "/compliance/schemas/{schemaName}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        // Evaluation
        const fmmEvaluateService = buildFMMEvaluateService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, fmmEvaluateService, {
            routePath: "/compliance/evaluate/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, fmmEvaluateService, {
            routePath: "/compliance/sweep/{schemaName}",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, fmmEvaluateService, {
            routePath: "/compliance/evaluations/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, fmmEvaluateService, {
            routePath: "/compliance/state/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, fmmEvaluateService, {
            routePath: "/compliance/state/{databaseId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        // Quarantine
        const fmmQuarantineService = buildFMMQuarantineService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, fmmQuarantineService, {
            routePath: "/compliance/quarantine",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, fmmQuarantineService, {
            routePath: "/compliance/quarantine/{databaseId}/{assetId}/release",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, fmmQuarantineService, {
            routePath: "/compliance/quarantine/{databaseId}/{assetId}/exception",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Cascades
        const fmmCascadeService = buildFMMCascadeService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, fmmCascadeService, {
            routePath: "/compliance/cascades",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, fmmCascadeService, {
            routePath: "/compliance/cascades",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, fmmCascadeService, {
            routePath: "/compliance/cascades/{cascadeId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, fmmCascadeService, {
            routePath: "/compliance/cascades/{cascadeId}/approve",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, fmmCascadeService, {
            routePath: "/compliance/cascades/{cascadeId}/reject",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Audit
        const fmmAuditService = buildFMMAuditService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, fmmAuditService, {
            routePath: "/compliance/audit/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, fmmAuditService, {
            routePath: "/compliance/audit",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        // Schema Binding
        const fmmSchemaBindingService = buildFMMSchemaBindingService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, fmmSchemaBindingService, {
            routePath: "/compliance/bind/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, fmmSchemaBindingService, {
            routePath: "/compliance/bind/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });
        attachFunctionToApi(this, fmmSchemaBindingService, {
            routePath: "/compliance/bind/{databaseId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, fmmSchemaBindingService, {
            routePath: "/compliance/bind/{databaseId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });
        attachFunctionToApi(this, fmmSchemaBindingService, {
            routePath: "/compliance/bind/{databaseId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        // Compliance Trigger - SNS subscription for asset upload events
        const fmmComplianceTrigger = buildFMMComplianceTrigger(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        fmmComplianceTrigger.addEventSource(
            new eventsources.SnsEventSource(storageResources.sns.assetIndexerSnsTopic)
        );

        // Pipeline Callback - EventBridge rule for SFN execution completion
        const fmmPipelineCallback = buildFMMPipelineCallback(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        const sfnCompletionRule = new events.Rule(this, "FmmSfnCompletionRule", {
            description: "Routes Step Functions execution completion events to FMM pipeline callback Lambda",
            eventPattern: {
                source: ["aws.states"],
                detailType: ["Step Functions Execution Status Change"],
                detail: {
                    status: ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"],
                },
            },
        });
        sfnCompletionRule.addTarget(new targets.LambdaFunction(fmmPipelineCallback));

        // Default schema deployment via custom resource
        const fmmCustomResourceRole = new iam.Role(this, "FmmSchemaDefaultsRole", {
            assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
        });
        storageResources.dynamo.fmmSchemaStorageTable.grantWriteData(fmmCustomResourceRole);

        new DynamoDbFmmSchemaDefaultsConstruct(this, "FmmSchemaDefaults", {
            customResourceRole: fmmCustomResourceRole,
            storageResources: storageResources,
            config: config,
        });

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Wildcard permissions required for DynamoDB GSI index access on FMM tables and states:StartExecution on dynamically-created Step Functions state machines. Scope is limited to deployment-specific FMM tables and compliance workflows.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-SQS3",
                    reason: "FMM SNS event source does not use a standalone SQS queue requiring a DLQ. Re-evaluation is triggered by re-upload or manual sweep.",
                },
            ],
            true
        );
    }
}


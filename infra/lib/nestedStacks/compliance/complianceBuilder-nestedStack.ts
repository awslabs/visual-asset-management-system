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
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import {
    buildComplianceSchemaServiceFunction,
    buildComplianceEvaluateServiceFunction,
    buildComplianceQuarantineServiceFunction,
    buildComplianceCascadeServiceFunction,
    buildComplianceAuditServiceFunction,
    buildComplianceTrigger,
    buildComplianceSchemaBindingServiceFunction,
    buildComplianceWorkflowCallback,
} from "../../lambdaBuilder/complianceFunctions";
import { DynamoDbComplianceSchemaDefaultsConstruct } from "../apiLambda/constructs/dynamodb-complianceschema-defaults-construct";

export interface ComplianceBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    registry: RouteRegistry;
    executeWorkflowFunction: lambda.Function;
}

export class ComplianceBuilderNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: ComplianceBuilderNestedStackProps) {
        super(parent, name);

        const {
            config,
            lambdaCommonBaseLayer,
            storageResources,
            vpc,
            subnets,
            registry,
            executeWorkflowFunction,
        } = props;

        // Schema Management
        const complianceSchemaService = buildComplianceSchemaServiceFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, complianceSchemaService, {
            routePath: "/compliance/schemas",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, complianceSchemaService, {
            routePath: "/compliance/schemas",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, complianceSchemaService, {
            routePath: "/compliance/schemas/{schemaName}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, complianceSchemaService, {
            routePath: "/compliance/schemas/{schemaName}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        // Evaluation
        const complianceEvaluateService = buildComplianceEvaluateServiceFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            executeWorkflowFunction
        );
        attachFunctionToApi(this, complianceEvaluateService, {
            routePath: "/compliance/evaluate/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, complianceEvaluateService, {
            routePath: "/compliance/sweep/{schemaName}",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, complianceEvaluateService, {
            routePath: "/compliance/evaluations/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, complianceEvaluateService, {
            routePath: "/compliance/state/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, complianceEvaluateService, {
            routePath: "/compliance/state/{databaseId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        // Quarantine
        const complianceQuarantineService = buildComplianceQuarantineServiceFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, complianceQuarantineService, {
            routePath: "/compliance/quarantine",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, complianceQuarantineService, {
            routePath: "/compliance/quarantine/{databaseId}/{assetId}/release",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, complianceQuarantineService, {
            routePath: "/compliance/quarantine/{databaseId}/{assetId}/exception",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Cascades
        const complianceCascadeService = buildComplianceCascadeServiceFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            executeWorkflowFunction
        );
        attachFunctionToApi(this, complianceCascadeService, {
            routePath: "/compliance/cascades",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, complianceCascadeService, {
            routePath: "/compliance/cascades",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, complianceCascadeService, {
            routePath: "/compliance/cascades/{cascadeId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, complianceCascadeService, {
            routePath: "/compliance/cascades/{cascadeId}/approve",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, complianceCascadeService, {
            routePath: "/compliance/cascades/{cascadeId}/reject",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Audit
        const complianceAuditService = buildComplianceAuditServiceFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, complianceAuditService, {
            routePath: "/compliance/audit/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, complianceAuditService, {
            routePath: "/compliance/audit",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        // Schema Binding
        const complianceSchemaBindingService = buildComplianceSchemaBindingServiceFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, complianceSchemaBindingService, {
            routePath: "/compliance/bind/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, complianceSchemaBindingService, {
            routePath: "/compliance/bind/{databaseId}/{assetId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });
        attachFunctionToApi(this, complianceSchemaBindingService, {
            routePath: "/compliance/bind/{databaseId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, complianceSchemaBindingService, {
            routePath: "/compliance/bind/{databaseId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });
        attachFunctionToApi(this, complianceSchemaBindingService, {
            routePath: "/compliance/bind/{databaseId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        // Compliance Trigger - SNS subscription for asset upload events
        const complianceTrigger = buildComplianceTrigger(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            executeWorkflowFunction
        );
        complianceTrigger.addEventSource(
            new eventsources.SnsEventSource(storageResources.sns.assetIndexerSnsTopic)
        );
        complianceTrigger.addEventSource(
            new eventsources.SnsEventSource(storageResources.sns.fileIndexerSnsTopic)
        );

        // Pipeline Callback - EventBridge rule for SFN execution completion
        const complianceWorkflowCallback = buildComplianceWorkflowCallback(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        const sfnCompletionRule = new events.Rule(this, "ComplianceSfnCompletionRule", {
            description:
                "Routes Step Functions execution completion events to Compliance pipeline callback Lambda",
            eventPattern: {
                source: ["aws.states"],
                detailType: ["Step Functions Execution Status Change"],
                detail: {
                    status: ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"],
                },
            },
        });
        sfnCompletionRule.addTarget(new targets.LambdaFunction(complianceWorkflowCallback));

        // Default schema deployment via custom resource
        const complianceCustomResourceRole = new iam.Role(this, "ComplianceSchemaDefaultsRole", {
            assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
        });
        storageResources.dynamo.complianceSchemaStorageTable.grantWriteData(
            complianceCustomResourceRole
        );

        new DynamoDbComplianceSchemaDefaultsConstruct(this, "ComplianceSchemaDefaults", {
            customResourceRole: complianceCustomResourceRole,
            storageResources: storageResources,
            config: config,
        });

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Wildcard permissions required for DynamoDB GSI index access on Compliance tables and SNS Publish for compliance notifications. Scope is limited to deployment-specific Compliance tables.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-SQS3",
                    reason: "ComplianceSNS event source does not use a standalone SQS queue requiring a DLQ. Re-evaluation is triggered by re-upload or manual sweep.",
                },
            ],
            true
        );
    }
}

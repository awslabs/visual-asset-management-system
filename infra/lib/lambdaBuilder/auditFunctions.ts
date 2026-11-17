/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as path from "path";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import {
    storageResources,
    AUDIT_LOG_OPEN_QUERIES_INDEX,
} from "../nestedStacks/storage/storageBuilder-nestedStack";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    suppressCdkNagLambda,
    setupSecurityAndLoggingEnvironmentAndPermissions,
} from "../helper/security";
import * as Config from "../../config/config";

/**
 * The routes the audit log search service serves (backend common/apiRoutes.py AUDIT_ROUTES, one
 * entry per route and method; `routePath` is the API Gateway template).
 */
export const AUDIT_LOG_ROUTES: ReadonlyArray<{
    routePath: string;
    method: apigateway.HttpMethod;
}> = [
    { routePath: "/audit/log-groups", method: apigateway.HttpMethod.GET },
    { routePath: "/audit/queries", method: apigateway.HttpMethod.POST },
    { routePath: "/audit/queries/{queryId}", method: apigateway.HttpMethod.GET },
    { routePath: "/audit/queries/{queryId}", method: apigateway.HttpMethod.DELETE },
    { routePath: "/audit/queries/{queryId}/export", method: apigateway.HttpMethod.GET },
];

/**
 * The Logs actions the search service may call on the audit log groups. Read-only on the audit data:
 * it starts, reads and stops its own Logs Insights queries and lists the running ones. Nothing in
 * VAMS may modify or delete an audit record, so no `logs:Delete*`, `PutRetentionPolicy`,
 * `PutSubscriptionFilter`, `CreateExportTask` or `AssociateKmsKey` is granted on any role
 * (test: `test/security/auditSearchGrants.test.ts`).
 */
export const AUDIT_SEARCH_LOGS_GROUP_ACTIONS = ["logs:StartQuery"];
export const AUDIT_SEARCH_LOGS_ACCOUNT_ACTIONS = [
    "logs:GetQueryResults",
    "logs:StopQuery",
    "logs:DescribeQueries",
];

/** The environment variables that carry app.auditLogging.search.* to the service (read by auditLogService.py). */
export function auditSearchEnvironment(config: Config.Config): Record<string, string> {
    // getConfig() fills every key; the defaults here cover a Config built without it (a test's synth
    // of a hand-edited config), so the function always carries a complete set.
    const search = { ...Config.AUDIT_SEARCH_DEFAULTS, ...(config.app.auditLogging?.search ?? {}) };
    return {
        AUDIT_SEARCH_MAX_QUERY_WINDOW_DAYS: String(search.maxQueryWindowDays),
        AUDIT_SEARCH_MAX_ROWS: String(search.maxRows),
        AUDIT_SEARCH_MAX_CONCURRENT_QUERIES: String(search.maxConcurrentQueries),
        AUDIT_SEARCH_MAX_CONCURRENT_QUERIES_PER_USER: String(search.maxConcurrentQueriesPerUser),
        AUDIT_SEARCH_QUERY_DEADLINE_SECONDS: String(search.queryDeadlineSeconds),
        AUDIT_SEARCH_LONG_POLL_WAIT_SECONDS: String(search.longPollWaitSeconds),
        AUDIT_SEARCH_MAX_CONTAINS_TERM_LENGTH: String(search.maxContainsTermLength),
        AUDIT_SEARCH_MAX_CONTAINS_TERMS: String(search.maxContainsTerms),
    };
}

/**
 * The audit log search service (`handlers/audit/auditLogService.py`): one function for the five audit
 * routes. Core VAMS, deployed unconditionally (no feature switch); access is decided only by
 * authorization. The timeout equals the API Gateway integration timeout: the long poll holds one API
 * request, so a longer Lambda would keep running after the API answered 504.
 */
export function buildAuditLogService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "auditLogService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.audit.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.seconds(config.app.api.apiGatewayRest.apiGatewayTimeoutTime),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: auditSearchEnvironment(config),
    });

    // The handle table and its open-search index.
    const queryTable = storageResources.dynamo.auditLogQueriesStorageTable;
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:Query",
            ],
            resources: [
                queryTable.tableArn,
                `${queryTable.tableArn}/index/${AUDIT_LOG_OPEN_QUERIES_INDEX}`,
            ],
        })
    );

    // Logs Insights over the audit log groups. StartQuery is scoped to the group ARNs (each built from
    // the group, so the partition and region follow the deployment); the three account-scoped actions
    // act on a query id and have no resource-level scoping.
    const groups = storageResources.cloudWatchAuditLogGroups;
    const groupArns = Object.values(groups).map((group) => group.logGroupArn);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: AUDIT_SEARCH_LOGS_GROUP_ACTIONS,
            resources: groupArns,
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: AUDIT_SEARCH_LOGS_ACCOUNT_ACTIONS,
            resources: ["*"],
        })
    );

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "logs:GetQueryResults, logs:StopQuery and logs:DescribeQueries act on a Logs Insights query id and do not support resource-level permissions (Service Authorization Reference, Amazon CloudWatch Logs). The service only passes a query id it started and stored against the calling user; the ownership table, not IAM, decides who may read a result. logs:StartQuery is scoped to the audit log group ARNs.",
                appliesTo: ["Resource::*"],
            },
        ],
        true
    );
    return fun;
}

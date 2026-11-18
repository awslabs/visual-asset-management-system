/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { NestedStack } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import { storageResources } from "../storage/storageBuilder-nestedStack";
import { buildAuditLogService, AUDIT_LOG_ROUTES } from "../../lambdaBuilder/auditFunctions";
import { RouteRegistry, attachFunctionToApi } from "./apiRouteRegistry";
import * as Config from "../../../config/config";

/**
 * Properties for the third API builder nested stack.
 */
export interface ApiBuilder3NestedStackProps {
    config: Config.Config;
    registry: RouteRegistry;
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
}

/**
 * ApiBuilder3NestedStack
 *
 * Third backend API nested stack, and the home of every NEW core API endpoint. With
 * `useGlobalVpc.useForAllLambdas` on, every function carries a VpcConfig block and a security group
 * and ApiBuilder2 sat at 986,768 bytes of the 1,000,000 byte CloudFormation template-body limit;
 * adding the audit log search made it 1,009,044 and a deploy stopped at change-set creation with
 * "Template may not exceed 1000000 bytes". Resource count is not the constraint; the body is, and
 * only a new template adds body budget. `test/api/nestedTemplateSizeGuard.test.ts` fails the build
 * when any template nears the limit in the worst-case cells, so the next stack to fill up is caught
 * before a deploy.
 *
 * It holds the audit log search service (five `/audit` routes). That service is core VAMS with no feature switch: it deploys unconditionally and access is decided only by authorization (Tier 1
 * route, Tier 2 per log group). Its handle table lives in the storage stack, so moving the function
 * between nested stacks replaces only the stateless function, its role and its log group.
 *
 * Routes register into the same RouteRegistry as every other API stack and the REST API stack
 * (built last) renders them, so the API Gateway deployment and the path-tree quota are unchanged by
 * where a function is built.
 */
export class ApiBuilder3NestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: ApiBuilder3NestedStackProps) {
        super(parent, name);

        const { config, registry, storageResources, lambdaCommonBaseLayer, vpc, subnets } = props;

        const auditLogService = buildAuditLogService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        for (const route of AUDIT_LOG_ROUTES) {
            attachFunctionToApi(this, auditLogService, {
                routePath: route.routePath,
                method: route.method,
                registry: registry,
            });
        }

        // Nag suppressions, scoped with appliesTo exactly as in ApiBuilder2: the audit-table and log
        // group wildcards and the CDK-generated custom-resource roles, not every IAM5 wildcard.
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

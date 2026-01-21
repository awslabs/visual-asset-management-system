/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import {
    AwsCustomResource,
    AwsSdkCall,
    AwsCustomResourcePolicy,
    PhysicalResourceId,
    PhysicalResourceIdReference,
} from "aws-cdk-lib/custom-resources";
import { Construct } from "constructs";
import { Config } from "../../../../config/config";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import { kmsKeyPolicyStatementGenerator } from "../../../helper/security";
import { Service } from "../../../../lib/helper/service-helper";
import * as ServiceHelper from "../../../../lib/helper/service-helper";

export interface DynamoDbAuthDefaultsROConstructStackProps extends cdk.StackProps {
    customResourceRole: iam.Role;
    lambdaCommonBaseLayer: LayerVersion;
    storageResources: storageResources;
    config: Config;
}

/**
 * Helper function to convert constraint data to denormalized table format
 * Creates one item per UNIQUE group/user for efficient GSI queries
 * Multiple permissions for same group are stored in the groupPermissions JSON
 */
function convertConstraintToNewFormat(constraint: any): any[] {
    const items: any[] = [];

    // Base constraint data shared across all denormalized items
    const baseConstraint = {
        name: constraint.name,
        description: constraint.description,
        objectType: constraint.objectType,
        // Convert complex fields to JSON strings
        criteriaAnd: {
            S: JSON.stringify(
                constraint.criteriaAnd?.L?.map((c: any) => ({
                    field: c.M.field.S,
                    id: c.M.id.S,
                    operator: c.M.operator.S,
                    value: c.M.value.S,
                })) || []
            ),
        },
        criteriaOr: {
            S: JSON.stringify(
                constraint.criteriaOr?.L?.map((c: any) => ({
                    field: c.M.field.S,
                    id: c.M.id.S,
                    operator: c.M.operator.S,
                    value: c.M.value.S,
                })) || []
            ),
        },
        groupPermissions: {
            S: JSON.stringify(
                constraint.groupPermissions?.L?.map((p: any) => ({
                    groupId: p.M.groupId.S,
                    id: p.M.id.S,
                    permission: p.M.permission.S,
                    permissionType: p.M.permissionType.S,
                })) || []
            ),
        },
        userPermissions: {
            S: JSON.stringify(
                constraint.userPermissions?.L?.map((p: any) => ({
                    userId: p.M.userId.S,
                    id: p.M.id.S,
                    permission: p.M.permission.S,
                    permissionType: p.M.permissionType.S,
                })) || []
            ),
        },
        dateCreated: {
            S: new Date().toISOString(),
        },
        dateModified: {
            S: new Date().toISOString(),
        },
        createdBy: {
            S: "SYSTEM",
        },
        modifiedBy: {
            S: "SYSTEM",
        },
    };

    // Create one item per UNIQUE groupId (not per permission)
    // Multiple permissions for same group are stored in the groupPermissions JSON
    const groupPermissions = constraint.groupPermissions?.L || [];
    const uniqueGroupIds = new Set<string>();
    for (const groupPerm of groupPermissions) {
        const groupId = groupPerm.M.groupId.S;
        if (!uniqueGroupIds.has(groupId)) {
            uniqueGroupIds.add(groupId);
            items.push({
                constraintId: {
                    S: `${constraint.constraintId.S}#group#${groupId}`,
                },
                groupId: { S: groupId }, // For GroupPermissionsIndex GSI
                ...baseConstraint,
            });
        }
    }

    // Create one item per UNIQUE userId (not per permission)
    // Multiple permissions for same user are stored in the userPermissions JSON
    const userPermissions = constraint.userPermissions?.L || [];
    const uniqueUserIds = new Set<string>();
    for (const userPerm of userPermissions) {
        const userId = userPerm.M.userId.S;
        if (!uniqueUserIds.has(userId)) {
            uniqueUserIds.add(userId);
            items.push({
                constraintId: {
                    S: `${constraint.constraintId.S}#user#${userId}`,
                },
                userId: { S: userId }, // For UserPermissionsIndex GSI
                ...baseConstraint,
            });
        }
    }

    // Safety: If no permissions exist, create one base item (shouldn't happen in practice)
    if (items.length === 0) {
        items.push({
            constraintId: constraint.constraintId,
            ...baseConstraint,
        });
    }

    return items;
}

/**
 * Deploys Auth Defaults to DynamoDB tables
 */
export class DynamoDbAuthDefaultsROConstructStack extends Construct {
    constructor(parent: Construct, name: string, props: DynamoDbAuthDefaultsROConstructStackProps) {
        super(parent, name);

        const roleName = "basicReadOnly";
        const roleNameIDClean = "basicro";

        const awsSdkCallRoleRo: AwsSdkCall = {
            service: "DynamoDB",
            action: "putItem",
            parameters: {
                TableName: props.storageResources.dynamo.rolesStorageTable.tableName,
                Item: {
                    roleName: {
                        S: roleName,
                    },
                    description: {
                        S: "Basic Read-Only User - Read access to all databases, assets, pipelines, workflows, and metadata schemas",
                    },
                    id: {
                        S: `initial_${roleNameIDClean}_role_creation`,
                    },
                    source: {
                        S: "INTERNAL_SYSTEM",
                    },
                    createdOn: {
                        S: new Date().toISOString(),
                    },
                    mfaRequired: {
                        BOOL: false,
                    },
                },
                //ConditionExpression: "attribute_not_exists(id)",
            },
            physicalResourceId: PhysicalResourceId.of(
                props.storageResources.dynamo.rolesStorageTable.tableName +
                    `_${roleNameIDClean}initialization`
            ),
        };

        //Deploy custom resources to tables
        new AwsCustomResource(this, `rolesStorageTable_${roleNameIDClean}CustomResource`, {
            onCreate: awsSdkCallRoleRo,
            onUpdate: awsSdkCallRoleRo,
            role: props.customResourceRole,
        });

        const initialConstraints = [
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: `initial_${roleNameIDClean}_allow_web_paths_get`,
                },
                constraintId: {
                    S: `initial_${roleNameIDClean}_allow_web_paths_get`,
                },
                criteriaOr: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `1_${roleNameIDClean}_web_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/assets",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `3_${roleNameIDClean}_web_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/databases",
                                },
                            },
                        },
                        // {
                        //     M: {
                        //         field: {
                        //             S: "route__path",
                        //         },
                        //         id: {
                        //             S: `4_${roleNameIDClean}_web_paths`,
                        //         },
                        //         operator: {
                        //             S: "starts_with",
                        //         },
                        //         value: {
                        //             S: "/metadataschema",
                        //         },
                        //     },
                        // },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `5_${roleNameIDClean}_web_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/pipelines",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `6_${roleNameIDClean}_web_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/search",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `7_${roleNameIDClean}_web_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/workflows",
                                },
                            },
                        },
                    ],
                },
                description: {
                    S: "Allow GET on Web Paths for basic user read only",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: roleName,
                                },
                                id: {
                                    S: `${roleNameIDClean}-allow-get-web-paths`,
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: `${roleNameIDClean}-allow-get-web-paths`,
                },
                objectType: {
                    S: "web",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: `initial_${roleNameIDClean}_allow_get_apis`,
                },
                constraintId: {
                    S: `initial_${roleNameIDClean}_allow_get_apis`,
                },
                criteriaOr: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `0_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/secure-config", //Technically not needed as no authorization but including anyway
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `1_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/api/amplify-config", //Technically not needed as no authorization but including anyway
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `2_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/auth/routes", //Technically not needed as no authorization but including anyway
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `2a_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/auth/loginProfile", //Technically not needed as no authorization currently but including anyway (auth is based on authenticated user)
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `3_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/asset-links",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `4_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/comments",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `5_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/database",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `6_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/assets",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `7_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/metadata",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `7_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/metadataschema",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `8_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/pipelines",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `9_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/search",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `10_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/subscriptions",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `11_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/check-subscription",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `12_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/tags",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `13_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/tag-types",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `15_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/workflows",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `15_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/buckets",
                                },
                            },
                        },
                    ],
                },
                description: {
                    S: "Allow GET on API Paths for basic user read only",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: roleName,
                                },
                                id: {
                                    S: `${roleNameIDClean}-allow-get-apis`,
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: `${roleNameIDClean}-allow-get-apis`,
                },
                objectType: {
                    S: "api",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: `initial_${roleNameIDClean}_allow_post_apis`,
                },
                constraintId: {
                    S: `initial_${roleNameIDClean}_allow_post_apis`,
                },
                criteriaOr: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `0_${roleNameIDClean}_api_paths_post`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/auth/routes", //Technically not needed as no authorization but including anyway
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `1_${roleNameIDClean}_api_paths_post`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/search",
                                },
                            },
                        },
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `2_${roleNameIDClean}_api_paths_post`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/check-subscription",
                                },
                            },
                        },
                    ],
                },
                description: {
                    S: "Allow POST on API Paths for basic user read only - these are for APIs that are still considered non-mutating but use POST for additional params",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: roleName,
                                },
                                id: {
                                    S: `${roleNameIDClean}-allow-post-apis`,
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: `${roleNameIDClean}-allow-post-apis`,
                },
                objectType: {
                    S: "api",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: `initial_${roleNameIDClean}_allow_all_databases`,
                },
                constraintId: {
                    S: `initial_${roleNameIDClean}_allow_all_databases`,
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "databaseId",
                                },
                                id: {
                                    S: `${roleNameIDClean}_all_databases`,
                                },
                                operator: {
                                    S: "contains",
                                },
                                value: {
                                    S: ".*",
                                },
                            },
                        },
                    ],
                },
                description: {
                    S: "Allow GET on all databases for basic read only",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: roleName,
                                },
                                id: {
                                    S: `${roleNameIDClean}-allow-get-all-databases`,
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: `${roleNameIDClean}-allow-all-databases`,
                },
                objectType: {
                    S: "database",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: `initial_${roleNameIDClean}_allow_all_assets`,
                },
                constraintId: {
                    S: `initial_${roleNameIDClean}_allow_all_assets`,
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "databaseId",
                                },
                                id: {
                                    S: `${roleNameIDClean}_all_assets`,
                                },
                                operator: {
                                    S: "contains",
                                },
                                value: {
                                    S: ".*",
                                },
                            },
                        },
                    ],
                },
                description: {
                    S: "Allow GET on all assets for basic read only",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: roleName,
                                },
                                id: {
                                    S: `${roleNameIDClean}-allow-get-all-assets`,
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: `${roleNameIDClean}-allow-all-assets`,
                },
                objectType: {
                    S: "asset",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: `initial_${roleNameIDClean}_allow_get_pipelines`,
                },
                constraintId: {
                    S: `initial_${roleNameIDClean}_allow_get_pipelines`,
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "databaseId",
                                },
                                id: {
                                    S: `${roleNameIDClean}_all_pipelines`,
                                },
                                operator: {
                                    S: "contains",
                                },
                                value: {
                                    S: ".*",
                                },
                            },
                        },
                    ],
                },
                description: {
                    S: "Allow GET on all pipelines for basic read only user",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: roleName,
                                },
                                id: {
                                    S: `${roleNameIDClean}-allow-get-all-pipelines`,
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: `${roleNameIDClean}-allow-all-pipelines`,
                },
                objectType: {
                    S: "pipeline",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: `initial_${roleNameIDClean}_allow_all_workflows`,
                },
                constraintId: {
                    S: `initial_${roleNameIDClean}_allow_all_workflows`,
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "databaseId",
                                },
                                id: {
                                    S: `${roleNameIDClean}_all_workflows`,
                                },
                                operator: {
                                    S: "contains",
                                },
                                value: {
                                    S: ".*",
                                },
                            },
                        },
                    ],
                },
                description: {
                    S: "Allow GET on all workflows for basic user read-only",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: roleName,
                                },
                                id: {
                                    S: `${roleNameIDClean}-allow-get-all-workflows`,
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: `${roleNameIDClean}-allow-all-workflows`,
                },
                objectType: {
                    S: "workflow",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: `initial_${roleNameIDClean}_allow_all_metadataschemas`,
                },
                constraintId: {
                    S: `initial_${roleNameIDClean}_allow_all_metadataschemas`,
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "databaseId",
                                },
                                id: {
                                    S: `${roleNameIDClean}_all_metadataschemas`,
                                },
                                operator: {
                                    S: "contains",
                                },
                                value: {
                                    S: ".*",
                                },
                            },
                        },
                    ],
                },
                description: {
                    S: "Allow GET on all metadataSchemas for basic read only user",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: roleName,
                                },
                                id: {
                                    S: `${roleNameIDClean}-allow-get-all-metadataschemas`,
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: `${roleNameIDClean}-allow-all-metadataschemas`,
                },
                objectType: {
                    S: "metadataSchema",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: `initial_${roleNameIDClean}_allow_all_tags`,
                },
                constraintId: {
                    S: `initial_${roleNameIDClean}_allow_all_tags`,
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "tagName",
                                },
                                id: {
                                    S: `${roleNameIDClean}_all_tags`,
                                },
                                operator: {
                                    S: "contains",
                                },
                                value: {
                                    S: ".*",
                                },
                            },
                        },
                    ],
                },
                description: {
                    S: "Allow GET on all tags for basic read only user",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: roleName,
                                },
                                id: {
                                    S: `${roleNameIDClean}-allow-get-all-tags`,
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: `${roleNameIDClean}-allow-all-tags`,
                },
                objectType: {
                    S: "tag",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: `initial_${roleNameIDClean}_allow_all_tagtypes`,
                },
                constraintId: {
                    S: `initial_${roleNameIDClean}_allow_all_tagtypes`,
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "tagTypeName",
                                },
                                id: {
                                    S: `${roleNameIDClean}_all_tagtypes`,
                                },
                                operator: {
                                    S: "contains",
                                },
                                value: {
                                    S: ".*",
                                },
                            },
                        },
                    ],
                },
                description: {
                    S: "Allow GET on all tagTypes for basic read only user",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: roleName,
                                },
                                id: {
                                    S: `${roleNameIDClean}-allow-get-all-tagtypes`,
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: `${roleNameIDClean}-allow-all-tagtypes`,
                },
                objectType: {
                    S: "tagType",
                },
            },
        ];

        let i = 0;
        for (const constraint of initialConstraints) {
            // Convert to denormalized table format (returns array of items)
            const denormalizedItems = convertConstraintToNewFormat(constraint);

            // Create a custom resource for each denormalized item
            for (const item of denormalizedItems) {
                const awsSdkCall: AwsSdkCall = {
                    service: "DynamoDB",
                    action: "putItem",
                    parameters: {
                        TableName: props.storageResources.dynamo.constraintsStorageTable.tableName,
                        Item: item,
                        //ConditionExpression: "attribute_not_exists(constraintId)",
                    },
                    physicalResourceId: PhysicalResourceId.of(
                        `${props.storageResources.dynamo.constraintsStorageTable.tableName}_initialization${roleNameIDClean}_${i}`
                    ),
                };

                new AwsCustomResource(
                    this,
                    `constraintsStorageTable_${roleNameIDClean}CustomResource_${i}`,
                    {
                        onCreate: awsSdkCall,
                        onUpdate: awsSdkCall,
                        role: props.customResourceRole,
                    }
                );
                i++;
            }
        }
    }
}

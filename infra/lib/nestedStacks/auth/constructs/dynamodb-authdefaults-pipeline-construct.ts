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

export interface DynamoDbAuthDefaultsPipelineConstructStackProps extends cdk.StackProps {
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
export class DynamoDbAuthDefaultsPipelineConstructStack extends Construct {
    constructor(
        parent: Construct,
        name: string,
        props: DynamoDbAuthDefaultsPipelineConstructStackProps
    ) {
        super(parent, name);

        const roleName = "pipeline";
        const roleNameIDClean = "pipeline";

        //Setup Custom Resource Role Policy
        const customResourcePolicyRole = AwsCustomResourcePolicy.fromStatements([
            new iam.PolicyStatement({
                sid: "AuthBuilderRCustomResourceWriteAccess",
                effect: iam.Effect.ALLOW,
                actions: ["dynamodb:PutItem"],
                resources: [props.storageResources.dynamo.rolesStorageTable.tableArn],
            }),
        ]);

        if (props.storageResources.encryption.kmsKey) {
            customResourcePolicyRole.statements.push(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
        }

        const customResourcePolicyRoleUser = AwsCustomResourcePolicy.fromStatements([
            new iam.PolicyStatement({
                sid: "AuthBuilderRUCustomResourceWriteAccess",
                effect: iam.Effect.ALLOW,
                actions: ["dynamodb:PutItem"],
                resources: [props.storageResources.dynamo.userRolesStorageTable.tableArn],
            }),
        ]);

        if (props.storageResources.encryption.kmsKey) {
            customResourcePolicyRoleUser.statements.push(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
        }

        const customResourcePolicyEntity = AwsCustomResourcePolicy.fromStatements([
            new iam.PolicyStatement({
                sid: "AuthBuilderECustomResourceWriteAccess",
                effect: iam.Effect.ALLOW,
                actions: ["dynamodb:PutItem"],
                resources: [props.storageResources.dynamo.constraintsStorageTable.tableArn],
            }),
        ]);

        if (props.storageResources.encryption.kmsKey) {
            customResourcePolicyEntity.statements.push(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
        }

        const awsSdkCallRoleBasic: AwsSdkCall = {
            service: "DynamoDB",
            action: "putItem",
            parameters: {
                TableName: props.storageResources.dynamo.rolesStorageTable.tableName,
                Item: {
                    roleName: {
                        S: roleName,
                    },
                    description: {
                        S: "Pipeline User - Read/Write access to pipelines and workflows",
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
            onCreate: awsSdkCallRoleBasic,
            onUpdate: awsSdkCallRoleBasic,
            policy: customResourcePolicyRole,
        });

        const initialConstraints = [
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: "initial_admin_allow_all_web_paths",
                },
                constraintId: {
                    S: "initial_admin_allow_all_web_paths",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: "all_web_paths",
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
                    S: "Allow GET/PUT/POST/DELETE on all Web Paths for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-web-paths",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-web-paths",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-web-paths",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-web-paths",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-web-paths",
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
                    S: "initial_admin_allow_all_apis",
                },
                constraintId: {
                    S: "initial_admin_allow_all_apis",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: "all_api_paths",
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
                    S: "Allow GET/PUT/POST/DELETE on all API paths for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-apis",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-apis",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-apis",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-apis",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-apis",
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
                    S: "initial_admin_allow_all_databases",
                },
                constraintId: {
                    S: "initial_admin_allow_all_databases",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "databaseId",
                                },
                                id: {
                                    S: "all_databases",
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
                    S: "Allow GET/PUT/POST/DELETE on all databases for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-databases",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-databases",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-databases",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-databases",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-databases",
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
                    S: "initial_admin_allow_all_assets",
                },
                constraintId: {
                    S: "initial_admin_allow_all_assets",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "databaseId",
                                },
                                id: {
                                    S: "all_assets",
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
                    S: "Allow GET/PUT/POST/DELETE on all assets for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-assets",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-assets",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-assets",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-assets",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-assets",
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
                    S: "initial_admin_allow_all_pipelines",
                },
                constraintId: {
                    S: "initial_admin_allow_all_pipelines",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "databaseId",
                                },
                                id: {
                                    S: "all_pipelines",
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
                    S: "Allow GET/PUT/POST/DELETE on all pipelines for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-pipelines",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-pipelines",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-pipelines",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-pipelines",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-pipelines",
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
                    S: "initial_admin_allow_all_workflows",
                },
                constraintId: {
                    S: "initial_admin_allow_all_workflows",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "databaseId",
                                },
                                id: {
                                    S: "all_workflows",
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
                    S: "Allow GET/PUT/POST/DELETE on all workflows for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-workflows",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-workflows",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-workflows",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-workflows",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-workflows",
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
                    S: "initial_admin_allow_all_metadataSchemas",
                },
                constraintId: {
                    S: "initial_admin_allow_all_metadataSchemas",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "databaseId",
                                },
                                id: {
                                    S: "all_metadataSchemas",
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
                    S: "Allow GET/PUT/POST/DELETE on all metadataSchemas for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-metadataSchemas",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-metadataSchemas",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-metadataSchemas",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-metadataSchemas",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-metadataSchemas",
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
                    S: "initial_admin_allow_all_tags",
                },
                constraintId: {
                    S: "initial_admin_allow_all_tags",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "tagName",
                                },
                                id: {
                                    S: "all_tags",
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
                    S: "Allow GET/PUT/POST/DELETE on all tags for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-tags",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-tags",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-tags",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-tags",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-tags",
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
                    S: "initial_admin_allow_all_tagTypes",
                },
                constraintId: {
                    S: "initial_admin_allow_all_tagTypes",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "tagTypeName",
                                },
                                id: {
                                    S: "all_tagTypes",
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
                    S: "Allow GET/PUT/POST/DELETE on all tagTypes for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-tagTypes",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-tagTypes",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-tagTypes",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-tagTypes",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-tagTypes",
                },
                objectType: {
                    S: "tagType",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: "initial_admin_allow_all_roles",
                },
                constraintId: {
                    S: "initial_admin_allow_all_roles",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "roleName",
                                },
                                id: {
                                    S: "all_roles",
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
                    S: "Allow GET/PUT/POST/DELETE on all roles for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-roles",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-roles",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-roles",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-roles",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-roles",
                },
                objectType: {
                    S: "role",
                },
            },
            {
                entityType: {
                    S: "constraint",
                },
                sk: {
                    S: "initial_admin_allow_all_userRoles",
                },
                constraintId: {
                    S: "initial_admin_allow_all_userRoles",
                },
                criteriaAnd: {
                    L: [
                        {
                            M: {
                                field: {
                                    S: "roleName",
                                },
                                id: {
                                    S: "all_userRoles",
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
                    S: "Allow GET/PUT/POST/DELETE on all userRoles for admin",
                },
                groupPermissions: {
                    L: [
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-get-all-userRoles",
                                },
                                permission: {
                                    S: "GET",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-put-all-userRoles",
                                },
                                permission: {
                                    S: "PUT",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-post-all-userRoles",
                                },
                                permission: {
                                    S: "POST",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                        {
                            M: {
                                groupId: {
                                    S: "admin",
                                },
                                id: {
                                    S: "admin-allow-delete-all-userRoles",
                                },
                                permission: {
                                    S: "DELETE",
                                },
                                permissionType: {
                                    S: "allow",
                                },
                            },
                        },
                    ],
                },
                name: {
                    S: "admin-allow-all-userRoles",
                },
                objectType: {
                    S: "userRole",
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
                        policy: customResourcePolicyEntity,
                    }
                );
                i++;
            }
        }
    }
}

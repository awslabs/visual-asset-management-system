/* eslint-disable @typescript-eslint/no-empty-function */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { IConstruct } from "constructs";
import * as iam from "aws-cdk-lib/aws-iam";
import { Aspects, Aws } from "aws-cdk-lib";

export function RoleRename(stack: IConstruct) {
    const rolePrefix = new cdk.CfnParameter(stack, "rolePrefix", {
        type: "String",
        description: "Role Prefix for stack IAM Roles",
        default: "",
    });
    const permissionBoundaryArn = new cdk.CfnParameter(stack, "permissionBoundaryArn", {
        type: "String",
        description: "Boundary Arn for stack IAM Roles",
        default: "",
    });
    Aspects.of(stack).add(
        new IamRoleTransform(stack, rolePrefix.valueAsString, permissionBoundaryArn.valueAsString)
    );
}

export class IamRoleTransform implements cdk.IAspect {
    private readonly permissionsBoundaryArn: string;
    resource_types: string[];
    prefix: string;
    conditionPrefix: cdk.CfnCondition;
    conditionBoundary: cdk.CfnCondition;
    constructor(stack: IConstruct, prefixParam: string, permissionBoundaryArn: string) {
        this.permissionsBoundaryArn = permissionBoundaryArn;
        this.resource_types = ["AWS::IAM::Role"];
        this.prefix = prefixParam;
        this.conditionPrefix = new cdk.CfnCondition(stack, "conditionPrefix", {
            expression: cdk.Fn.conditionEquals(prefixParam, ""),
        });
        this.conditionBoundary = new cdk.CfnCondition(stack, "conditionBoundary", {
            expression: cdk.Fn.conditionEquals(permissionBoundaryArn, ""),
        });
    }

    public visit(node: IConstruct): void {
        if (
            cdk.CfnResource.isCfnResource(node) &&
            this.resource_types.includes(node.cfnResourceType)
        ) {
            const resource_type = node.cfnResourceType;
            if (resource_type.includes("IAM::Role")) {
                // node.addPropertyOverride("RoleName",roleName);
                cdk.Fn.conditionIf(
                    this.conditionPrefix.logicalId,
                    () => {},
                    this.ApplyOverride(node, "RoleName", this.getRoleName(node))
                );
                cdk.Fn.conditionIf(
                    this.conditionBoundary.logicalId,
                    this.ApplyOverride(node, "PermissionsBoundary", Aws.NO_VALUE),
                    this.ApplyOverride(node, "PermissionsBoundary", Aws.NO_VALUE)
                );
            }
        }
    }
    public ApplyOverride(node: cdk.CfnResource, key: string, value: string) {
        node.addPropertyOverride(key, value);
    }
    public DeleteProperty(node: cdk.CfnResource, property: string) {
        node.addDeletionOverride(property);
    }
    public getRoleName(resource: IConstruct): string {
        const roleRef = resource as iam.Role;
        const originalName = roleRef.roleName;
        const deploymentIdentifierId =
            // "-" +
            // cdk.Stack.of(resource).account.toString() +
            "-" + cdk.Stack.of(resource).region.toString();
        const uniqueResourceName = cdk.Names.uniqueResourceName(resource, {
            maxLength: 40 - this.prefix.length,
        });

        const returnNameRaw = this.prefix + uniqueResourceName + deploymentIdentifierId;

        if (returnNameRaw.length <= 63) return returnNameRaw;
        else return returnNameRaw.substring(0, 63);
    }
}

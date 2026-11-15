/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { IConstruct } from "constructs";

export interface IamRoleTransformOptions {
    /**
     * Override `RoleName` on every visited role. Defaults to true. The core stack has always named its
     * roles, so turning this off there would replace every role; it is off only for stacks whose roles
     * never carried an explicit name.
     */
    readonly renameRoles?: boolean;
}

/**
 * Applies the `cdk.json` `environments.aws` IAM settings to every `AWS::IAM::Role` it visits, nested
 * stacks included: a deterministic `RoleName` carrying `IamRoleNamePrefix`, and `PermissionsBoundary`
 * set to `PermissionBoundaryArn` when that is non-empty. An empty boundary leaves the property as the
 * construct emitted it, so a boundary applied by any other mechanism is kept.
 */
export class IamRoleTransform implements cdk.IAspect {
    /**
     * Sets `permissionsBoundaryArn` on every `AWS::IAM::Role` under `scope` (the app, so the WAF, API
     * origin verification and cross-Region notification stacks are covered as well as the core
     * stack), including the roles CDK adds during prepare. Role names are left as they are. No-op
     * when the ARN is empty.
     */
    public static applyPermissionsBoundary(
        scope: IConstruct,
        permissionsBoundaryArn: string | undefined
    ): void {
        if (!permissionsBoundaryArn) return;
        const aspect = new IamRoleTransform("", permissionsBoundaryArn, { renameRoles: false });
        cdk.Aspects.of(scope).add(aspect);
        scope.node.addValidation({ validate: () => aspect.boundRolesAddedDuringPrepare(scope) });
    }

    private readonly permissionsBoundaryArn: string;
    private readonly renameRoles: boolean;
    private readonly bounded = new WeakSet<cdk.CfnResource>();
    resource_types: string[];
    prefix: string;
    constructor(
        prefixParam: string,
        permissionBoundaryArn: string | undefined,
        options: IamRoleTransformOptions = {}
    ) {
        this.permissionsBoundaryArn = permissionBoundaryArn || "";
        this.renameRoles = options.renameRoles ?? true;
        this.resource_types = ["AWS::IAM::Role"];
        this.prefix = prefixParam;
    }

    public visit(node: IConstruct): void {
        if (
            cdk.CfnResource.isCfnResource(node) &&
            this.resource_types.includes(node.cfnResourceType)
        ) {
            if (this.renameRoles) {
                this.ApplyOverride(node, "RoleName", this.getRoleName(node));
            }
            if (this.permissionsBoundaryArn !== "") {
                this.ApplyOverride(node, "PermissionsBoundary", this.permissionsBoundaryArn);
                this.bounded.add(node);
            }
        }
    }

    /**
     * Bounds the roles CDK adds while preparing the app, after aspects have run: the cross-Region
     * export reader and writer providers that `crossRegionReferences` creates in a top-level stack.
     * A top-level template is rendered after this runs, so those roles are bounded here. A nested
     * template is already rendered by then, so an unbounded role in a nested stack is returned as an
     * error rather than deployed without the boundary.
     */
    public boundRolesAddedDuringPrepare(scope: IConstruct): string[] {
        if (this.permissionsBoundaryArn === "") return [];
        const errors: string[] = [];
        for (const node of scope.node.findAll()) {
            if (
                !cdk.CfnResource.isCfnResource(node) ||
                !this.resource_types.includes(node.cfnResourceType) ||
                this.bounded.has(node)
            ) {
                continue;
            }
            if (cdk.Stack.of(node).nested) {
                errors.push(
                    `IAM role ${node.node.path} was added to a nested stack after the permissions ` +
                        "boundary was applied, so its template cannot carry the boundary configured " +
                        "in environments.aws.PermissionBoundaryArn."
                );
                continue;
            }
            this.ApplyOverride(node, "PermissionsBoundary", this.permissionsBoundaryArn);
            this.bounded.add(node);
        }
        return errors;
    }
    public ApplyOverride(node: cdk.CfnResource, key: string, value: string) {
        node.addPropertyOverride(key, value);
    }
    public DeleteProperty(node: cdk.CfnResource, property: string) {
        node.addDeletionOverride(property);
    }
    public getRoleName(resource: IConstruct): string {
        const deploymentIdentifierId =
            // "-" +
            // cdk.Stack.of(resource).account.toString() +
            "-" + cdk.Stack.of(resource).region.toString();
        const uniqueResourceName = cdk.Names.uniqueResourceName(resource, {
            maxLength: 40 - this.prefix.length,
        });

        const returnNameRaw = this.prefix + uniqueResourceName + deploymentIdentifierId;

        if (returnNameRaw.length <= 63) return returnNameRaw;
        // When the name exceeds the 63-char IAM limit, truncate the MIDDLE
        // (uniqueResourceName) rather than the tail, so the deployment-identifying
        // suffix (the region, and account if re-enabled above) is always preserved.
        // Truncating from the front would drop that suffix and let roles from
        // different deployments collide on the same name.
        const overflow = returnNameRaw.length - 63;
        const truncatedUnique = uniqueResourceName.substring(
            0,
            Math.max(0, uniqueResourceName.length - overflow)
        );
        return this.prefix + truncatedUnique + deploymentIdentifierId;
    }
}

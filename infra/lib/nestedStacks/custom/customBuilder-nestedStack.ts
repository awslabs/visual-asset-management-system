/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cloudformation from "aws-cdk-lib/aws-cloudformation";
import { NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import { buildCustomResources } from "./customResources";
import { CustomResourcesProps } from "./customStackHelpers";

export type CustomBuilderNestedStackProps = CustomResourcesProps;

/**
 * Holds the resources a fork adds (customResources.ts, fork-owned) in a template of their own, so
 * they count against neither API builder stack's resource and template-size ceilings.
 */
export class CustomBuilderNestedStack extends NestedStack {
    /** CUSTOM_APP_FEATURES values the fork's resources enable, pushed by the core stack. */
    public readonly enabledFeatures: string[];

    constructor(parent: Construct, name: string, props: CustomBuilderNestedStackProps) {
        super(parent, name);

        // CloudFormation rejects a nested template with no resources; a wait-condition handle is a
        // free, permissionless placeholder that keeps the stack valid while it holds nothing.
        new cloudformation.CfnWaitConditionHandle(this, "CustomBuilderPlaceholder");

        const routesBefore = props.registry.list().length;
        this.enabledFeatures = buildCustomResources(this, props).enabledFeatures;

        // Anonymous routes need an entry in the authorizer's ignored-path list, which is core-owned,
        // so a custom route is always served behind the VAMS authorizer.
        const anonymous = props.registry
            .list()
            .slice(routesBefore)
            .filter((route) => route.allowAnonymous)
            .map((route) => `${route.method} ${route.path}`);
        if (anonymous.length > 0) {
            throw new Error(
                `CustomBuilder: custom routes cannot be anonymous (allowAnonymous): ${anonymous.join(
                    ", "
                )}`
            );
        }
    }
}

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cloudformation from "aws-cdk-lib/aws-cloudformation";
import { NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import { VAMS_APP_FEATURES } from "../../../common/vamsAppFeatures";
import { RouteRegistry } from "../apiLambda/apiRouteRegistry";
import { buildCustomResources } from "./customResources";
import { CustomResourcesProps } from "./customStackHelpers";

export type CustomBuilderNestedStackProps = CustomResourcesProps;

/** `METHOD path` of every anonymous route in the registry. */
function anonymousRouteKeys(registry: RouteRegistry): Set<string> {
    return new Set(
        registry
            .list()
            .filter((route) => route.allowAnonymous)
            .map((route) => `${route.method} ${route.path}`)
    );
}

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

        const anonymousBefore = anonymousRouteKeys(props.registry);
        this.enabledFeatures = buildCustomResources(this, props).enabledFeatures;

        // Anonymous routes need an entry in the authorizer's ignored-path list, which is core-owned,
        // so a custom route is always served behind the VAMS authorizer, and no route registered
        // earlier becomes anonymous while the fork's code runs.
        const anonymousAdded = [...anonymousRouteKeys(props.registry)].filter(
            (key) => !anonymousBefore.has(key)
        );
        if (anonymousAdded.length > 0) {
            throw new Error(
                `CustomBuilder: custom routes cannot be anonymous, and no existing route can be made ` +
                    `anonymous (allowAnonymous): ${anonymousAdded.join(", ")}`
            );
        }

        // A core switch is enabled by the core stack only; publishing one from here would tell the
        // web that a core feature is deployed when it is not.
        const coreFeatures = new Set<string>(Object.values(VAMS_APP_FEATURES));
        const coreReturned = this.enabledFeatures.filter((feature) => coreFeatures.has(feature));
        if (coreReturned.length > 0) {
            throw new Error(
                `CustomBuilder: buildCustomResources() returned core VAMS_APP_FEATURES values; ` +
                    `declare custom switches in CUSTOM_APP_FEATURES: ${coreReturned.join(", ")}`
            );
        }
    }
}

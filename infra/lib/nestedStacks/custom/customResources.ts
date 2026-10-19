/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here
 * do not conflict on upgrade.
 *
 * Builds the CDK resources a fork adds, inside the custom nested stack (customBuilder-nestedStack.ts)
 * so they have a template of their own. Lambdas follow the lambda-builder pattern (the five security
 * helpers); routes are registered with attachFunctionToApi(scope, fn, { routePath, method,
 * registry: props.registry }); resource names are published with publishCustomResourceName(); fork
 * settings come from loadCustomConfig() (infra/config/custom/customConfig.json). Return the
 * CUSTOM_APP_FEATURES values the deployment enables.
 */

import { Construct } from "constructs";
import { CustomResourcesProps, CustomResourcesResult } from "./customStackHelpers";

export function buildCustomResources(
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    scope: Construct,
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    props: CustomResourcesProps
): CustomResourcesResult {
    return { enabledFeatures: [] };
}

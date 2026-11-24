/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Publishing for the core resource names whose resources are built after ResourceNamesBuilder
 * materializes the registry: the OpenSearch endpoint and index names (SearchBuilder), the deployed
 * web URL (StaticWeb), the Amazon Location Service map API key ARN (LocationService) and the large
 * object copy engine's function name (ApiBuilder2). The stack that creates the resource publishes
 * the per-key parameter `<resourceNamesSSMParamPrefix>/<key>` and records the name in the registry,
 * so the chunk parameters (resourceNameChunks.ts) carry it with the core names and a Lambda cold
 * start reads it in the one call it already makes. Core-owned.
 *
 * Only non-secret identifiers go through here: the values land in Standard String parameters every
 * VAMS function may read (globalLambdaEnvironmentsAndPermissions grants the prefix and the chunk
 * path). A credential or key material belongs in AWS Secrets Manager, never in a resource name.
 */

import { Stack } from "aws-cdk-lib";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { Construct, IDependable } from "constructs";
import * as Config from "../../../config/config";
import { RESOURCE_PARAM_KEYS } from "../../../common/resourceParamKeys";
import { ResourceNameRegistry } from "./resourceNameRegistry";
import { resourceParamKeySet } from "./laneResourceNames";

function requireCoreKey(fn: string, key: string): void {
    if (
        !resourceParamKeySet(
            RESOURCE_PARAM_KEYS as unknown as Record<string, Record<string, string>>
        ).has(key)
    ) {
        throw new Error(
            `${fn}: "${key}" is not a core resource-name key (RESOURCE_PARAM_KEYS); publish an ` +
                `add-on or custom name with publishAddonResourceName() or publishCustomResourceName()`
        );
    }
}

/**
 * Publish a core resource name from the stack that creates its resource: a Standard String
 * parameter `<prefix>/<key>` in `scope`'s stack, waiting for `options.dependsOn` (the resource the
 * value must not be read before, such as the index the name points to), recorded for the chunk
 * parameters. A key that is not a core key, or one ResourceNamesBuilder or another stack already
 * publishes, throws at synth.
 */
export function publishLateCoreResourceName(
    scope: Construct,
    config: Config.Config,
    key: string,
    value: string,
    options: { dependsOn?: IDependable[] } = {}
): ssm.StringParameter {
    requireCoreKey("publishLateCoreResourceName", key);
    const parameter = new ssm.StringParameter(
        scope,
        `LateCoreResourceName-${key.replace(/\//g, "-")}`,
        {
            parameterName: `${config.resourceNamesSSMParamPrefix}/${key}`,
            stringValue: value,
            tier: ssm.ParameterTier.STANDARD,
        }
    );
    for (const dependency of options.dependsOn ?? []) {
        parameter.node.addDependency(dependency);
    }
    recordLateCoreResourceName(parameter, key, value);
    return parameter;
}

/**
 * Record a core resource name whose per-key parameter already exists (the copy engine's
 * `LargeObjectCopyEngineFunctionNameParameter`), so the chunk parameters carry it, without creating
 * a second parameter: a new construct with the same parameter name would fail the deploy with
 * ParameterAlreadyExists, and a changed construct id would replace the parameter. A scope outside a
 * VAMS core stack (a unit test's bare stack) has no registry; its name is published per key only.
 */
export function recordLateCoreResourceName(
    parameter: ssm.StringParameter,
    key: string,
    value: string
): void {
    requireCoreKey("recordLateCoreResourceName", key);
    ResourceNameRegistry.of(parameter)?.recordLateCore({
        paramKey: key,
        value,
        owner: Stack.of(parameter),
    });
}

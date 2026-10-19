/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Publishing for the resource-name keys the add-on and custom lanes declare
 * (common/addonResourceParamKeys.ts, common/customResourceParamKeys.ts). Core keys
 * (RESOURCE_PARAM_KEYS) are published by ResourceNamesBuilder; an add-on or custom key is published
 * by the stack that creates its resource, through publishAddonResourceName() or
 * publishCustomResourceName(), which both land here. Core-owned.
 */

import * as ssm from "aws-cdk-lib/aws-ssm";
import { Construct } from "constructs";
import * as Config from "../../../config/config";
import { ADDON_RESOURCE_PARAM_KEYS } from "../../../common/addonResourceParamKeys";
import { CUSTOM_RESOURCE_PARAM_KEYS } from "../../../common/customResourceParamKeys";
import { RESOURCE_PARAM_KEYS } from "../../../common/resourceParamKeys";

/** The two lanes beside the core keys; each is checked against the core keys and the other lane. */
export type ResourceNameLane = "addon" | "custom";

const LANES: Record<
    ResourceNameLane,
    {
        keys: Record<string, Record<string, string>>;
        label: string;
        article: string;
        idPrefix: string;
        fn: string;
    }
> = {
    addon: {
        keys: ADDON_RESOURCE_PARAM_KEYS,
        label: "add-on",
        article: "an add-on",
        idPrefix: "AddonResourceName",
        fn: "publishAddonResourceName",
    },
    custom: {
        keys: CUSTOM_RESOURCE_PARAM_KEYS,
        label: "custom",
        article: "a custom",
        idPrefix: "CustomResourceName",
        fn: "publishCustomResourceName",
    },
};

/** Every key suffix across every category of a key registry. */
export function resourceParamKeySet(registry: Record<string, Record<string, string>>): Set<string> {
    return new Set(Object.values(registry).flatMap((category) => Object.values(category)));
}

/**
 * Publish a lane's resource name as an SSM parameter under the deployment's resource-name prefix,
 * where backend handlers resolve it with get_resource_name(). A key equal to a core key, or to a key
 * the other lane declares, throws: that parameter already exists or will, and the deploy would
 * otherwise fail part-way through.
 */
export function publishLaneResourceName(
    lane: ResourceNameLane,
    scope: Construct,
    config: Config.Config,
    key: string,
    value: string
): ssm.StringParameter {
    const { fn, idPrefix } = LANES[lane];
    if (!key) {
        throw new Error(`${fn}: key must be a non-empty SSM key suffix`);
    }
    if (
        resourceParamKeySet(
            RESOURCE_PARAM_KEYS as unknown as Record<string, Record<string, string>>
        ).has(key)
    ) {
        throw new Error(
            `${fn}: "${key}" is a core VAMS resource-name key; ` +
                `declare the ${LANES[lane].label} resource under a key of its own`
        );
    }
    const other: ResourceNameLane = lane === "addon" ? "custom" : "addon";
    if (resourceParamKeySet(LANES[other].keys).has(key)) {
        throw new Error(
            `${fn}: "${key}" is declared as ${LANES[other].article} resource-name key; ` +
                `declare the ${LANES[lane].label} resource under a key of its own`
        );
    }
    return new ssm.StringParameter(scope, `${idPrefix}-${key.replace(/\//g, "-")}`, {
        parameterName: `${config.resourceNamesSSMParamPrefix}/${key}`,
        stringValue: value,
    });
}

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Helpers and types for the fork extension point in customResources.ts. Core-owned: the fork edits
 * customResources.ts only, and imports what it needs from here.
 */

import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { existsSync, readFileSync } from "fs";
import { join } from "path";
import * as Config from "../../../config/config";
import { publishLaneResourceName } from "../resourceNames/laneResourceNames";
import { RouteRegistry } from "../apiLambda/apiRouteRegistry";
import { storageResources } from "../storage/storageBuilder-nestedStack";

/** What the core stack hands the fork's buildCustomResources(). */
export interface CustomResourcesProps {
    config: Config.Config;
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    vpc: ec2.IVpc;
    isolatedSubnets: ec2.ISubnet[];
    privateSubnets: ec2.ISubnet[];
    /** The shared route registry; register custom routes through attachFunctionToApi(). */
    registry: RouteRegistry;
}

/** What buildCustomResources() returns to the core stack. */
export interface CustomResourcesResult {
    /** CUSTOM_APP_FEATURES values to publish through /api/secure-config. */
    enabledFeatures: string[];
}

const CUSTOM_CONFIG_PATH = join(__dirname, "../../../config/custom/customConfig.json");

/**
 * The fork's settings from infra/config/custom/customConfig.json, or `{}` when the file is absent.
 * The file is outside ConfigPublic: getConfig() and the shipped config templates do not read it.
 */
export function loadCustomConfig(configPath: string = CUSTOM_CONFIG_PATH): Record<string, any> {
    if (!existsSync(configPath)) {
        return {};
    }
    let parsed: unknown;
    try {
        parsed = JSON.parse(readFileSync(configPath, "utf8"));
    } catch (error) {
        throw new Error(
            `Configuration Error: ${configPath} is not valid JSON: ${(error as Error).message}`
        );
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error(`Configuration Error: ${configPath} must contain a JSON object`);
    }
    return parsed as Record<string, any>;
}

/**
 * Publish a custom resource name as an SSM parameter under the deployment's resource-name prefix,
 * where backend handlers resolve it with get_resource_name(). `key` is a suffix declared in
 * CUSTOM_RESOURCE_PARAM_KEYS (common/customResourceParamKeys.ts). A key equal to a core key or to an
 * add-on key (ADDON_RESOURCE_PARAM_KEYS) throws: that parameter already exists or will, and the
 * deploy would otherwise fail part-way through.
 */
export function publishCustomResourceName(
    scope: Construct,
    config: Config.Config,
    key: string,
    value: string
): ssm.StringParameter {
    return publishLaneResourceName("custom", scope, config, key, value);
}

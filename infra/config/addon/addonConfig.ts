/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The add-on lane of the deployment configuration. `ConfigPublic.app.addons` is the core add-on block
 * intersected with `ConfigAddonLane`, and `getConfig()` calls the functions below where it fills in
 * defaults, warns about CodeBuild partition support, collects the features that require a VPC, and
 * validates. Each add-on keeps its interfaces, defaults and validation in its own module in this
 * directory; this file composes them.
 */
import type { Config } from "../config";
import type { ConfigEdhWorkstations } from "./edhWorkstationsConfig";
import {
    applyEdhWorkstationsDefaults,
    edhVpcRequiringFeatures,
    validateEdhWorkstationsConfig,
    warnEdhImageBuildsOutsideCodeBuildPartitions,
} from "./edhWorkstationsConfig";

/** The add-on members of `ConfigPublic.app.addons`. */
export interface ConfigAddonLane {
    useEdhWorkstations: ConfigEdhWorkstations;
}

/** Fills in each add-on block's defaults. */
export function applyAddonConfigDefaults(config: Config): void {
    applyEdhWorkstationsDefaults(config);
}

/** Warns about add-on image builds in a partition outside the in-account CodeBuild partitions. */
export function warnAddonCodeBuildPartition(config: Config, resolvedPartition: string): void {
    warnEdhImageBuildsOutsideCodeBuildPartitions(config, resolvedPartition);
}

/** The enabled add-on features that require a VPC, named as getConfig() reports them. */
export function addonVpcRequiringFeatures(config: Config): string[] {
    return [...edhVpcRequiringFeatures(config)];
}

/** Validates each add-on block. */
export function validateAddonConfig(config: Config): void {
    validateEdhWorkstationsConfig(config);
}

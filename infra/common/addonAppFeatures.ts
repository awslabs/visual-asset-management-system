/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Feature switches for the optional add-ons (nestedStacks/addon/), published through
 * /api/secure-config with the core VAMS_APP_FEATURES (vamsAppFeatures.ts) and a fork's
 * CUSTOM_APP_FEATURES (customAppFeatures.ts). AddonBuilderNestedStack enables each one when its
 * add-on is deployed. Upstream-owned; mirrored for the CLI by vamscli/addon_constants.py and for
 * the web by web/src/addons/addonFeatures.ts. A value here must not repeat a core or custom value
 * (infra/test/addon/appFeatureLanes.test.ts).
 */
export enum ADDON_APP_FEATURES {
    PHYSNA_ADDON = "PHYSNA_ADDON",
    EDH_WORKSTATIONS = "EDH_WORKSTATIONS",
    EDH_CONTAINERS = "EDH_CONTAINERS",
    EDH_AMI_RECIPES = "EDH_AMI_RECIPES",
}

/**
 * The environment variable the core stack sets on the Lambdas that list API routes and permission
 * object types (the constraint editor's lists and the allowed-route checks): the enabled add-on
 * switches, comma-separated, empty when no add-on is deployed. Those listings leave out the entries of
 * an add-on that is not deployed (backend common/addonFeatures.py reads it).
 */
export const ADDON_FEATURES_ENABLED_ENV = "ADDON_FEATURES_ENABLED";

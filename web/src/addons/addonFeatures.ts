/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Names of the add-ons' feature switches (infra/common/addonAppFeatures.ts), in the shape of
 * common/constants/featuresEnabled.ts. Upstream-owned. A page checks one against the
 * `featuresEnabled` list from /api/secure-config exactly as it checks a core switch, and a
 * navigation entry (addons/addonNavigation.ts) lists the ones it needs in its
 * `featuresEnabledRestriction`. A value here must be declared in ADDON_APP_FEATURES and must not
 * repeat a core or custom name (addonFeatures.test.ts).
 */
export const addonFeaturesEnabled = {
    EDH_WORKSTATIONS: "EDH_WORKSTATIONS",
    EDH_CONTAINERS: "EDH_CONTAINERS",
    EDH_AMI_RECIPES: "EDH_AMI_RECIPES",
};

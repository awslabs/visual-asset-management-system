/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Feature switches for the optional add-ons (nestedStacks/addon/), published through
 * /api/secure-config with the core VAMS_APP_FEATURES (vamsAppFeatures.ts) and a fork's
 * CUSTOM_APP_FEATURES (customAppFeatures.ts). AddonBuilderNestedStack enables each one when its
 * add-on is deployed. Upstream-owned; mirrored for the CLI by vamscli/addon_constants.py. A value
 * here must not repeat a core or custom value (infra/test/addon/appFeatureLanes.test.ts).
 */
export enum ADDON_APP_FEATURES {
    PHYSNA_ADDON = "PHYSNA_ADDON",
}

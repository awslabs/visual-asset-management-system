/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here
 * do not conflict on upgrade.
 *
 * Names of the feature switches a fork adds (infra/common/customAppFeatures.ts), in the shape of
 * common/constants/featuresEnabled.ts. A page checks one against the `featuresEnabled` list from
 * /api/secure-config exactly as it checks a core switch.
 */

export const customFeaturesEnabled: Record<string, string> = {};

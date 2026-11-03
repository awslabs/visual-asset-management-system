/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * SSM resource-name keys for the resources the optional add-ons (nestedStacks/addon/) create, in the
 * same category shape as RESOURCE_PARAM_KEYS (resourceParamKeys.ts). Upstream-owned. Mirrored by
 * backend/backend/common/addonResourceNames.py (AddonResourceKeys) — keep the two identical; an
 * add-on stack publishes each value with publishAddonResourceName()
 * (nestedStacks/addon/addonStackHelpers.ts). Key suffixes under `addon/` are recommended; a key
 * equal to a core or custom key fails at synth.
 */
export const ADDON_RESOURCE_PARAM_KEYS: Record<string, Record<string, string>> = {};

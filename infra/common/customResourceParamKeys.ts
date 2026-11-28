/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here
 * do not conflict on upgrade.
 *
 * SSM parameter key suffixes (relative to config.resourceNamesSSMParamPrefix) for the resources a
 * fork adds, in the same category shape as RESOURCE_PARAM_KEYS (resourceParamKeys.ts). Mirrored by
 * backend/backend/custom/customResourceNames.py — keep the two identical; the custom CDK stack
 * publishes each value with publishCustomResourceName(). Key suffixes under `custom/` are
 * recommended so a later core key does not collide with one declared here.
 */
export const CUSTOM_RESOURCE_PARAM_KEYS: Record<string, Record<string, string>> = {};

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here
 * do not conflict on upgrade.
 *
 * Feature switches for the features a fork adds, published through /api/secure-config with the
 * core VAMS_APP_FEATURES (vamsAppFeatures.ts). The custom CDK stack returns the enabled ones from
 * buildCustomResources(); the web reads them from web/src/custom/customFeatures.ts and the CLI from
 * vamscli/custom_constants.py (FEATURE_CUSTOM_*). Names prefixed `CUSTOM_` are recommended so a
 * later core switch does not collide with one declared here.
 */
export enum CUSTOM_APP_FEATURES {}

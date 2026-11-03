/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here
 * do not conflict on upgrade.
 *
 * API calls for the routes a fork adds. src/custom/services/ is a service layer like src/services/:
 * files here may import `apiClient` (../../services/apiClient), and components and pages call the
 * functions exported here rather than importing `apiClient` themselves. Follow the patterns in
 * services/APIService.ts.
 */

export {};

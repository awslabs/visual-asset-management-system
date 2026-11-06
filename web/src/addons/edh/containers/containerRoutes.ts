/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Route templates as `backend/backend/common/addonApiRoutes.py` declares them; `can()` matches them
// segment by segment against the caller's allowed routes.
export const CONTAINERS_CATALOG_ROUTE = "/addon/edh/containers/catalog";
export const CONTAINERS_JOBS_ROUTE = "/addon/edh/containers/jobs";
export const CONTAINERS_JOB_ROUTE = "/addon/edh/containers/jobs/{jobId}";

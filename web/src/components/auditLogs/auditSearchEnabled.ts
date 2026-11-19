/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { appCache } from "../../services/appCache";
import { featuresEnabled, normalizeFeaturesEnabled } from "../../common/constants/featuresEnabled";

/** Shown in place of the page when the deployment has Audit Log Search switched off. */
export const AUDIT_SEARCH_NOT_ENABLED_MESSAGE =
    "Audit Log Search is not enabled on this deployment";

/**
 * Whether this deployment has Audit Log Search (the AUDITLOGSEARCH feature switch the API publishes
 * in /secure-config). The switch only avoids showing a page whose API routes are not deployed. It
 * grants nothing: who may open the page is decided by the API (the web-route and API-route decisions),
 * and the API enforces every call.
 */
export function isAuditLogSearchEnabled(config: any = appCache.getItem("config")): boolean {
    return normalizeFeaturesEnabled(config?.featuresEnabled).includes(
        featuresEnabled.AUDITLOGSEARCH
    );
}

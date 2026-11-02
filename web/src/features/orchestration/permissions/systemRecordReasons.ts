/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The `disabledReason` of each action the API refuses on a system (deployment-owned) pipeline or
 * workflow (`isSystem`), paraphrasing the 400 messages in `common/workflows/systemRecords.py`. A page
 * keeps such an action listed and disabled with its reason rather than hiding it.
 *
 * Tier-1 is applied first: an action whose route the role may not call is graded by the route check
 * (hidden or given its route reason), whatever the record; only an action the role may call is graded
 * on the record. The API still refuses each of these actions on its own.
 */
export const SYSTEM_RECORD_REASONS = {
    archivePipeline: "System pipelines are managed by the deployment and cannot be archived.",
    archiveWorkflow: "System workflows are managed by the deployment and cannot be archived.",
    createTemplate: "Templates cannot be added to a system pipeline.",
    deleteTemplate: "Templates of a system pipeline cannot be deleted.",
    addTrigger: "Triggers cannot be added to a system workflow.",
    deleteTrigger:
        "Triggers of a system workflow cannot be deleted. Use Edit to switch the trigger off.",
} as const;

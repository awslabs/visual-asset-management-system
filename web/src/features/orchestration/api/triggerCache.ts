/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { QueryClient } from "@tanstack/react-query";
import type { Workflow } from "../types";

/**
 * A trigger write changes what the workflow queries report as well as the trigger list: the workflow
 * LIST rows carry server-computed triggerCount/triggersEnabledCount (which the list's cards and
 * trigger facet read), and the single-workflow response embeds the trigger rows themselves. All
 * three are invalidated so a saved or deleted trigger is not contradicted by a cached workflow.
 *
 * The keys are spelled out here rather than taken from the `qk` factory so this module stands on
 * its own: it is called from components whose hook module is replaced wholesale in their tests.
 */
export function invalidateTriggerQueries(
    queryClient: QueryClient,
    databaseId: string,
    workflowId: string
): void {
    queryClient.invalidateQueries({ queryKey: ["triggers", databaseId, workflowId] });
    queryClient.invalidateQueries({ queryKey: ["workflows"] });
    queryClient.invalidateQueries({ queryKey: ["workflow", databaseId, workflowId] });
}

/**
 * Writes a freshly created workflow into the single-workflow entry `useWorkflow` reads, so the edit
 * route renders it from the create response instead of waiting on the first GET. A missing id is a
 * response this cannot address, so nothing is written.
 */
export function seedWorkflowCache(
    queryClient: QueryClient,
    databaseId: string,
    workflowId: string,
    workflow: Workflow
): void {
    if (!databaseId || !workflowId) return;
    queryClient.setQueryData(["workflow", databaseId, workflowId], workflow);
}

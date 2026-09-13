/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { QueryClient } from "@tanstack/react-query";
import { invalidateTriggerQueries, seedWorkflowCache } from "./triggerCache";
import { qk } from "./queries";
import type { Workflow } from "../types";

/** The query keys a helper invalidated, as arrays, in call order. */
const invalidatedKeys = (spy: jest.SpyInstance) =>
    spy.mock.calls.map((call) => (call[0] as any)?.queryKey);

describe("invalidateTriggerQueries", () => {
    // triggerCount/triggersEnabledCount are computed per LIST row and the single-workflow response
    // embeds the trigger rows, so a trigger write that invalidated only the trigger list would leave
    // a cached workflow contradicting the trigger just written.
    it("invalidates the trigger list and both workflow query families, in that order", () => {
        const queryClient = new QueryClient();
        const spy = jest.spyOn(queryClient, "invalidateQueries");

        invalidateTriggerQueries(queryClient, "db1", "wf-1");

        expect(invalidatedKeys(spy)).toEqual([
            ["triggers", "db1", "wf-1"],
            ["workflows"],
            ["workflow", "db1", "wf-1"],
        ]);
    });

    it("spells the per-workflow keys exactly as the qk factory does", () => {
        const queryClient = new QueryClient();
        const spy = jest.spyOn(queryClient, "invalidateQueries");

        invalidateTriggerQueries(queryClient, "db1", "wf-1");

        expect(invalidatedKeys(spy)).toContainEqual([...qk.triggers("db1", "wf-1")]);
        expect(invalidatedKeys(spy)).toContainEqual([...qk.workflow("db1", "wf-1")]);
    });
});

describe("seedWorkflowCache", () => {
    const workflow: Workflow = {
        databaseId: "db1",
        workflowId: "wf-1",
        workflowName: "Seeded",
        specifiedPipelines: [],
    };

    it("writes the workflow under the key useWorkflow reads", () => {
        const queryClient = new QueryClient();

        seedWorkflowCache(queryClient, "db1", "wf-1", workflow);

        expect(queryClient.getQueryData(qk.workflow("db1", "wf-1"))).toEqual(workflow);
    });

    it("writes nothing when the workflow id is empty", () => {
        const queryClient = new QueryClient();

        seedWorkflowCache(queryClient, "db1", "", workflow);

        expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    });
});

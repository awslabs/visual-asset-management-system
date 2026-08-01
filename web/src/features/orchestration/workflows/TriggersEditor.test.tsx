/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TriggersEditor from "./TriggersEditor";
import type { WorkflowTrigger } from "../types";

jest.mock("../api/queries", () => ({
    useTriggers: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false })),
}));

jest.mock("../api/workflows", () => ({
    setTrigger: jest.fn(),
    deleteTrigger: jest.fn(),
}));

const pipelineRefs = [{ pipelineId: "p1", pipelineDatabaseId: "db1" }];

const renderEditor = (triggers: WorkflowTrigger[]) => {
    const { useTriggers } = require("../api/queries");
    useTriggers.mockReturnValue({ data: triggers });
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
        <QueryClientProvider client={queryClient}>
            <TriggersEditor databaseId="db1" workflowId="wf-1" pipelineRefs={pipelineRefs} />
        </QueryClientProvider>
    );
};

describe("TriggersEditor", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { setTrigger, deleteTrigger } = require("../api/workflows");
        setTrigger.mockResolvedValue([true, {}]);
        deleteTrigger.mockResolvedValue([true, {}]);
    });

    it("creates a trigger enabled, matching the backend default", async () => {
        const { setTrigger } = require("../api/workflows");
        renderEditor([]);

        await userEvent.click(screen.getByRole("button", { name: /create file upload trigger/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(setTrigger).toHaveBeenCalled());
        // enabled:false would be stored verbatim and _trigger_fires() would never fire.
        expect(setTrigger.mock.calls[0][3].enabled).toBe(true);
    });

    it("drops default templates for pipelines no longer in the workflow", async () => {
        const { setTrigger } = require("../api/workflows");
        renderEditor([
            {
                triggerType: "fileUpload",
                enabled: true,
                defaultTemplateIds: { "db1:p1": "t1", "db1:removed": "t9" },
            } as WorkflowTrigger,
        ]);

        await userEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
        await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));

        await waitFor(() => expect(setTrigger).toHaveBeenCalled());
        expect(setTrigger.mock.calls[0][3].defaultTemplateIds).toEqual({ "db1:p1": "t1" });
    });

    it("clears the form after a delete so the next create starts empty", async () => {
        const { useTriggers } = require("../api/queries");
        renderEditor([
            {
                triggerType: "fileUpload",
                enabled: true,
                inputFileFilters: { allow: ["*.glb"], exclude: ["*.tmp"] },
            } as WorkflowTrigger,
        ]);

        await userEvent.click(await screen.findByRole("button", { name: /^delete$/i }));
        // The row is gone once the delete succeeds, so the hydration effect never runs again.
        useTriggers.mockReturnValue({ data: [] });
        await userEvent.click(await screen.findByRole("button", { name: /^delete$/i }));

        await userEvent.click(
            await screen.findByRole("button", { name: /create file upload trigger/i })
        );

        expect(await screen.findByText("Edit File Upload Trigger")).toBeInTheDocument();
        expect(screen.queryByText("*.glb")).not.toBeInTheDocument();
        expect(screen.queryByText("*.tmp")).not.toBeInTheDocument();
    });
});

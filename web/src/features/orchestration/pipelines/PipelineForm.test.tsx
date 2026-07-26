/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PipelineForm from "./PipelineForm";

jest.mock("../api/queries", () => ({
    useCreatePipeline: jest.fn(() => ({
        mutateAsync: jest.fn(),
    })),
    useUpdatePipeline: jest.fn(() => ({
        mutateAsync: jest.fn(),
    })),
}));

jest.mock("../../../services/appCache", () => ({
    appCache: {
        getItem: jest.fn(() => ({
            featuresEnabled: [],
        })),
    },
}));

const createWrapper = () => {
    const queryClient = new QueryClient({
        defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
        },
    });
    return ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
};

describe("PipelineForm", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("renders create form with Lambda as default", () => {
        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        expect(screen.getByText("Create Pipeline")).toBeInTheDocument();
        expect(screen.getByLabelText(/Pipeline Name/)).toBeInTheDocument();
    });

    it("shows DeadlineCloud option when feature flag is enabled", async () => {
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({
            featuresEnabled: ["DEADLINECLOUD_PIPELINES"],
        });

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        const select = screen.getByLabelText(/Execution Type/);
        expect(select).toBeInTheDocument();

        const options = Array.from((select as HTMLSelectElement).options).map((opt) => opt.value);
        expect(options).toContain("DeadlineCloud");
    });

    it("hides DeadlineCloud option when feature flag is absent", () => {
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({
            featuresEnabled: [],
        });

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        const select = screen.getByLabelText(/Execution Type/);
        const options = Array.from((select as HTMLSelectElement).options).map((opt) => opt.value);
        expect(options).not.toContain("DeadlineCloud");
    });

    it("hides DeadlineCloud option when GOVCLOUD flag is present", () => {
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({
            featuresEnabled: ["DEADLINECLOUD_PIPELINES", "GOVCLOUD"],
        });

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        const select = screen.getByLabelText(/Execution Type/);
        const options = Array.from((select as HTMLSelectElement).options).map((opt) => opt.value);
        expect(options).not.toContain("DeadlineCloud");
    });

    it("shows DeadlineCloud fields when selected and locks waitForCallback to Enabled", async () => {
        const user = userEvent.setup();
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({
            featuresEnabled: ["DEADLINECLOUD_PIPELINES"],
        });

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        const select = screen.getByLabelText(/Execution Type/);
        await user.selectOptions(select, "DeadlineCloud");

        await waitFor(() => {
            expect(screen.getByLabelText(/Farm ID/)).toBeInTheDocument();
            expect(screen.getByLabelText(/Queue ID/)).toBeInTheDocument();
        });

        const waitForCallbackSelect = screen.getByLabelText(/Wait For Callback/);
        expect(waitForCallbackSelect).toHaveValue("Enabled");
        expect(waitForCallbackSelect).toBeDisabled();
    });

    it("shows Lambda resource ID field with disclosure text when Lambda is selected", () => {
        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        expect(screen.getByLabelText(/Lambda Resource ID/)).toBeInTheDocument();
        expect(screen.getByText(/Leave blank to auto-provision a new Lambda/)).toBeInTheDocument();
    });

    it("shows SQS queue URL field when SQS is selected", async () => {
        const user = userEvent.setup();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        const select = screen.getByLabelText(/Execution Type/);
        await user.selectOptions(select, "SQS");

        await waitFor(() => {
            expect(screen.getByLabelText(/Queue URL/)).toBeInTheDocument();
        });
    });

    it("shows EventBridge fields when EventBridge is selected", async () => {
        const user = userEvent.setup();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        const select = screen.getByLabelText(/Execution Type/);
        await user.selectOptions(select, "EventBridge");

        await waitFor(() => {
            expect(screen.getByLabelText(/Event Bus ARN/)).toBeInTheDocument();
            expect(screen.getByLabelText(/Source/)).toBeInTheDocument();
            expect(screen.getByLabelText(/Detail Type/)).toBeInTheDocument();
        });
    });

    it("shows DeadlineCloud option in edit mode even when flag is off, but makes form read-only", () => {
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({
            featuresEnabled: [],
        });

        const dcPipeline = {
            pipelineId: "dc1",
            pipelineName: "DC Pipeline",
            databaseId: "db1",
            executionConfig: {
                executionType: "DeadlineCloud" as const,
                waitForCallback: "Enabled" as const,
                deadlineCloud: {
                    farmId: "farm-123",
                    queueId: "queue-456",
                },
            },
            systemConfig: {
                inputFileArity: "one" as const,
                assetScope: {},
                metadataInputs: {},
                requireTemplate: false,
                allowCustomTemplateOverride: false,
                inputFileFilters: { allow: [], exclude: [] },
            },
        };

        render(
            <PipelineForm mode="edit" databaseId="db1" initial={dcPipeline} onDone={jest.fn()} />,
            {
                wrapper: createWrapper(),
            }
        );

        const select = screen.getByLabelText(/Execution Type/);
        const options = Array.from((select as HTMLSelectElement).options).map((opt) => opt.value);
        expect(options).toContain("DeadlineCloud");

        expect(select).toBeDisabled();

        expect(screen.getByText(/Read-only:/)).toBeInTheDocument();
        expect(screen.getByText(/DeadlineCloud feature is disabled/)).toBeInTheDocument();

        expect(screen.queryByText(/Update/i)).not.toBeInTheDocument();
    });

    it("allows editing a DeadlineCloud pipeline when flag is on", () => {
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({
            featuresEnabled: ["DEADLINECLOUD_PIPELINES"],
        });

        const dcPipeline = {
            pipelineId: "dc1",
            pipelineName: "DC Pipeline",
            databaseId: "db1",
            executionConfig: {
                executionType: "DeadlineCloud" as const,
                waitForCallback: "Enabled" as const,
                deadlineCloud: {
                    farmId: "farm-123",
                    queueId: "queue-456",
                },
            },
            systemConfig: {
                inputFileArity: "one" as const,
                assetScope: {},
                metadataInputs: {},
                requireTemplate: false,
                allowCustomTemplateOverride: false,
                inputFileFilters: { allow: [], exclude: [] },
            },
        };

        render(
            <PipelineForm mode="edit" databaseId="db1" initial={dcPipeline} onDone={jest.fn()} />,
            {
                wrapper: createWrapper(),
            }
        );

        const select = screen.getByLabelText(/Execution Type/);
        expect(select).not.toBeDisabled();

        expect(screen.queryByText(/Read-only:/)).not.toBeInTheDocument();

        expect(screen.getByText(/Update/)).toBeInTheDocument();
    });

    it("shows a non-blocking warning banner and holds navigation when a save returns warnings", async () => {
        const user = userEvent.setup();
        const warning =
            "pipeline 'New Pipe' requires a template and is part of auto-triggered workflow 'db1:wf1' (trigger 'fileUpload'), but that trigger has not chosen a default template for it.";
        const mutateAsync = jest
            .fn()
            .mockResolvedValue({ pipeline: { pipelineId: "p1" }, warnings: [warning] });
        const onDone = jest.fn();
        const { useCreatePipeline } = require("../api/queries");
        (useCreatePipeline as jest.Mock).mockReturnValue({ mutateAsync });

        render(<PipelineForm mode="create" databaseId="db1" onDone={onDone} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "New Pipe");
        // The optional timeout fields reject an empty string, so give them valid values. Both share
        // the "1-604800" placeholder (Task Timeout, Task Heartbeat Timeout).
        const timeouts = screen.getAllByPlaceholderText(/1-604800/);
        await user.type(timeouts[0], "3600");
        await user.type(timeouts[1], "60");
        // The submit button lives in the Dialog footer (associated via form=), which jsdom does not
        // fully honour — submit the form element directly.
        fireEvent.submit(document.getElementById("pipeline-form")!);

        // Warning banner shows; the form has NOT navigated away yet (save succeeded).
        await waitFor(() => {
            expect(screen.getByText(/Pipeline saved with warnings/)).toBeInTheDocument();
        });
        expect(screen.getByText(/requires a template/)).toBeInTheDocument();
        expect(onDone).not.toHaveBeenCalled();

        // Acknowledging closes the form.
        await user.click(screen.getByRole("button", { name: /Acknowledge/ }));
        expect(onDone).toHaveBeenCalled();
    });

    it("closes immediately (no warning banner) when a save returns no warnings", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest
            .fn()
            .mockResolvedValue({ pipeline: { pipelineId: "p1" }, warnings: [] });
        const onDone = jest.fn();
        const { useCreatePipeline } = require("../api/queries");
        (useCreatePipeline as jest.Mock).mockReturnValue({ mutateAsync });

        render(<PipelineForm mode="create" databaseId="db1" onDone={onDone} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "New Pipe");
        const timeouts = screen.getAllByPlaceholderText(/1-604800/);
        await user.type(timeouts[0], "3600");
        await user.type(timeouts[1], "60");
        fireEvent.submit(document.getElementById("pipeline-form")!);

        await waitFor(() => expect(onDone).toHaveBeenCalled());
        expect(screen.queryByText(/saved with warnings/)).not.toBeInTheDocument();
    });
});

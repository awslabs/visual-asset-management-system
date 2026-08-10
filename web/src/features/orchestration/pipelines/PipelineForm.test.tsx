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
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    Wrapper.displayName = "TestQueryClientWrapper";
    return Wrapper;
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

    it("gates DeadlineCloud on its own feature flag alone, not on GOVCLOUD", () => {
        // The web layer deliberately does NOT re-check GOVCLOUD: getConfig() refuses to synthesize a
        // stack that enables Deadline Cloud in GovCloud or any non-'aws' partition, so the two flags
        // cannot legitimately co-exist. The flag's presence is therefore sufficient on its own.
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({
            featuresEnabled: ["DEADLINECLOUD_PIPELINES"],
        });

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        const select = screen.getByLabelText(/Execution Type/);
        const options = Array.from((select as HTMLSelectElement).options).map((opt) => opt.value);
        expect(options).toContain("DeadlineCloud");
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
        // Both timeout inputs share the "1-604800" placeholder (Task Timeout, Task Heartbeat
        // Timeout).
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

    it("sends pipelineId as null in the create body when the user supplied none", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest.fn().mockResolvedValue({ pipeline: { pipelineId: "gen" } });
        const { useCreatePipeline } = require("../api/queries");
        (useCreatePipeline as jest.Mock).mockReturnValue({ mutateAsync });

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "New Pipe");
        const timeouts = screen.getAllByPlaceholderText(/1-604800/);
        await user.type(timeouts[0], "3600");
        await user.type(timeouts[1], "60");
        fireEvent.submit(document.getElementById("pipeline-form")!);

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        const body = mutateAsync.mock.calls[0][0];
        // The backend auto-generates an id for null, but rejects an empty string (min_length=1) —
        // sending "" makes every create fail with a 400.
        expect(body.pipelineId).toBeNull();
        expect(body.databaseId).toBe("db1");
        expect(body.pipelineName).toBe("New Pipe");
    });

    it("submits with both timeout fields left blank", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest.fn().mockResolvedValue({ pipeline: { pipelineId: "p1" } });
        const { useCreatePipeline } = require("../api/queries");
        (useCreatePipeline as jest.Mock).mockReturnValue({ mutateAsync });

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "New Pipe");
        fireEvent.submit(document.getElementById("pipeline-form")!);

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(screen.queryByText(/Must be an integer between/)).not.toBeInTheDocument();
    });

    it("submits a DeadlineCloud pipeline with the optional numeric fields left blank", async () => {
        const user = userEvent.setup();
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({ featuresEnabled: ["DEADLINECLOUD_PIPELINES"] });
        const mutateAsync = jest.fn().mockResolvedValue({ pipeline: { pipelineId: "p1" } });
        const { useCreatePipeline } = require("../api/queries");
        (useCreatePipeline as jest.Mock).mockReturnValue({ mutateAsync });

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "New Pipe");
        await user.selectOptions(screen.getByLabelText(/Execution Type/), "DeadlineCloud");
        await user.type(screen.getByLabelText(/Farm ID/), "farm-1");
        await user.type(screen.getByLabelText(/Queue ID/), "queue-1");
        await user.type(screen.getByLabelText(/Job Template/), "specificationVersion: x");
        fireEvent.submit(document.getElementById("pipeline-form")!);

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        const dc = mutateAsync.mock.calls[0][0].executionConfig.deadlineCloud;
        expect(dc.priority).toBeUndefined();
        expect(dc.maxRetriesPerTask).toBeUndefined();
        expect(dc.maxFailedTasksCount).toBeUndefined();
    });

    it("does not submit again after a save that returned warnings", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest
            .fn()
            .mockResolvedValue({ pipeline: { pipelineId: "p1" }, warnings: ["heads up"] });
        const { useCreatePipeline } = require("../api/queries");
        (useCreatePipeline as jest.Mock).mockReturnValue({ mutateAsync });

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "New Pipe");
        fireEvent.submit(document.getElementById("pipeline-form")!);

        await waitFor(() => {
            expect(screen.getByText(/Pipeline saved with warnings/)).toBeInTheDocument();
        });
        // The entity already exists — the footer submit is withdrawn and a further submit is a no-op.
        expect(screen.queryByRole("button", { name: /^Create$/ })).not.toBeInTheDocument();
        fireEvent.submit(document.getElementById("pipeline-form")!);
        await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    });

    it("re-seeds every field when the edited pipeline changes without a remount", async () => {
        const mutateAsync = jest.fn().mockResolvedValue({ pipeline: { pipelineId: "b" } });
        const { useUpdatePipeline } = require("../api/queries");
        (useUpdatePipeline as jest.Mock).mockReturnValue({ mutateAsync });

        const pipelineA = {
            pipelineId: "aaa",
            pipelineName: "Alpha",
            databaseId: "db1",
            category: "catA",
            executionConfig: {
                executionType: "Lambda" as const,
                waitForCallback: "Disabled" as const,
            },
            systemConfig: { inputFileFilters: { allow: [".a"], exclude: [] } },
        };
        const pipelineB = {
            pipelineId: "bbb",
            pipelineName: "Beta",
            databaseId: "db1",
            category: "catB",
            executionConfig: {
                executionType: "Lambda" as const,
                waitForCallback: "Disabled" as const,
            },
            systemConfig: { inputFileFilters: { allow: [".b"], exclude: [] } },
        };

        const Wrapper = createWrapper();
        const { rerender } = render(
            <Wrapper>
                <PipelineForm mode="edit" databaseId="db1" initial={pipelineA} onDone={jest.fn()} />
            </Wrapper>
        );
        expect(screen.getByLabelText(/Pipeline Name/)).toHaveValue("Alpha");

        rerender(
            <Wrapper>
                <PipelineForm mode="edit" databaseId="db1" initial={pipelineB} onDone={jest.fn()} />
            </Wrapper>
        );

        await waitFor(() => {
            expect(screen.getByLabelText(/Pipeline Name/)).toHaveValue("Beta");
        });
        fireEvent.submit(document.getElementById("pipeline-form")!);

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        const call = mutateAsync.mock.calls[0][0];
        expect(call.pipelineId).toBe("bbb");
        expect(call.body.pipelineName).toBe("Beta");
        expect(call.body.category).toBe("catB");
        expect(call.body.systemConfig.inputFileFilters.allow).toEqual([".b"]);
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

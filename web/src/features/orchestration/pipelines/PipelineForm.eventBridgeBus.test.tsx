/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PipelineForm from "./PipelineForm";

// The event bus of an EventBridge pipeline is optional in the API: an absent value publishes to the
// account's default event bus (backend models/pipelines.py _validate_execution_config, and
// common/workflows/workflowAsl.py, which maps a missing or empty busArn to "default"). Only the format
// of a supplied value is checked. The form must accept a pipeline without one, on create and on edit,
// and must not send the empty input as "" (#1030).

jest.mock("../api/queries", () => ({
    useCreatePipeline: jest.fn(() => ({ mutateAsync: jest.fn() })),
    useUpdatePipeline: jest.fn(() => ({ mutateAsync: jest.fn() })),
}));

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));

const createWrapper = () => {
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    // The page variant renders a Breadcrumb of router Links.
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <MemoryRouter>
            <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
        </MemoryRouter>
    );
    Wrapper.displayName = "TestQueryClientWrapper";
    return Wrapper;
};

const mockCreate = () => {
    const mutateAsync = jest.fn().mockResolvedValue({ pipeline: { pipelineId: "p1" } });
    const { useCreatePipeline } = require("../api/queries");
    (useCreatePipeline as jest.Mock).mockReturnValue({ mutateAsync });
    return mutateAsync;
};

const mockUpdate = () => {
    const mutateAsync = jest.fn().mockResolvedValue({ pipeline: { pipelineId: "p1" } });
    const { useUpdatePipeline } = require("../api/queries");
    (useUpdatePipeline as jest.Mock).mockReturnValue({ mutateAsync });
    return mutateAsync;
};

const submit = () => fireEvent.submit(document.getElementById("pipeline-form")!);
const nextButton = () => screen.getByRole("button", { name: "Next" });

const BUS_ARN = "arn:aws:events:us-east-1:123456789012:event-bus/my-bus";

/** An EventBridge pipeline as the API stores it, with all four sub-blocks. */
const storedEventBridgePipeline = (eventBridge: Record<string, string>) => ({
    databaseId: "db1",
    pipelineId: "eb-pipe",
    pipelineName: "EB Pipe",
    category: "",
    description: "",
    enabled: true,
    executionConfig: {
        executionType: "EventBridge" as const,
        waitForCallback: "Disabled" as const,
        taskTimeout: "",
        taskHeartbeatTimeout: "",
        lambda: {},
        sqs: {},
        eventBridge,
        deadlineCloud: {},
    },
    systemConfig: {
        inputFileArity: "one" as const,
        assetScope: {},
        metadataInputs: {},
        requireTemplate: false,
        allowCustomTemplateOverride: false,
        inputFileFilters: { allow: [], exclude: [] },
    },
});

/** Create-mode dialog with EventBridge selected and the two required fields filled. */
const fillEventBridgeCreate = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.type(screen.getByLabelText(/Pipeline Name/), "EB Pipe");
    await user.selectOptions(screen.getByLabelText(/Execution Type/), "EventBridge");
    await waitFor(() => expect(screen.getByLabelText(/Event Bus ARN/)).toBeInTheDocument());
    await user.type(screen.getByLabelText(/Source/), "vams.test");
    await user.type(screen.getByLabelText(/Detail Type/), "PipelineExecution");
};

describe("PipelineForm EventBridge event bus is optional (#1030)", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({ featuresEnabled: [] });
    });

    it("labels the bus field without the required marker and explains the empty value", async () => {
        const user = userEvent.setup();
        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.selectOptions(screen.getByLabelText(/Execution Type/), "EventBridge");

        // Exact label text: the required fields of the same type keep their asterisk.
        await waitFor(() => expect(screen.getByLabelText("Event Bus ARN")).toBeInTheDocument());
        expect(screen.getByLabelText("Source *")).toBeInTheDocument();
        expect(screen.getByLabelText("Detail Type *")).toBeInTheDocument();
        expect(
            screen.getByText("Leave empty to use the account's default event bus")
        ).toBeInTheDocument();
    });

    it("creates a pipeline with no bus and leaves busArn out of the request", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();
        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await fillEventBridgeCreate(user);
        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        // toStrictEqual: a `busArn: undefined` key would fail this as well as `busArn: ""`.
        expect(mutateAsync.mock.calls[0][0].executionConfig.eventBridge).toStrictEqual({
            source: "vams.test",
            detailType: "PipelineExecution",
        });
    });

    it("saves an API-created pipeline without a bus unchanged", async () => {
        const mutateAsync = mockUpdate();
        const stored = storedEventBridgePipeline({
            source: "vams.test",
            detailType: "PipelineExecution",
        });
        render(<PipelineForm mode="edit" databaseId="db1" initial={stored} onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await waitFor(() => expect(screen.getByLabelText(/Event Bus ARN/)).toHaveValue(""));
        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0].body.executionConfig.eventBridge).toStrictEqual(
            stored.executionConfig.eventBridge
        );
        expect(screen.queryByText(/is required/)).not.toBeInTheDocument();
    });

    it("drops the bus from the request when an edit clears it", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockUpdate();
        const stored = storedEventBridgePipeline({
            busArn: BUS_ARN,
            source: "vams.test",
            detailType: "PipelineExecution",
        });
        render(<PipelineForm mode="edit" databaseId="db1" initial={stored} onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.clear(screen.getByLabelText(/Event Bus ARN/));
        submit();

        // The update replaces the stored eventBridge block, so an absent bus is the default bus.
        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0].body.executionConfig.eventBridge).toStrictEqual({
            source: "vams.test",
            detailType: "PipelineExecution",
        });
    });

    it("still rejects a malformed bus ARN when one is entered", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();
        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await fillEventBridgeCreate(user);
        await user.type(screen.getByLabelText(/Event Bus ARN/), "not-an-arn");
        submit();

        await waitFor(() =>
            expect(screen.getByText(/Must be a valid event-bus ARN/)).toBeInTheDocument()
        );
        expect(mutateAsync).not.toHaveBeenCalled();
    });

    it("sends a valid bus ARN as entered", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();
        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await fillEventBridgeCreate(user);
        await user.click(screen.getByLabelText(/Event Bus ARN/));
        await user.paste(BUS_ARN);
        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0].executionConfig.eventBridge).toStrictEqual({
            busArn: BUS_ARN,
            source: "vams.test",
            detailType: "PipelineExecution",
        });
    });

    describe("page wizard", () => {
        it("enables Next on the Execution step with a source and detail type but no bus", async () => {
            const user = userEvent.setup();
            const mutateAsync = mockCreate();
            render(
                <PipelineForm mode="create" databaseId="db1" variant="page" onDone={jest.fn()} />,
                { wrapper: createWrapper() }
            );

            await user.type(screen.getByLabelText(/Pipeline Name/), "EB Pipe");
            await user.click(nextButton());
            await user.selectOptions(screen.getByLabelText(/Execution Type/), "EventBridge");
            await waitFor(() => expect(screen.getByLabelText(/Event Bus ARN/)).toBeInTheDocument());

            // Source and detail type stay required: Next is held until both are set.
            expect(nextButton()).toBeDisabled();
            await user.type(screen.getByLabelText(/Source/), "vams.test");
            expect(nextButton()).toBeDisabled();
            await user.type(screen.getByLabelText(/Detail Type/), "PipelineExecution");
            expect(nextButton()).toBeEnabled();

            await user.click(nextButton());
            await waitFor(() =>
                expect(screen.getByRole("button", { name: "Create" })).toBeInTheDocument()
            );
            await user.click(screen.getByRole("button", { name: "Create" }));

            await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
            expect(mutateAsync.mock.calls[0][0].executionConfig.eventBridge).toStrictEqual({
                source: "vams.test",
                detailType: "PipelineExecution",
            });
        });

        it("opens and saves an API-created pipeline without a bus in the edit wizard", async () => {
            const user = userEvent.setup();
            const mutateAsync = mockUpdate();
            const stored = storedEventBridgePipeline({
                source: "vams.test",
                detailType: "PipelineExecution",
            });
            render(
                <PipelineForm
                    mode="edit"
                    databaseId="db1"
                    variant="page"
                    initial={stored}
                    onDone={jest.fn()}
                />,
                { wrapper: createWrapper() }
            );

            await user.click(nextButton());
            await waitFor(() => expect(screen.getByLabelText(/Event Bus ARN/)).toHaveValue(""));
            expect(nextButton()).toBeEnabled();

            await user.click(nextButton());
            await waitFor(() =>
                expect(screen.getByRole("button", { name: "Update" })).toBeInTheDocument()
            );
            await user.click(screen.getByRole("button", { name: "Update" }));

            await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
            expect(mutateAsync.mock.calls[0][0].body.executionConfig.eventBridge).toStrictEqual(
                stored.executionConfig.eventBridge
            );
        });
    });
});

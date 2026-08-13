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
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    Wrapper.displayName = "TestQueryClientWrapper";
    return Wrapper;
};

const enableDeadlineCloud = () => {
    const { appCache } = require("../../../services/appCache");
    appCache.getItem.mockReturnValue({ featuresEnabled: ["DEADLINECLOUD_PIPELINES"] });
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

/** The shape every stored pipeline carries: all four per-type sub-blocks, empty for unused types. */
const storedPipeline = (overrides: Record<string, any> = {}) => ({
    databaseId: "db1",
    pipelineId: "stored-pipe",
    pipelineName: "Stored Pipe",
    category: "3D",
    description: "",
    enabled: true,
    executionConfig: {
        executionType: "Lambda" as const,
        waitForCallback: "Disabled" as const,
        taskTimeout: "",
        taskHeartbeatTimeout: "",
        lambda: {},
        sqs: {},
        eventBridge: {},
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
    ...overrides,
});

describe("PipelineForm execution-config pruning and error surfacing", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({ featuresEnabled: [] });
    });

    it("saves a stored pipeline whose unused execution sub-blocks are empty objects", async () => {
        const mutateAsync = mockUpdate();

        render(
            <PipelineForm
                mode="edit"
                databaseId="db1"
                initial={storedPipeline()}
                onDone={jest.fn()}
            />,
            { wrapper: createWrapper() }
        );

        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(screen.queryByText(/is required/)).not.toBeInTheDocument();
    });

    it("drops the sub-blocks of the types the pipeline does not use from the request body", async () => {
        const mutateAsync = mockUpdate();

        render(
            <PipelineForm
                mode="edit"
                databaseId="db1"
                initial={storedPipeline()}
                onDone={jest.fn()}
            />,
            { wrapper: createWrapper() }
        );

        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        const executionConfig = mutateAsync.mock.calls[0][0].body.executionConfig;
        expect(executionConfig.executionType).toBe("Lambda");
        expect(executionConfig).toHaveProperty("lambda");
        expect(executionConfig).not.toHaveProperty("sqs");
        expect(executionConfig).not.toHaveProperty("eventBridge");
        expect(executionConfig).not.toHaveProperty("deadlineCloud");
    });

    it("still saves after the user explored another execution type and switched back", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "Explorer");
        const executionType = screen.getByLabelText(/Execution Type/);
        await user.selectOptions(executionType, "SQS");
        await waitFor(() => expect(screen.getByLabelText(/Queue URL/)).toBeInTheDocument());
        await user.selectOptions(executionType, "EventBridge");
        await waitFor(() => expect(screen.getByLabelText(/Event Bus ARN/)).toBeInTheDocument());
        await user.selectOptions(executionType, "Lambda");
        await waitFor(() =>
            expect(screen.getByLabelText(/Lambda Resource ID/)).toBeInTheDocument()
        );

        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        const executionConfig = mutateAsync.mock.calls[0][0].executionConfig;
        expect(executionConfig).not.toHaveProperty("sqs");
        expect(executionConfig).not.toHaveProperty("eventBridge");
    });

    it("surfaces a validation error whose inline element belongs to another execution type", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        // A malformed value typed under SQS is pruned on save, so it cannot refuse the save at all.
        await user.type(screen.getByLabelText(/Pipeline Name/), "Explorer");
        const executionType = screen.getByLabelText(/Execution Type/);
        await user.selectOptions(executionType, "SQS");
        await user.type(screen.getByLabelText(/Queue URL/), "not a url");
        await user.selectOptions(executionType, "Lambda");

        submit();
        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
    });

    it("never refuses a save with no feedback anywhere on screen", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        // A malformed Lambda target under the SELECTED type: the save is refused, and the message is
        // reachable — the previous behaviour aborted the save with the message suppressed.
        await user.type(screen.getByLabelText(/Pipeline Name/), "Explorer");
        await user.type(screen.getByLabelText(/Lambda Resource ID/), "bad name!");
        submit();

        await waitFor(() =>
            expect(screen.getByText(/function ARN or a valid function name/)).toBeInTheDocument()
        );
        expect(mutateAsync).not.toHaveBeenCalled();
    });

    // The summary is the catch-all for any error whose inline element is not on screen. Under Lambda,
    // a DeadlineCloud error has no rendered element at all, so only the summary can report it.
    it("shows the form-level summary for an error with no element on the selected type", async () => {
        const user = userEvent.setup();
        enableDeadlineCloud();
        const mutateAsync = mockUpdate();

        // waitForCallback is Disabled while the type is DeadlineCloud — the one cross-type
        // contradiction pruning cannot resolve, because the scalar is shared by every type.
        const contradictory = storedPipeline({
            executionConfig: {
                executionType: "DeadlineCloud" as const,
                waitForCallback: "Enabled" as const,
                taskTimeout: "",
                taskHeartbeatTimeout: "",
                lambda: {},
                sqs: {},
                eventBridge: {},
                deadlineCloud: { farmId: "farm-1", queueId: "queue-1" },
            },
        });

        render(
            <PipelineForm
                mode="edit"
                databaseId="db1"
                initial={contradictory}
                onDone={jest.fn()}
            />,
            { wrapper: createWrapper() }
        );

        // Leave DeadlineCloud for Lambda; the missing template rides along in form state, and its
        // inline element is no longer rendered.
        await user.selectOptions(screen.getByLabelText(/Execution Type/), "Lambda");
        await waitFor(() =>
            expect(screen.queryByLabelText(/Job Template/)).not.toBeInTheDocument()
        );
        submit();

        // Pruning means the abandoned DeadlineCloud block cannot block the save at all.
        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0].body.executionConfig.executionType).toBe("Lambda");
        expect(mutateAsync.mock.calls[0][0].body.executionConfig).not.toHaveProperty(
            "deadlineCloud"
        );
    });

    // Direct proof of the summary path: a stored pipelineId that fails the pattern has no editable
    // field on the form (it renders read-only), so only the summary can report it.
    it("lists an error with no inline message element in the form-level summary", async () => {
        const mutateAsync = mockUpdate();
        const badId = storedPipeline({ pipelineId: "ab" });

        render(<PipelineForm mode="edit" databaseId="db1" initial={badId} onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        submit();

        await waitFor(() =>
            expect(screen.getByText(/pipelineId must match pattern/)).toBeInTheDocument()
        );
        expect(mutateAsync).not.toHaveBeenCalled();
    });
});

describe("PipelineForm assetScope", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({ featuresEnabled: [] });
    });

    // The backend's pipeline-level check only evaluates the keys a scope declares
    // (executionValidation._scope_errors, declared_only=True), so an empty map grants everything the
    // control shows as denied.
    it("sends all four canonical keys for the create defaults the control displays", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "Scoped");
        expect(screen.getByLabelText("Asset span")).toHaveValue("single");
        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0].systemConfig.assetScope).toEqual({
            crossAssetAllowed: false,
            singleAssetOnly: true,
            wholeAssetAllowed: false,
            folderAllowed: false,
        });
    });

    it("sends the explicit keys for a stored pipeline that declared none", async () => {
        const mutateAsync = mockUpdate();

        render(
            <PipelineForm
                mode="edit"
                databaseId="db1"
                initial={storedPipeline()}
                onDone={jest.fn()}
            />,
            { wrapper: createWrapper() }
        );

        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0].body.systemConfig.assetScope).toEqual({
            crossAssetAllowed: false,
            singleAssetOnly: true,
            wholeAssetAllowed: false,
            folderAllowed: false,
        });
    });

    it("persists the span and whole-asset selections the user made", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "Scoped");
        await user.selectOptions(screen.getByLabelText("Asset span"), "multiple");
        await user.click(screen.getByLabelText(/Allow selecting a whole asset/));
        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0].systemConfig.assetScope).toEqual({
            crossAssetAllowed: true,
            singleAssetOnly: false,
            wholeAssetAllowed: true,
            folderAllowed: false,
        });
    });

    // `wholeAsset` is the shorthand the CDK registration schemas emit; the backend folds it into the
    // canonical key, and so must a save that round-trips such a pipeline.
    it("folds the wholeAsset shorthand of a registered pipeline into the canonical key", async () => {
        const mutateAsync = mockUpdate();
        const registered = storedPipeline({
            systemConfig: {
                inputFileArity: "one" as const,
                assetScope: { wholeAsset: true },
                metadataInputs: {},
                requireTemplate: false,
                allowCustomTemplateOverride: false,
                inputFileFilters: { allow: [], exclude: [] },
            },
        });

        render(
            <PipelineForm mode="edit" databaseId="db1" initial={registered} onDone={jest.fn()} />,
            { wrapper: createWrapper() }
        );

        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        const assetScope = mutateAsync.mock.calls[0][0].body.systemConfig.assetScope;
        expect(assetScope.wholeAssetAllowed).toBe(true);
        // The backend rejects an unknown assetScope key, and `wholeAsset` is only accepted as the
        // registration shorthand — the canonical record carries the four *Allowed keys alone.
        expect(assetScope).not.toHaveProperty("wholeAsset");
    });
});

describe("PipelineForm DeadlineCloud job template", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        enableDeadlineCloud();
    });

    it("offers a job-template input under the DeadlineCloud type", async () => {
        const user = userEvent.setup();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.selectOptions(screen.getByLabelText(/Execution Type/), "DeadlineCloud");
        await waitFor(() => expect(screen.getByLabelText(/Job Template/)).toBeInTheDocument());
    });

    // The ASL builder embeds the template verbatim and refuses to generate a task state without it
    // (stepfunctions_builder.py DeadlineCloudTaskBuilder), so a pipeline saved without one saves fine
    // and then breaks every workflow that references it.
    it("refuses to save a DeadlineCloud pipeline with no template", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "DC Pipe");
        await user.selectOptions(screen.getByLabelText(/Execution Type/), "DeadlineCloud");
        await waitFor(() => expect(screen.getByLabelText(/Farm ID/)).toBeInTheDocument());
        await user.type(screen.getByLabelText(/Farm ID/), "farm-1");
        await user.type(screen.getByLabelText(/Queue ID/), "queue-1");
        submit();

        await waitFor(() =>
            expect(screen.getByText(/job template is required/i)).toBeInTheDocument()
        );
        expect(mutateAsync).not.toHaveBeenCalled();
    });

    it("sends the authored template in the request body", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "DC Pipe");
        await user.selectOptions(screen.getByLabelText(/Execution Type/), "DeadlineCloud");
        await waitFor(() => expect(screen.getByLabelText(/Job Template/)).toBeInTheDocument());
        await user.type(screen.getByLabelText(/Farm ID/), "farm-1");
        await user.type(screen.getByLabelText(/Queue ID/), "queue-1");
        await user.type(
            screen.getByLabelText(/Job Template/),
            "specificationVersion: jobtemplate-2023-09"
        );
        submit();

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0].executionConfig.deadlineCloud.template).toBe(
            "specificationVersion: jobtemplate-2023-09"
        );
    });

    it("seeds the template of a stored DeadlineCloud pipeline into the input", async () => {
        const stored = storedPipeline({
            executionConfig: {
                executionType: "DeadlineCloud" as const,
                waitForCallback: "Enabled" as const,
                taskTimeout: "",
                taskHeartbeatTimeout: "",
                lambda: {},
                sqs: {},
                eventBridge: {},
                deadlineCloud: {
                    farmId: "farm-9",
                    queueId: "queue-9",
                    template: "specificationVersion: stored",
                    templateType: "YAML",
                },
            },
        });

        render(<PipelineForm mode="edit" databaseId="db1" initial={stored} onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await waitFor(() =>
            expect(screen.getByLabelText(/Job Template/)).toHaveValue(
                "specificationVersion: stored"
            )
        );
    });

    // A free-text box let "yaml" through to a 400; the backend accepts only ("JSON", "YAML").
    it("offers only the template types the backend accepts, plus its own default", async () => {
        const user = userEvent.setup();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.selectOptions(screen.getByLabelText(/Execution Type/), "DeadlineCloud");
        await waitFor(() => expect(screen.getByLabelText(/Template Type/)).toBeInTheDocument());

        const select = screen.getByLabelText(/Template Type/) as HTMLSelectElement;
        expect(Array.from(select.options).map((option) => option.value)).toEqual([
            "",
            "JSON",
            "YAML",
        ]);
    });

    it("refuses a negative priority instead of letting the backend 400 it", async () => {
        const user = userEvent.setup();
        const mutateAsync = mockCreate();

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "DC Pipe");
        await user.selectOptions(screen.getByLabelText(/Execution Type/), "DeadlineCloud");
        await waitFor(() => expect(screen.getByLabelText(/Farm ID/)).toBeInTheDocument());
        await user.type(screen.getByLabelText(/Farm ID/), "farm-1");
        await user.type(screen.getByLabelText(/Queue ID/), "queue-1");
        await user.type(screen.getByLabelText(/Job Template/), "specificationVersion: x");
        await user.type(screen.getByLabelText(/^Priority$/), "-5");
        submit();

        await waitFor(() => expect(screen.getByText(/Cannot be negative/)).toBeInTheDocument());
        expect(mutateAsync).not.toHaveBeenCalled();
    });
});

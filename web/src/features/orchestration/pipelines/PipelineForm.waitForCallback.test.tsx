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
import type { Pipeline } from "../types";

// A pipeline stored without executionConfig.waitForCallback runs with the callback disabled: the API
// reads an absent value as Disabled (backend models/pipelines.py PipelineExecutionConfigModel,
// common/workflows/workflowAsl.py to_asl_pipeline_dict), and the state machine adds the
// wait-for-callback task only for "Enabled". A pipeline created through the API or CLI without the
// field is stored that way. The edit form must show it as Disabled and must not save it as Enabled,
// which would make every workflow using the pipeline wait for a task token (#1032).

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

const enableDeadlineCloud = () => {
    const { appCache } = require("../../../services/appCache");
    appCache.getItem.mockReturnValue({ featuresEnabled: ["DEADLINECLOUD_PIPELINES"] });
};

const submit = () => fireEvent.submit(document.getElementById("pipeline-form")!);
const nextButton = () => screen.getByRole("button", { name: "Next" });
const callbackSelect = () => screen.getByLabelText("Wait For Callback");

// Each type's own block as `vamscli pipeline create --execution-config` stores it: the record holds
// exactly the JSON that was sent, so a pipeline created without waitForCallback has no such key.
const TYPE_BLOCKS = {
    Lambda: { lambda: { resourceId: "my-function" } },
    SQS: { sqs: { queueUrl: "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue" } },
    EventBridge: { eventBridge: { source: "vams.test", detailType: "PipelineExecution" } },
} as const;
type NonDeadlineType = keyof typeof TYPE_BLOCKS;
const NON_DEADLINE_TYPES = Object.keys(TYPE_BLOCKS) as NonDeadlineType[];

const DEADLINE_BLOCK = {
    deadlineCloud: {
        farmId: "farm-1",
        queueId: "queue-1",
        template: "specificationVersion: jobtemplate-2023-09\nname: job\nsteps: []",
    },
};

const storedPipeline = (
    executionConfig: Record<string, unknown>,
    extra: Partial<Pipeline> = {}
): Partial<Pipeline> =>
    ({
        databaseId: "db1",
        pipelineId: "cli-pipe",
        pipelineName: "CLI Pipe",
        enabled: true,
        executionConfig,
        systemConfig: { inputFileArity: "one" },
        ...extra,
    } as Partial<Pipeline>);

/** A stored pipeline of `type`, with `waitForCallback` set only when given. */
const pipelineOfType = (type: NonDeadlineType, waitForCallback?: string) =>
    storedPipeline({
        executionType: type,
        ...TYPE_BLOCKS[type],
        ...(waitForCallback === undefined ? {} : { waitForCallback }),
    });

const renderEdit = (initial: Partial<Pipeline>, variant: "dialog" | "page" = "dialog") =>
    render(
        <PipelineForm
            mode="edit"
            databaseId="db1"
            variant={variant}
            initial={initial}
            onDone={jest.fn()}
        />,
        { wrapper: createWrapper() }
    );

/** Page wizard: Basic -> Execution, where the callback select renders. */
const openExecutionStep = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(nextButton());
    await waitFor(() => expect(callbackSelect()).toBeInTheDocument());
};

/** Page wizard, from the Execution step: on to Settings and save. */
const saveFromExecutionStep = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(nextButton());
    await waitFor(() => expect(screen.getByRole("button", { name: "Update" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Update" }));
};

const sentCallback = (mutateAsync: jest.Mock) =>
    mutateAsync.mock.calls[0][0].body.executionConfig.waitForCallback;

describe("PipelineForm Wait For Callback for a stored pipeline without the field (#1032)", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({ featuresEnabled: [] });
    });

    describe("dialog", () => {
        it.each(NON_DEADLINE_TYPES)(
            "shows a %s pipeline without the field as Disabled and saves Disabled",
            async (type) => {
                const mutateAsync = mockUpdate();
                renderEdit(pipelineOfType(type));

                await waitFor(() => expect(callbackSelect()).toHaveValue("Disabled"));
                submit();

                await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
                expect(sentCallback(mutateAsync)).toBe("Disabled");
            }
        );

        it.each(["", null])(
            "treats a stored %p value, which the API also reads as Disabled, as Disabled",
            async (stored) => {
                const mutateAsync = mockUpdate();
                renderEdit(
                    storedPipeline({
                        executionType: "Lambda",
                        ...TYPE_BLOCKS.Lambda,
                        waitForCallback: stored,
                    })
                );

                await waitFor(() => expect(callbackSelect()).toHaveValue("Disabled"));
                submit();

                await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
                expect(sentCallback(mutateAsync)).toBe("Disabled");
            }
        );

        it.each(["Enabled", "Disabled"])("keeps an explicit %s as stored", async (stored) => {
            const mutateAsync = mockUpdate();
            renderEdit(pipelineOfType("EventBridge", stored));

            await waitFor(() => expect(callbackSelect()).toHaveValue(stored));
            submit();

            await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
            expect(sentCallback(mutateAsync)).toBe(stored);
        });

        it("still saves the value the user picks", async () => {
            const user = userEvent.setup();
            const mutateAsync = mockUpdate();
            renderEdit(pipelineOfType("Lambda"));

            await waitFor(() => expect(callbackSelect()).toHaveValue("Disabled"));
            await user.selectOptions(callbackSelect(), "Enabled");
            submit();

            await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
            expect(sentCallback(mutateAsync)).toBe("Enabled");
        });

        it("re-seeds Disabled when the form switches to another pipeline without the field", async () => {
            const mutateAsync = mockUpdate();
            const { rerender } = renderEdit(pipelineOfType("Lambda", "Enabled"));
            await waitFor(() => expect(callbackSelect()).toHaveValue("Enabled"));

            rerender(
                <PipelineForm
                    mode="edit"
                    databaseId="db1"
                    initial={{ ...pipelineOfType("SQS"), pipelineId: "other-pipe" }}
                    onDone={jest.fn()}
                />
            );

            await waitFor(() => expect(callbackSelect()).toHaveValue("Disabled"));
            submit();
            await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
            expect(sentCallback(mutateAsync)).toBe("Disabled");
        });

        it("shows a system pipeline without the field as Disabled in the view page", async () => {
            renderEdit({ ...pipelineOfType("Lambda"), isSystem: true });

            await waitFor(() => expect(callbackSelect()).toHaveValue("Disabled"));
        });

        it("creates with Disabled shown and sent, the API's own default", async () => {
            const user = userEvent.setup();
            const mutateAsync = mockCreate();
            render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
                wrapper: createWrapper(),
            });

            expect(callbackSelect()).toHaveValue("Disabled");
            await user.type(screen.getByLabelText(/Pipeline Name/), "New Pipe");
            submit();

            await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
            expect(mutateAsync.mock.calls[0][0].executionConfig.waitForCallback).toBe("Disabled");
        });
    });

    describe("DeadlineCloud stays locked to Enabled", () => {
        it.each([undefined, "Enabled"])(
            "shows and saves Enabled for a stored value of %p",
            async (stored) => {
                enableDeadlineCloud();
                const mutateAsync = mockUpdate();
                renderEdit(
                    storedPipeline({
                        executionType: "DeadlineCloud",
                        ...DEADLINE_BLOCK,
                        ...(stored === undefined ? {} : { waitForCallback: stored }),
                    })
                );

                await waitFor(() => expect(callbackSelect()).toHaveValue("Enabled"));
                expect(callbackSelect()).toBeDisabled();
                submit();

                await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
                expect(sentCallback(mutateAsync)).toBe("Enabled");
            }
        );
    });

    describe("page wizard", () => {
        it.each(NON_DEADLINE_TYPES)(
            "shows a %s pipeline without the field as Disabled and saves Disabled",
            async (type) => {
                const user = userEvent.setup();
                const mutateAsync = mockUpdate();
                renderEdit(pipelineOfType(type), "page");

                await openExecutionStep(user);
                expect(callbackSelect()).toHaveValue("Disabled");
                await saveFromExecutionStep(user);

                await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
                expect(sentCallback(mutateAsync)).toBe("Disabled");
            }
        );

        it.each(["Enabled", "Disabled"])("keeps an explicit %s as stored", async (stored) => {
            const user = userEvent.setup();
            const mutateAsync = mockUpdate();
            renderEdit(pipelineOfType("SQS", stored), "page");

            await openExecutionStep(user);
            expect(callbackSelect()).toHaveValue(stored);
            await saveFromExecutionStep(user);

            await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
            expect(sentCallback(mutateAsync)).toBe(stored);
        });

        it("shows and saves Enabled for a DeadlineCloud pipeline without the field", async () => {
            const user = userEvent.setup();
            enableDeadlineCloud();
            const mutateAsync = mockUpdate();
            renderEdit(
                storedPipeline({ executionType: "DeadlineCloud", ...DEADLINE_BLOCK }),
                "page"
            );

            await openExecutionStep(user);
            await waitFor(() => expect(callbackSelect()).toHaveValue("Enabled"));
            expect(callbackSelect()).toBeDisabled();
            await saveFromExecutionStep(user);

            await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
            expect(sentCallback(mutateAsync)).toBe("Enabled");
        });
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TemplateForm, { TemplateFormEditLoader } from "./TemplateForm";
import type { Template } from "../types";

const mockUpdate = jest.fn();
const mockCreate = jest.fn();

jest.mock("../api/queries", () => ({
    useTemplate: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false })),
    usePipeline: jest.fn(() => ({ data: { pipelineName: "P1" } })),
    useTemplateMutations: jest.fn(() => ({
        createTemplate: { mutateAsync: mockCreate, isPending: false },
        updateTemplate: { mutateAsync: mockUpdate, isPending: false },
    })),
}));

jest.mock("react-router-dom", () => ({
    useNavigate: () => jest.fn(),
    Link: ({ children }: any) => <span>{children}</span>,
}));

// Monaco is lazy/heavy — stub it to a plain textarea.
jest.mock("../components/ConfigEditor", () => ({
    __esModule: true,
    default: ({ value }: any) => <textarea data-testid="config-editor" value={value} readOnly />,
}));

const createWrapper = () => {
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    Wrapper.displayName = "TestQueryWrapper";
    return Wrapper;
};

/** A template as returned by the single-template GET: tagSchema present, bodies rehydrated. */
const fullTemplate: Template = {
    pipelineDatabaseId: "db1",
    pipelineId: "p1",
    templateId: "t1",
    templateName: "Existing",
    description: "d",
    configFormat: "json",
    configBody: '{"a":1}',
    webFormJson: "[]",
    allowCustomEdit: false,
    inputInstructions: "",
    overrides: {},
    isDefault: false,
    tagSchema: [{ tagKey: "prompt", type: "string", required: true }],
} as unknown as Template;

const advanceToSaveAndSubmit = async () => {
    const user = userEvent.setup();
    // Basic -> Configuration -> Tags -> Review, then Save.
    for (let i = 0; i < 3; i++) {
        await user.click(screen.getByRole("button", { name: "Next" }));
    }
    await user.click(screen.getByRole("button", { name: "Save" }));
};

describe("TemplateForm — tag schema and body preservation", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        // clearAllMocks resets calls but keeps a mockReturnValue, so a test that overrides a query
        // hook would leak its pipeline into every later test.
        const { usePipeline } = require("../api/queries");
        usePipeline.mockReturnValue({ data: { pipelineName: "P1" } });
    });

    it("loads the edit form from the single-template GET, not the list", () => {
        const { useTemplate, useTemplates } = require("../api/queries");
        useTemplate.mockReturnValue({ data: fullTemplate, isLoading: false });

        render(<TemplateFormEditLoader databaseId="db1" pipelineId="p1" templateId="t1" />, {
            wrapper: createWrapper(),
        });

        // The list response omits tagSchema and blanks S3-offloaded bodies, so the edit path must
        // not source its initial values from it.
        expect(useTemplate).toHaveBeenCalledWith("db1", "p1", "t1");
        expect(useTemplates).not.toHaveBeenCalled();
    });

    it("preserves an existing tagSchema through an edit that does not touch tags", async () => {
        render(
            <TemplateForm mode="edit" databaseId="db1" pipelineId="p1" initial={fullTemplate} />,
            { wrapper: createWrapper() }
        );

        await advanceToSaveAndSubmit();

        await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
        const body = mockUpdate.mock.calls[0][0].body;
        // Must NOT send an empty schema — that would erase the stored tag definitions.
        expect(body.tagSchema).toEqual(fullTemplate.tagSchema);
    });

    it("omits tagSchema entirely when the form never loaded one", async () => {
        // Simulates a hydration source that carries no tagSchema (e.g. a light list descriptor):
        // the field must be omitted so the backend preserves whatever is stored.
        const withoutSchema = { ...fullTemplate, tagSchema: undefined } as unknown as Template;

        render(
            <TemplateForm mode="edit" databaseId="db1" pipelineId="p1" initial={withoutSchema} />,
            { wrapper: createWrapper() }
        );

        await advanceToSaveAndSubmit();

        await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
        const body = mockUpdate.mock.calls[0][0].body;
        expect("tagSchema" in body).toBe(false);
        expect("webFormJson" in body).toBe(false);
    });

    it("leaves an independently authored webFormJson untouched by an edit that does not touch tags", async () => {
        const authored = {
            ...fullTemplate,
            webFormJson: '{"fields":[{"name":"prompt"}]}',
        } as unknown as Template;

        render(<TemplateForm mode="edit" databaseId="db1" pipelineId="p1" initial={authored} />, {
            wrapper: createWrapper(),
        });

        await advanceToSaveAndSubmit();

        await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
        const body = mockUpdate.mock.calls[0][0].body;
        // A non-None webFormJson is an authoritative body rewrite on the backend.
        expect("webFormJson" in body).toBe(false);
    });

    it("rewrites webFormJson from the tag schema once the tags are edited", async () => {
        const user = userEvent.setup();

        render(
            <TemplateForm mode="edit" databaseId="db1" pipelineId="p1" initial={fullTemplate} />,
            { wrapper: createWrapper() }
        );

        // Basic -> Configuration -> Tags.
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.type(screen.getByLabelText(/^label/i), "Prompt");
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
        const body = mockUpdate.mock.calls[0][0].body;
        expect(JSON.parse(body.webFormJson)).toEqual(body.tagSchema);
    });

    it("hands the pipeline's arity and filters to the overrides editor", async () => {
        // The override REPLACES the pipeline's value per key, so the editor can only seed from the
        // pipeline's settings if the form passes them through.
        const { usePipeline } = require("../api/queries");
        usePipeline.mockReturnValue({
            data: {
                pipelineName: "P1",
                systemConfig: {
                    inputFileArity: "multi",
                    inputFileFilters: { allow: ["*.glb"], exclude: [] },
                },
            },
        });
        const user = userEvent.setup();

        render(
            <TemplateForm mode="edit" databaseId="db1" pipelineId="p1" initial={fullTemplate} />,
            {
                wrapper: createWrapper(),
            }
        );

        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("checkbox", { name: /Override input file count/i }));
        expect(
            (
                screen.getByRole("combobox", {
                    name: "Override input file count",
                }) as HTMLSelectElement
            ).value
        ).toBe("multi");

        await user.click(screen.getByRole("checkbox", { name: /Override input file filters/i }));
        expect(screen.getByText("*.glb")).toBeInTheDocument();
    });

    it("warns on the Tags and Review steps when a declared tag is unreferenced", async () => {
        // The renderer only substitutes tags the body names, so the value is collected on the
        // execute form and then dropped.
        const user = userEvent.setup();

        render(
            <TemplateForm mode="edit" databaseId="db1" pipelineId="p1" initial={fullTemplate} />,
            {
                wrapper: createWrapper(),
            }
        );

        // Basic and Configuration carry no warning.
        expect(screen.queryByText(/never references/)).not.toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.queryByText(/never references/)).not.toBeInTheDocument();

        // Tags.
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByText(/never references/)).toHaveTextContent("{{prompt}}");
        // Review.
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByText(/never references/)).toHaveTextContent("{{prompt}}");
    });

    it("does not warn when the body references the tag, whitespace and all", async () => {
        // Matches the backend _TAG_PATTERN's tolerance of {{ tag }}.
        const user = userEvent.setup();
        const referenced = {
            ...fullTemplate,
            configBody: '{"p":"{{ prompt }}"}',
        } as unknown as Template;

        render(<TemplateForm mode="edit" databaseId="db1" pipelineId="p1" initial={referenced} />, {
            wrapper: createWrapper(),
        });

        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.queryByText(/never references/)).not.toBeInTheDocument();
    });

    it("keeps the unreferenced-tag warning non-blocking", async () => {
        // allowCustomEdit can legitimately supply the placeholder at launch, and the backend
        // accepts the schema either way, so the warning must not gate Next or Save.
        const user = userEvent.setup();

        render(
            <TemplateForm mode="edit" databaseId="db1" pipelineId="p1" initial={fullTemplate} />,
            {
                wrapper: createWrapper(),
            }
        );

        for (let i = 0; i < 3; i++) {
            await user.click(screen.getByRole("button", { name: "Next" }));
        }
        const save = screen.getByRole("button", { name: "Save" });
        expect(save).toBeEnabled();
        await user.click(save);
        await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    });

    it("blocks advancing and saving while a tag row is invalid", async () => {
        const user = userEvent.setup();

        render(<TemplateForm mode="create" databaseId="db1" pipelineId="p1" />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByPlaceholderText("Template name"), "T");
        // Basic -> Configuration -> Tags.
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Next" }));

        await user.click(screen.getByRole("button", { name: /add tag/i }));
        await user.type(screen.getByLabelText(/tag key/i), "executionId");

        // The builder withholds the invalid row, so the parent schema would silently lag the display.
        await waitFor(() => {
            expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
        });
        expect(screen.getByText(/Fix the highlighted tag definitions/)).toBeInTheDocument();
        expect(mockCreate).not.toHaveBeenCalled();
    });
});

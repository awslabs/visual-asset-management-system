/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TemplateForm from "./TemplateForm";
import type { Template } from "../types";

const mockUpdate = jest.fn();
const mockCreate = jest.fn();

jest.mock("../api/queries", () => ({
    useTemplate: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false })),
    usePipeline: jest.fn(() => ({
        data: { pipelineName: "System GenAI Metadata", isSystem: true },
    })),
    useTemplateMutations: jest.fn(() => ({
        createTemplate: { mutateAsync: mockCreate, isPending: false },
        updateTemplate: { mutateAsync: mockUpdate, isPending: false },
    })),
}));
jest.mock("react-router-dom", () => ({
    useNavigate: () => jest.fn(),
    Link: ({ children }: any) => <span>{children}</span>,
}));
// Monaco is lazy/heavy — stub it to a plain textarea that reports edits.
jest.mock("../components/ConfigEditor", () => ({
    __esModule: true,
    default: ({ value, onChange }: any) => (
        <textarea
            data-testid="config-editor"
            value={value}
            onChange={(e) => onChange(e.target.value)}
        />
    ),
}));
const mockToast = { success: jest.fn(), error: jest.fn(), warning: jest.fn(), info: jest.fn() };
jest.mock("../components/ToastProvider", () => ({
    ...jest.requireActual("../components/ToastProvider"),
    useToast: () => mockToast,
}));

const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider
        client={
            new QueryClient({
                defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
            })
        }
    >
        {children}
    </QueryClientProvider>
);

const systemTemplate: Template = {
    pipelineDatabaseId: "GLOBAL",
    pipelineId: "system-genai-metadata",
    templateId: "system-genai-metadata-default",
    templateName: "Default",
    description: "Shipped template",
    configFormat: "json",
    configBody: '{"RENDER_VIEWS": 4}',
    webFormJson: "[]",
    allowCustomEdit: false,
    inputInstructions: "",
    overrides: {},
    isDefault: true,
    tagSchema: [{ tagKey: "RENDER_VIEWS", type: "integer" }],
} as unknown as Template;

const nextTimes = async (user: ReturnType<typeof userEvent.setup>, n: number) => {
    for (let i = 0; i < n; i++) {
        await user.click(screen.getByRole("button", { name: "Next" }));
    }
};

describe("TemplateForm on a system pipeline", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockUpdate.mockResolvedValue({});
    });

    it("locks the basic fields, the format and the switches but not the config body", async () => {
        const user = userEvent.setup();
        render(
            <TemplateForm
                mode="edit"
                databaseId="GLOBAL"
                pipelineId="system-genai-metadata"
                initial={systemTemplate}
            />,
            { wrapper }
        );
        expect(screen.getByText(/System template:/)).toBeInTheDocument();
        expect(screen.getByPlaceholderText("Template name")).toBeDisabled();
        expect(screen.getByPlaceholderText("Template description")).toBeDisabled();
        await nextTimes(user, 1);
        expect(screen.getByLabelText("Config Format *")).toBeDisabled();
        expect(screen.getByTestId("config-editor")).not.toBeDisabled();
        expect(screen.getByLabelText(/Allow editing the config body/)).toBeDisabled();
    });

    it("sends the full body with the unchanged locked values and the edited config body", async () => {
        const user = userEvent.setup();
        render(
            <TemplateForm
                mode="edit"
                databaseId="GLOBAL"
                pipelineId="system-genai-metadata"
                initial={systemTemplate}
            />,
            { wrapper }
        );
        await nextTimes(user, 1);
        await user.clear(screen.getByTestId("config-editor"));
        await user.type(screen.getByTestId("config-editor"), '{{"RENDER_VIEWS": 8}');
        await nextTimes(user, 2);
        await user.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
        const { body } = mockUpdate.mock.calls[0][0];
        expect(body.templateName).toBe("Default");
        expect(body.description).toBe("Shipped template");
        expect(body.configFormat).toBe("json");
        expect(body.isDefault).toBe(true);
        expect(body.configBody).toBe('{"RENDER_VIEWS": 8}');
        expect(body.tagSchema).toEqual([{ tagKey: "RENDER_VIEWS", type: "integer" }]);
    });

    it("refuses to create a template on a system pipeline", async () => {
        const user = userEvent.setup();
        render(
            <TemplateForm mode="create" databaseId="GLOBAL" pipelineId="system-genai-metadata" />,
            {
                wrapper,
            }
        );
        expect(
            screen.getByText(/Templates of system pipelines cannot be added/)
        ).toBeInTheDocument();
        await user.type(screen.getByPlaceholderText("Template name"), "Extra");
        await nextTimes(user, 3);
        expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
        expect(mockCreate).not.toHaveBeenCalled();
    });
});

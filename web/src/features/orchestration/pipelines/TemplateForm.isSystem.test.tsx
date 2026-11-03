/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TemplateForm from "./TemplateForm";
import * as queries from "../api/queries";
import type { Template } from "../types";

const mockUpdate = jest.fn();
const mockCreate = jest.fn();

jest.mock("../api/queries", () => ({
    useTemplate: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false })),
    usePipeline: jest.fn(() => ({
        data: { pipelineName: "System Sample", isSystem: true },
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
// Monaco is lazy/heavy — stub it to a plain textarea that reports edits. The stub takes the
// editor's ref so the form's imperative handle has somewhere to land; the Review step renders a
// second, read-only copy, so the editable one is addressed by its own test id.
jest.mock("../components/ConfigEditor", () => {
    const ReactModule = require("react");
    const Stub = ReactModule.forwardRef(({ value, onChange, readOnly }: any, ref: any) => {
        ReactModule.useImperativeHandle(ref, () => ({
            insertAtCursor: (text: string) => onChange?.(`${value}${text}`),
        }));
        return (
            <textarea
                data-testid={readOnly ? "config-editor-readonly" : "config-editor"}
                value={value}
                readOnly={readOnly}
                onChange={(e) => onChange?.(e.target.value)}
            />
        );
    });
    Stub.displayName = "ConfigEditorStub";
    return { __esModule: true, default: Stub };
});
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
    pipelineId: "system-sample",
    templateId: "system-sample-default",
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

const setPipelineIsSystem = (isSystem: boolean) => {
    (queries.usePipeline as jest.Mock).mockImplementation(() => ({
        data: { pipelineName: "System Sample", isSystem },
    }));
};

const renderEdit = () =>
    render(
        <TemplateForm
            mode="edit"
            databaseId="GLOBAL"
            pipelineId="system-sample"
            initial={systemTemplate}
        />,
        { wrapper }
    );

const lockedBasicFields = ["Template name", "Template description"];

describe("TemplateForm on a system pipeline", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockUpdate.mockResolvedValue({});
        setPipelineIsSystem(true);
    });

    it("is titled View Template, as the system pipeline and workflow pages say View", () => {
        renderEdit();
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("View Template");
    });

    it("draws each locked field greyed and leaves the config body editable", async () => {
        const user = userEvent.setup();
        const { container } = renderEdit();
        const basic = container.querySelector("fieldset") as HTMLFieldSetElement;
        expect(basic).toBeDisabled();
        // Every disabled control in the step greys, the switches included.
        expect(basic).toHaveClass("[&_:disabled]:opacity-50");
        const fields = [
            ...lockedBasicFields.map((p) => screen.getByPlaceholderText(p)),
            screen.getByLabelText("Input Instructions"),
        ];
        for (const field of fields) {
            // The greyed look keys on :disabled, which a control inside a disabled fieldset matches.
            expect(field.matches(":disabled")).toBe(true);
            expect(field).toHaveClass("disabled:opacity-50");
        }
        // Basic -> Pipeline overrides
        await nextTimes(user, 1);
        const overrides = container.querySelector("fieldset") as HTMLFieldSetElement;
        expect(overrides).toBeDisabled();
        expect(overrides).toHaveClass("[&_:disabled]:opacity-50");
        // -> Tags and Config Body
        await nextTimes(user, 1);
        const format = screen.getByLabelText("Config Format *");
        expect(format.matches(":disabled")).toBe(true);
        expect(format).toHaveClass("disabled:opacity-50");
        expect(screen.getByTestId("config-editor")).not.toBeDisabled();
    });

    it("keeps an ordinary pipeline's template form titled Edit Template and at full strength", async () => {
        setPipelineIsSystem(false);
        const user = userEvent.setup();
        const { container } = renderEdit();
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Edit Template");
        const basic = container.querySelector("fieldset") as HTMLFieldSetElement;
        expect(basic).toBeEnabled();
        expect(basic).not.toHaveClass("[&_:disabled]:opacity-50");
        for (const p of lockedBasicFields) {
            expect(screen.getByPlaceholderText(p).matches(":disabled")).toBe(false);
        }
        await nextTimes(user, 1);
        expect(container.querySelector("fieldset")).not.toHaveClass("[&_:disabled]:opacity-50");
    });

    it("locks the basic fields, the format and the switches but not the config body", async () => {
        const user = userEvent.setup();
        render(
            <TemplateForm
                mode="edit"
                databaseId="GLOBAL"
                pipelineId="system-sample"
                initial={systemTemplate}
            />,
            { wrapper }
        );
        // Basic: name, description, the default switch and the execution-time edit switch.
        expect(screen.getByText(/System template:/)).toBeInTheDocument();
        expect(screen.getByPlaceholderText("Template name")).toBeDisabled();
        expect(screen.getByPlaceholderText("Template description")).toBeDisabled();
        expect(screen.getByLabelText(/Allow editing the config body/)).toBeDisabled();
        // Basic -> Pipeline overrides -> Tags and Config Body
        await nextTimes(user, 2);
        expect(screen.getByLabelText("Config Format *")).toBeDisabled();
        expect(screen.getByTestId("config-editor")).not.toBeDisabled();
    });

    it("sends the full body with the unchanged locked values and the edited config body", async () => {
        const user = userEvent.setup();
        render(
            <TemplateForm
                mode="edit"
                databaseId="GLOBAL"
                pipelineId="system-sample"
                initial={systemTemplate}
            />,
            { wrapper }
        );
        // Basic -> Pipeline overrides -> Tags and Config Body
        await nextTimes(user, 2);
        await user.clear(screen.getByTestId("config-editor"));
        await user.type(screen.getByTestId("config-editor"), '{{"RENDER_VIEWS": 8}');
        // -> Review
        await nextTimes(user, 1);
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
        render(<TemplateForm mode="create" databaseId="GLOBAL" pipelineId="system-sample" />, {
            wrapper,
        });
        expect(
            screen.getByText(/Templates of system pipelines cannot be added/)
        ).toBeInTheDocument();
        await user.type(screen.getByPlaceholderText("Template name"), "Extra");
        await nextTimes(user, 3);
        expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
        expect(mockCreate).not.toHaveBeenCalled();
    });
});

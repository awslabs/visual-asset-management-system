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

// Monaco is lazy/heavy — stub it to a plain textarea. The stub honours the editor's imperative
// handle by appending, which is what a chip click does when the cursor sits at the end of the body.
jest.mock("../components/ConfigEditor", () => {
    const ReactModule = require("react");
    const Stub = ReactModule.forwardRef(({ value, onChange }: any, ref: any) => {
        ReactModule.useImperativeHandle(ref, () => ({
            insertAtCursor: (text: string) => onChange?.(`${value}${text}`),
        }));
        return <textarea data-testid="config-editor" value={value} readOnly />;
    });
    Stub.displayName = "ConfigEditorStub";
    return { __esModule: true, default: Stub };
});

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
    // Basic -> Pipeline overrides -> Tags and Config Body -> Review, then Save.
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

        // Basic -> Pipeline overrides -> Tags and Config Body.
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

        // Basic and Pipeline overrides carry no warning.
        expect(screen.queryByText(/never references/)).not.toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.queryByText(/never references/)).not.toBeInTheDocument();

        // Tags and Config Body.
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
        // Basic -> Pipeline overrides -> Tags and Config Body.
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

/**
 * Edit-to-edit navigation between two templates under the same route pattern.
 *
 * routes.tsx reuses one Route element per pattern, so a templateId change re-renders the loader
 * without unmounting it. With the target already in the TanStack cache the query is `success` on that
 * first render, so `isLoading` never goes true and the loader's spinner branch — the only thing that
 * would remount the form — never runs. Every field is seeded from `initial` with useState, so the form
 * would keep the previous template's values and Save would write them under the NEW templateId.
 */
describe("TemplateFormEditLoader target change", () => {
    const templateA: Template = {
        pipelineDatabaseId: "db1",
        pipelineId: "p1",
        templateId: "t-a",
        templateName: "Template A",
        description: "a",
        configFormat: "json",
        configBody: '{"from":"A"}',
        allowCustomEdit: false,
        inputInstructions: "",
        overrides: {},
        isDefault: false,
        tagSchema: [],
    } as unknown as Template;

    const templateB: Template = {
        ...templateA,
        templateId: "t-b",
        templateName: "Template B",
        description: "b",
        configBody: '{"from":"B"}',
    } as unknown as Template;

    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("re-seeds every field when the cached edit target changes without a remount", async () => {
        const { useTemplate } = require("../api/queries");
        useTemplate.mockReturnValue({ data: templateA, isLoading: false });

        const view = render(
            <TemplateFormEditLoader databaseId="db1" pipelineId="p1" templateId="t-a" />,
            { wrapper: createWrapper() }
        );

        // Control: A's own values are what is on screen before the navigation.
        expect(screen.getByDisplayValue("Template A")).toBeInTheDocument();

        // Edit A, then navigate to B — cached, so isLoading stays false.
        await userEvent.clear(screen.getByDisplayValue("Template A"));
        await userEvent.type(screen.getByPlaceholderText("Template name"), "A edited");
        expect(screen.getByDisplayValue("A edited")).toBeInTheDocument();

        useTemplate.mockReturnValue({ data: templateB, isLoading: false });
        view.rerender(<TemplateFormEditLoader databaseId="db1" pipelineId="p1" templateId="t-b" />);

        expect(screen.getByDisplayValue("Template B")).toBeInTheDocument();
        expect(screen.queryByDisplayValue("A edited")).not.toBeInTheDocument();
    });

    it("saves the new target's body, not the previous one's", async () => {
        const { useTemplate } = require("../api/queries");
        useTemplate.mockReturnValue({ data: templateA, isLoading: false });

        const view = render(
            <TemplateFormEditLoader databaseId="db1" pipelineId="p1" templateId="t-a" />,
            { wrapper: createWrapper() }
        );
        await userEvent.clear(screen.getByDisplayValue("Template A"));
        await userEvent.type(screen.getByPlaceholderText("Template name"), "A edited");

        useTemplate.mockReturnValue({ data: templateB, isLoading: false });
        view.rerender(<TemplateFormEditLoader databaseId="db1" pipelineId="p1" templateId="t-b" />);

        // Basic -> Pipeline overrides -> Tags and Config Body -> Review, then Save.
        for (let i = 0; i < 3; i++) {
            await userEvent.click(screen.getByRole("button", { name: "Next" }));
        }
        await userEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
        const call = mockUpdate.mock.calls[0][0];
        expect(call.templateId).toBe("t-b");
        expect(call.body.templateName).toBe("Template B");
        expect(call.body.configBody).toBe('{"from":"B"}');
    });
});

/**
 * The Tags and Config Body step: the tag schema and the body are authored side by side, the declared
 * tags are inserted into the body as chips with the quoting the backend expects, and a json body is
 * checked against the backend's two-pass rule while it is typed.
 */
describe("TemplateForm — Tags and Config Body step", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { usePipeline } = require("../api/queries");
        usePipeline.mockReturnValue({ data: { pipelineName: "P1" } });
    });

    const editorValue = () => (screen.getByTestId("config-editor") as HTMLTextAreaElement).value;

    const openTagsStep = async (user: ReturnType<typeof userEvent.setup>) => {
        await user.type(screen.getByPlaceholderText("Template name"), "T");
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Next" }));
    };

    const addTag = async (
        user: ReturnType<typeof userEvent.setup>,
        index: number,
        key: string,
        type: string
    ) => {
        await user.click(screen.getByRole("button", { name: /add tag/i }));
        await user.type(screen.getAllByLabelText(/tag key/i)[index], key);
        await user.selectOptions(screen.getAllByLabelText(/^type/i)[index], type);
    };

    it("composes the steps as Basic, Pipeline overrides, Tags and Config Body, Review", async () => {
        const user = userEvent.setup();
        render(<TemplateForm mode="create" databaseId="db1" pipelineId="p1" />, {
            wrapper: createWrapper(),
        });

        for (const label of ["Basic", "Pipeline overrides", "Tags and Config Body", "Review"]) {
            expect(screen.getByText(label, { exact: true })).toBeInTheDocument();
        }
        // The execution-time options sit on Basic with the other template-level switches.
        expect(
            screen.getByRole("checkbox", {
                name: /Allow editing the config body at execution time/,
            })
        ).toBeInTheDocument();
        expect(
            screen.getByRole("checkbox", { name: /Set as the pipeline's default template/ })
        ).toBeInTheDocument();
        expect(screen.queryByTestId("config-editor")).not.toBeInTheDocument();

        await openTagsStep(user);
        // Tag builder, format and body are on the same screen.
        expect(screen.getByRole("button", { name: /add tag/i })).toBeInTheDocument();
        expect(screen.getByLabelText(/Config Format/)).toBeInTheDocument();
        expect(screen.getByTestId("config-editor")).toBeInTheDocument();
        expect(screen.getByTestId("tags-and-body")).toBeInTheDocument();
        // No tags yet: the chip catalog says so instead of showing an empty row.
        expect(screen.getByText(/No tags declared yet/)).toBeInTheDocument();
    });

    it("inserts a quoted placeholder for a string tag and a bare one for a number tag in a json body", async () => {
        const user = userEvent.setup();
        render(<TemplateForm mode="create" databaseId="db1" pipelineId="p1" />, {
            wrapper: createWrapper(),
        });
        await openTagsStep(user);

        await addTag(user, 0, "PROMPT", "string");
        await user.click(screen.getByRole("button", { name: "Insert {{PROMPT}}" }));
        expect(editorValue()).toBe('"{{PROMPT}}"');

        await addTag(user, 1, "STEPS", "integer");
        await user.click(screen.getByRole("button", { name: "Insert {{STEPS}}" }));
        expect(editorValue()).toBe('"{{PROMPT}}"{{STEPS}}');

        // The chip says which form it inserts.
        expect(screen.getByRole("button", { name: "Insert {{PROMPT}}" })).toHaveTextContent(
            "quoted"
        );
        expect(screen.getByRole("button", { name: "Insert {{STEPS}}" })).toHaveTextContent("bare");
    });

    it("inserts a bare placeholder for every tag type in a yaml body", async () => {
        const user = userEvent.setup();
        render(<TemplateForm mode="create" databaseId="db1" pipelineId="p1" />, {
            wrapper: createWrapper(),
        });
        await openTagsStep(user);
        await user.selectOptions(screen.getByLabelText(/Config Format/), "yaml");

        await addTag(user, 0, "PROMPT", "string");
        await user.click(screen.getByRole("button", { name: "Insert {{PROMPT}}" }));
        expect(editorValue()).toBe("{{PROMPT}}");
    });

    it("clears the unreferenced-tag warning once the chip has placed the placeholder", async () => {
        const user = userEvent.setup();
        render(<TemplateForm mode="create" databaseId="db1" pipelineId="p1" />, {
            wrapper: createWrapper(),
        });
        await openTagsStep(user);

        await addTag(user, 0, "PROMPT", "string");
        expect(screen.getByTestId("unreferenced-tags-warning")).toHaveTextContent("{{PROMPT}}");

        await user.click(screen.getByRole("button", { name: "Insert {{PROMPT}}" }));
        expect(screen.queryByTestId("unreferenced-tags-warning")).not.toBeInTheDocument();
    });

    it("reports a json body that quotes a typed tag while it is being authored, without blocking", async () => {
        const user = userEvent.setup();
        const quoted = {
            ...fullTemplate,
            configBody: '{"steps": "{{STEPS}}"}',
            tagSchema: [{ tagKey: "STEPS", type: "integer", required: false }],
        } as unknown as Template;
        render(<TemplateForm mode="edit" databaseId="db1" pipelineId="p1" initial={quoted} />, {
            wrapper: createWrapper(),
        });

        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByTestId("config-body-error")).toHaveTextContent(
            /quotes a \{\{tagName\}\} placeholder for a tag declared integer, number, boolean or string-list/
        );
        // The backend is the authority, so the verdict is shown but Next stays enabled.
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
        await user.click(screen.getByRole("button", { name: "Next" }));
        // Review repeats it beside the body preview.
        expect(screen.getByTestId("config-body-error")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    });

    it("reports malformed json and a bare text tag, and stays quiet for a correct body or another format", async () => {
        const user = userEvent.setup();
        const view = render(
            <TemplateForm
                mode="edit"
                databaseId="db1"
                pipelineId="p1"
                initial={{ ...fullTemplate, configBody: '{"p": ' } as unknown as Template}
            />,
            { wrapper: createWrapper() }
        );
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByTestId("config-body-error")).toHaveTextContent(
            "The config body is not valid JSON."
        );
        view.unmount();

        const bare = render(
            <TemplateForm
                mode="edit"
                databaseId="db1"
                pipelineId="p1"
                initial={
                    { ...fullTemplate, configBody: '{"p": {{prompt}}}' } as unknown as Template
                }
            />,
            { wrapper: createWrapper() }
        );
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByTestId("config-body-error")).toHaveTextContent(
            /belongs inside the JSON string it fills/
        );
        bare.unmount();

        const fine = render(
            <TemplateForm
                mode="edit"
                databaseId="db1"
                pipelineId="p1"
                initial={
                    { ...fullTemplate, configBody: '{"p": "{{prompt}}"}' } as unknown as Template
                }
            />,
            { wrapper: createWrapper() }
        );
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.queryByTestId("config-body-error")).not.toBeInTheDocument();
        // A yaml body is stored verbatim and never shape-checked.
        await user.selectOptions(screen.getByLabelText(/Config Format/), "yaml");
        expect(screen.queryByTestId("config-body-error")).not.toBeInTheDocument();
        fine.unmount();
    });

    it("reopens a visited step from the stepper and jumps forward only past valid steps", async () => {
        const user = userEvent.setup();
        render(<TemplateForm mode="create" databaseId="db1" pipelineId="p1" />, {
            wrapper: createWrapper(),
        });

        // Basic is invalid (no name), so nothing ahead is reachable from the stepper.
        expect(screen.queryByRole("button", { name: /Go to step/ })).not.toBeInTheDocument();
        await user.type(screen.getByPlaceholderText("Template name"), "T");
        // Every later step is now reachable, since no step between has a failing gate.
        await user.click(screen.getByRole("button", { name: "Go to step Tags and Config Body" }));
        expect(screen.getByTestId("config-editor")).toBeInTheDocument();
        // And the way back is one click.
        await user.click(screen.getByRole("button", { name: "Go to step Basic" }));
        expect(screen.getByDisplayValue("T")).toBeInTheDocument();
    });
});

describe("TemplateForm — edit route hydration", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { usePipeline } = require("../api/queries");
        usePipeline.mockReturnValue({
            data: { pipelineName: "P1", systemConfig: { inputFileArity: "one" } },
        });
    });

    it("hydrates every field of an existing template into the same steps", async () => {
        const user = userEvent.setup();
        const { useTemplate } = require("../api/queries");
        const stored: Template = {
            ...fullTemplate,
            templateName: "Stored",
            description: "Stored description",
            inputInstructions: "Fill the prompt.",
            configFormat: "yaml",
            configBody: "prompt: {{PROMPT}}\nsteps: {{STEPS}}",
            allowCustomEdit: true,
            isDefault: true,
            overrides: { inputFileArity: "multi" },
            tagSchema: [
                { tagKey: "PROMPT", type: "string", required: true, label: "Prompt" },
                { tagKey: "STEPS", type: "integer", required: false, default: 4 },
            ],
        } as unknown as Template;
        useTemplate.mockReturnValue({ data: stored, isLoading: false });

        render(<TemplateFormEditLoader databaseId="db1" pipelineId="p1" templateId="t1" />, {
            wrapper: createWrapper(),
        });

        // Basic.
        expect(screen.getByDisplayValue("Stored")).toBeInTheDocument();
        expect(screen.getByDisplayValue("Stored description")).toBeInTheDocument();
        expect(screen.getByDisplayValue("Fill the prompt.")).toBeInTheDocument();
        expect(
            screen.getByRole("checkbox", { name: /Set as the pipeline's default template/ })
        ).toBeChecked();
        expect(
            screen.getByRole("checkbox", {
                name: /Allow editing the config body at execution time/,
            })
        ).toBeChecked();

        // Pipeline overrides.
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByRole("checkbox", { name: /Override input file count/i })).toBeChecked();
        expect(
            (
                screen.getByRole("combobox", {
                    name: "Override input file count",
                }) as HTMLSelectElement
            ).value
        ).toBe("multi");

        // Tags and Config Body.
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect((screen.getByLabelText(/Config Format/) as HTMLSelectElement).value).toBe("yaml");
        expect((screen.getByTestId("config-editor") as HTMLTextAreaElement).value).toBe(
            "prompt: {{PROMPT}}\nsteps: {{STEPS}}"
        );
        expect(
            screen.getAllByLabelText(/tag key/i).map((el) => (el as HTMLInputElement).value)
        ).toEqual(["PROMPT", "STEPS"]);
        expect(screen.getByRole("button", { name: "Insert {{PROMPT}}" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Insert {{STEPS}}" })).toBeInTheDocument();
        expect(screen.queryByTestId("unreferenced-tags-warning")).not.toBeInTheDocument();

        // Review: the tag table and the options.
        await user.click(screen.getByRole("button", { name: "Next" }));
        const rows = screen.getByTestId("review-tag-table").querySelectorAll("tbody tr");
        expect(rows).toHaveLength(2);
        expect(rows[0]).toHaveTextContent("{{PROMPT}}");
        expect(rows[0]).toHaveTextContent("Yes");
        expect(rows[1]).toHaveTextContent("4");
        // The label is its own span; the value follows it in the parent row.
        expect(screen.getByText("Pipeline overrides:").parentElement).toHaveTextContent(
            "Input file count"
        );
        expect(screen.getByText("Tags:").parentElement).toHaveTextContent("Tags: 2");

        // Saving writes every hydrated field back.
        await user.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
        const body = mockUpdate.mock.calls[0][0].body;
        expect(body).toMatchObject({
            templateName: "Stored",
            description: "Stored description",
            inputInstructions: "Fill the prompt.",
            configFormat: "yaml",
            configBody: "prompt: {{PROMPT}}\nsteps: {{STEPS}}",
            allowCustomEdit: true,
            isDefault: true,
            overrides: { inputFileArity: "multi" },
            tagSchema: stored.tagSchema,
        });
    });
});

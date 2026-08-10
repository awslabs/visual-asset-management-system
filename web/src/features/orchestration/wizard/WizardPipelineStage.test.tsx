/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WizardPipelineStage from "./WizardPipelineStage";
import type { Workflow, Pipeline, SpecifiedPipelineRef } from "../types";
import type { PipelineStageData } from "./ExecuteWizard";

jest.mock("../api/queries", () => ({
    useTemplates: jest.fn(),
    useTemplate: jest.fn(),
}));

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => null,
}));

const workflow: Workflow = {
    databaseId: "db1",
    workflowId: "wf1",
    workflowName: "Test Workflow",
    enabled: true,
    archived: false,
    specifiedPipelines: [{ pipelineId: "pipe1", pipelineDatabaseId: "db1" }],
    systemConfig: { inputFileArity: "none" },
};

const pipelineRef: SpecifiedPipelineRef = { pipelineId: "pipe1", pipelineDatabaseId: "db1" };

const makePipeline = (systemConfig: Record<string, any>): Pipeline => ({
    databaseId: "db1",
    pipelineId: "pipe1",
    pipelineName: "Test Pipeline",
    enabled: true,
    executionConfig: { executionType: "Lambda" },
    systemConfig: systemConfig as any,
});

describe("WizardPipelineStage", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { useTemplates, useTemplate } = require("../api/queries");
        useTemplates.mockReturnValue({ data: [], isLoading: false, isSuccess: true });
        useTemplate.mockReturnValue({ data: undefined, isLoading: false, isSuccess: false });
    });

    it("surfaces the require-template error when no template is selected", () => {
        const onChange = jest.fn();

        render(
            <WizardPipelineStage
                workflow={workflow}
                pipeline={makePipeline({ requireTemplate: true })}
                pipelineRef={pipelineRef}
                onChange={onChange}
            />
        );

        expect(screen.getByText(/Validation Errors/i)).toBeInTheDocument();
        expect(screen.getByText(/requires a template \(templateId\)/i)).toBeInTheDocument();
        // The parent gates Launch on the reported errors, so they must reach it too.
        const reported = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(reported.errors).toEqual([
            "This pipeline requires a template (templateId) for execution",
        ]);
    });

    it("reports no errors when no template is selected and none is required", () => {
        const onChange = jest.fn();

        render(
            <WizardPipelineStage
                workflow={workflow}
                pipeline={makePipeline({ requireTemplate: false })}
                pipelineRef={pipelineRef}
                onChange={onChange}
            />
        );

        expect(screen.queryByText(/Validation Errors/i)).not.toBeInTheDocument();
        const reported = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(reported.errors).toEqual([]);
        expect(reported.mode).toBe(4);
    });
});

/**
 * Tag values belong to the template they were entered against.
 *
 * The form is seeded per template, so switching the picker must re-seed from the NEW template's own
 * tagSchema — carrying the previous template's values over submits free text as another template's
 * enum value, and keys the new schema may not declare at all.
 */
describe("WizardPipelineStage tag seeding across template changes", () => {
    const TPL_A = {
        templateId: "tplA",
        templateName: "Template A",
        configFormat: "json",
        configBody: '{"p":"{{prompt}}"}',
        isDefault: true,
        tagSchema: [{ tagKey: "prompt", type: "string" }],
    };
    const TPL_B = {
        templateId: "tplB",
        templateName: "Template B",
        configFormat: "json",
        configBody: '{"p":"{{prompt}}"}',
        tagSchema: [
            {
                tagKey: "prompt",
                type: "enum",
                required: true,
                default: "fast",
                enumValues: ["fast", "slow"],
            },
        ],
    };

    /**
     * ExecuteWizard stores every reported PipelineStageData and feeds it back as `data`, so the stage
     * always sees its own last report on re-render. The harness reproduces that round trip — without
     * it the stale-value path this describes is unreachable.
     */
    const renderWith = (list: any[], initial?: PipelineStageData) => {
        const { useTemplates, useTemplate } = require("../api/queries");
        useTemplates.mockReturnValue({ data: list, isLoading: false, isSuccess: true });
        useTemplate.mockImplementation((_db: string, _pipe: string, templateId: string) => {
            const tpl = list.find((t) => t.templateId === templateId);
            return { data: tpl, isLoading: false, isSuccess: !!tpl };
        });
        const onChange = jest.fn();
        // The pipeline object is created once: the stage's validation memo keys off identity, so a new
        // object per render would re-report on every parent update.
        const pipeline = makePipeline({ requireTemplate: false });
        const Harness: React.FC = () => {
            const [data, setData] = React.useState<PipelineStageData | undefined>(initial);
            return (
                <WizardPipelineStage
                    workflow={workflow}
                    pipeline={pipeline}
                    pipelineRef={pipelineRef}
                    data={data}
                    onChange={(d) => {
                        onChange(d);
                        setData(d);
                    }}
                />
            );
        };
        render(<Harness />);
        return onChange;
    };

    const lastTags = (onChange: jest.Mock) =>
        onChange.mock.calls[onChange.mock.calls.length - 1][0].tags;
    const lastReport = (onChange: jest.Mock) =>
        onChange.mock.calls[onChange.mock.calls.length - 1][0];

    /** The template picker is the first select on the step; the tag form's own controls follow it. */
    const selectTemplate = async (templateName: string) =>
        userEvent.selectOptions(screen.getAllByRole("combobox")[0], templateName);

    beforeEach(() => jest.clearAllMocks());

    it("replaces the previous template's tag values with the new template's defaults", async () => {
        const onChange = renderWith([TPL_A, TPL_B]);
        await userEvent.type(await screen.findByLabelText("prompt"), "free-text-from-A");
        await waitFor(() =>
            expect(lastTags(onChange)).toEqual([{ key: "prompt", value: "free-text-from-A" }])
        );

        await selectTemplate("Template B");

        await waitFor(() => expect(lastReport(onChange).templateId).toBe("tplB"));
        expect(lastTags(onChange)).toEqual([{ key: "prompt", value: "fast" }]);
    });

    it("drops a value whose key the new template does not declare", async () => {
        const tplC = { ...TPL_B, templateId: "tplC", templateName: "Template C", tagSchema: [] };
        const onChange = renderWith([TPL_A, tplC]);
        await userEvent.type(await screen.findByLabelText("prompt"), "x");
        await waitFor(() => expect(lastTags(onChange)).toEqual([{ key: "prompt", value: "x" }]));

        await selectTemplate("Template C");

        await waitFor(() => expect(lastReport(onChange).templateId).toBe("tplC"));
        expect(lastTags(onChange)).toEqual([]);
    });

    it("does not flag a blank optional tag as an error after re-seeding", async () => {
        // An optional string/enum/string-list tag left blank is valid: the backend materializes an
        // empty value for it, which is what the metadata fallback relies on.
        const optional = {
            ...TPL_B,
            templateId: "tplOpt",
            templateName: "Template Optional",
            tagSchema: [{ tagKey: "prompt", type: "enum", enumValues: ["fast", "slow"] }],
        };
        const onChange = renderWith([TPL_A, optional]);
        await selectTemplate("Template Optional");

        await waitFor(() => expect(lastReport(onChange).templateId).toBe("tplOpt"));
        expect(lastTags(onChange)).toEqual([]);
        expect(lastReport(onChange).errors).toEqual([]);
    });

    it("restores the run's own tags when the step is revisited on the same template", async () => {
        const onChange = renderWith([TPL_A, TPL_B], {
            pipelineId: "pipe1",
            templateId: "tplA",
            tags: [{ key: "prompt", value: "entered-earlier" }],
            errors: [],
            params: {},
        });

        await waitFor(() =>
            expect(lastTags(onChange)).toEqual([{ key: "prompt", value: "entered-earlier" }])
        );
        expect(await screen.findByLabelText("prompt")).toHaveValue("entered-earlier");
    });

    it("does not restore the run's tags onto a different template", async () => {
        const onChange = renderWith([TPL_A, TPL_B], {
            pipelineId: "pipe1",
            templateId: "tplA",
            tags: [{ key: "prompt", value: "entered-earlier" }],
            errors: [],
            params: {},
        });
        await waitFor(() =>
            expect(lastTags(onChange)).toEqual([{ key: "prompt", value: "entered-earlier" }])
        );

        await selectTemplate("Template B");

        await waitFor(() => expect(lastReport(onChange).templateId).toBe("tplB"));
        expect(lastTags(onChange)).toEqual([{ key: "prompt", value: "fast" }]);
    });
});

/**
 * The selected template's guidance must reach the person RUNNING the pipeline.
 *
 * `inputInstructions` was authored per template (and now documents every metadata key each pipeline
 * reads) but was only ever rendered in the template EDITOR — the execute wizard never displayed it,
 * so the audience it was written for never saw it.
 */
describe("WizardPipelineStage template instructions", () => {
    const template = (over: any = {}) => ({
        templateId: "t1",
        templateName: "Template One",
        configFormat: "json",
        configBody: "{}",
        isDefault: true,
        ...over,
    });

    const renderWith = (tpl: any) => {
        const { useTemplates, useTemplate } = require("../api/queries");
        useTemplates.mockReturnValue({ data: [tpl], isLoading: false, isSuccess: true });
        useTemplate.mockReturnValue({ data: tpl, isLoading: false, isSuccess: true });
        render(
            <WizardPipelineStage
                workflow={workflow}
                pipeline={makePipeline({ requireTemplate: false })}
                pipelineRef={pipelineRef}
                onChange={jest.fn()}
            />
        );
    };

    beforeEach(() => jest.clearAllMocks());

    it("shows short instructions inline on the run screen", () => {
        renderWith(template({ inputInstructions: "Select the source model as the input file." }));
        expect(screen.getByText("Select the source model as the input file.")).toBeInTheDocument();
    });

    it("collapses long instructions so they do not bury the form", () => {
        // A metadata-documenting template runs to ~20 lines; inline would push the tag fields and the
        // configuration section off screen.
        const long = Array.from({ length: 18 }, (_, i) => `COSMOS3_KEY_${i}  what it does`).join(
            "\n"
        );
        renderWith(template({ inputInstructions: long }));
        expect(screen.getByTestId("instructions-tooltip-trigger")).toBeInTheDocument();
        expect(screen.queryByTestId("instructions-inline")).not.toBeInTheDocument();
    });

    it("shows the template description alongside the instructions", () => {
        renderWith(
            template({ description: "Converts to GLB.", inputInstructions: "Pick a model file." })
        );
        expect(screen.getByText("Converts to GLB.")).toBeInTheDocument();
        expect(screen.getByText("Pick a model file.")).toBeInTheDocument();
    });

    it("renders no instructions block when the template has none", () => {
        renderWith(template({}));
        expect(screen.queryByTestId("instructions-inline")).not.toBeInTheDocument();
        expect(screen.queryByTestId("instructions-tooltip-trigger")).not.toBeInTheDocument();
    });
});

/**
 * The step must never render as a blank page.
 *
 * Every block in this stage is conditional — template picker, tag form, customize toggle, config
 * editor. A pipeline with no templates, no tag schema, and no customize grant therefore rendered
 * nothing but its own heading. An empty step reads as "still loading" or "broken", not as "this
 * pipeline takes no configuration", which is what it actually means.
 */
describe("WizardPipelineStage empty state", () => {
    const render_ = (systemConfig: Record<string, any>) =>
        render(
            <WizardPipelineStage
                workflow={workflow}
                pipeline={makePipeline(systemConfig)}
                pipelineRef={pipelineRef}
                onChange={jest.fn()}
            />
        );

    beforeEach(() => {
        jest.clearAllMocks();
        const { useTemplates, useTemplate } = require("../api/queries");
        useTemplates.mockReturnValue({ data: [], isLoading: false, isSuccess: true });
        useTemplate.mockReturnValue({ data: undefined, isLoading: false, isSuccess: false });
    });

    it("explains itself when there is nothing to configure", () => {
        render_({ allowCustomTemplateOverride: false });
        expect(screen.getByText(/takes no run-time configuration/i)).toBeInTheDocument();
    });

    it("says what to do next rather than leaving the user stuck", () => {
        render_({ allowCustomTemplateOverride: false });
        expect(screen.getByText(/Continue to the next step/i)).toBeInTheDocument();
    });

    it("stays silent while the template list is still loading", () => {
        // Claiming "nothing to configure" mid-flight would be wrong — templates may yet arrive.
        const { useTemplates } = require("../api/queries");
        useTemplates.mockReturnValue({ data: undefined, isLoading: true, isSuccess: false });
        render_({ allowCustomTemplateOverride: false });
        expect(screen.queryByText(/takes no run-time configuration/i)).not.toBeInTheDocument();
    });

    it("is not shown when templates exist", () => {
        const { useTemplates } = require("../api/queries");
        useTemplates.mockReturnValue({
            data: [{ templateId: "t1", templateName: "Default", isDefault: false }],
            isLoading: false,
            isSuccess: true,
        });
        render_({ allowCustomTemplateOverride: false });
        expect(screen.queryByText(/takes no run-time configuration/i)).not.toBeInTheDocument();
        expect(screen.getByText("Template")).toBeInTheDocument();
    });

    it("is not shown when the pipeline allows a custom configuration", () => {
        // The customize toggle IS content, so the step is not empty.
        render_({ allowCustomTemplateOverride: true });
        expect(screen.queryByText(/takes no run-time configuration/i)).not.toBeInTheDocument();
        expect(screen.getByText(/Customize configuration before running/i)).toBeInTheDocument();
    });

    it("still shows validation errors alongside the message when both apply", () => {
        // requireTemplate with no templates available is a real misconfiguration; the error must not
        // be replaced by the friendly message.
        render_({ requireTemplate: true, allowCustomTemplateOverride: false });
        expect(screen.getByText(/takes no run-time configuration/i)).toBeInTheDocument();
        expect(screen.getByText(/Validation Errors/i)).toBeInTheDocument();
    });
});

/**
 * The template-tag catalog must be reachable from the config editor.
 *
 * While editing a config body the operative question is "what placeholders can I write here?". The
 * catalog existed only as a panel below the editor, so on a long body it was off-screen exactly when
 * it was needed. An icon beside the resolve-time note answers it in place.
 */
describe("WizardPipelineStage template tag help", () => {
    const render_ = (systemConfig: Record<string, any>, template?: any) => {
        const { useTemplates, useTemplate } = require("../api/queries");
        useTemplates.mockReturnValue({
            data: template ? [template] : [],
            isLoading: false,
            isSuccess: true,
        });
        useTemplate.mockReturnValue({ data: template, isLoading: false, isSuccess: !!template });
        return render(
            <WizardPipelineStage
                workflow={workflow}
                pipeline={makePipeline(systemConfig)}
                pipelineRef={pipelineRef}
                onChange={jest.fn()}
            />
        );
    };

    const TEMPLATE = {
        templateId: "t1",
        templateName: "Default",
        configBody: '{"a":1}',
        configFormat: "json",
        allowCustomEdit: true,
        isDefault: true,
    };

    beforeEach(() => jest.clearAllMocks());

    /** The config section is a CollapsibleSection, closed unless customizing — open it first. */
    const openConfigSection = async () => {
        const toggle = screen.queryByText(/Configuration \(from template\)/i);
        if (toggle) await userEvent.click(toggle);
    };

    it("says dynamic AND system placeholders are resolved at launch", async () => {
        render_({ allowCustomTemplateOverride: true }, TEMPLATE);
        await openConfigSection();
        expect(
            await screen.findByText(
                /Dynamic and system tag placeholders are resolved per pipeline task at launch\./i
            )
        ).toBeInTheDocument();
    });

    it("offers an icon to reveal the tag catalog when the config can be edited", async () => {
        render_({ allowCustomTemplateOverride: true }, TEMPLATE);
        await openConfigSection();
        expect(await screen.findByLabelText("Show available template tags")).toBeInTheDocument();
    });

    it("reveals the catalog when the icon is clicked", async () => {
        render_({ allowCustomTemplateOverride: true }, TEMPLATE);
        await openConfigSection();
        const icon = await screen.findByLabelText("Show available template tags");
        await userEvent.click(icon);
        // The catalog is open when its grouped contents render — the collapsed panel shows only its
        // heading, and the heading text alone appears more than once on this step.
        expect(await screen.findByText(/Execution & workflow identity/i)).toBeInTheDocument();
        expect(screen.getByText(/Output locations/i)).toBeInTheDocument();
    });

    it("does not offer the icon when the config cannot be customized", async () => {
        // Nothing to write, so a list of writable placeholders would be misleading.
        render_({ allowCustomTemplateOverride: false }, { ...TEMPLATE, allowCustomEdit: false });
        await openConfigSection();
        await screen.findByText(/resolved per pipeline task at launch/i);
        expect(screen.queryByLabelText("Show available template tags")).not.toBeInTheDocument();
    });
});

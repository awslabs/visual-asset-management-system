/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import WizardPipelineStage from "./WizardPipelineStage";
import type { Workflow, Pipeline, SpecifiedPipelineRef } from "../types";

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

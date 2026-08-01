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

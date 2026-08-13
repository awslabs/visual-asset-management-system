/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import WizardReviewStage from "./WizardReviewStage";
import type { Workflow, Pipeline } from "../types";
import type { PipelineStageData } from "./ExecuteWizard";

const workflow: Workflow = {
    databaseId: "db1",
    workflowId: "wf1",
    workflowName: "WF",
    specifiedPipelines: [{ pipelineId: "pipeA", pipelineDatabaseId: "db1" }],
};

const pipeline: Pipeline = {
    databaseId: "db1",
    pipelineId: "pipeA",
    pipelineName: "Pipeline A",
    executionConfig: { executionType: "Lambda" },
};

// Stage data and errors are keyed by the composite `${databaseId}:${pipelineId}`.
const pipelineData: Record<string, PipelineStageData> = {
    "db1:pipeA": {
        pipelineId: "pipeA",
        templateId: "tpl1",
        tags: [{ key: "quality", value: "high" }],
        customTemplateOverride: "{}",
        errors: ["Required tags missing: quality"],
        params: {},
    },
};

describe("WizardReviewStage", () => {
    it("reads pipeline summary data by composite database:pipeline key", () => {
        render(
            <WizardReviewStage
                workflow={workflow}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={pipelineData}
                inputFiles={[]}
                validationErrors={{ "db1:pipeA": ["Required tags missing: quality"] }}
            />
        );

        expect(screen.getByText(/Template: tpl1/)).toBeInTheDocument();
        expect(screen.getByText(/Tags: quality=high/)).toBeInTheDocument();
        expect(screen.getByText(/Custom override enabled/)).toBeInTheDocument();
        expect(screen.getByText("Required tags missing: quality")).toBeInTheDocument();
    });

    it("falls back to the wizard's database when the ref carries none", () => {
        const refWithoutDb: Workflow = {
            ...workflow,
            specifiedPipelines: [{ pipelineId: "pipeA" }],
        };

        render(
            <WizardReviewStage
                workflow={refWithoutDb}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={pipelineData}
                inputFiles={[]}
                validationErrors={{}}
            />
        );

        expect(screen.getByText(/Template: tpl1/)).toBeInTheDocument();
    });
});

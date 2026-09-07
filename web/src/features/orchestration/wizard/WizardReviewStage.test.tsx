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

/**
 * The output path prefix decides where every output file lands and is the hardest input to undo after
 * a run, but it was collected on the Input step, sent on launch, and never shown on the confirmation
 * screen — so neither a cleared value nor a mistyped tag was verifiable before launching.
 */
describe("WizardReviewStage output target", () => {
    const renderReview = (props: Record<string, any> = {}) =>
        render(
            <WizardReviewStage
                workflow={workflow}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={{}}
                inputFiles={[]}
                validationErrors={{}}
                {...props}
            />
        );

    it("states the prefix the run will write under", () => {
        renderReview({ outputPathPrefix: "/run/" });

        expect(screen.getByText("Output Target")).toBeInTheDocument();
        expect(screen.getByText(/Path prefix: \/run\//)).toBeInTheDocument();
    });

    it("spells out a cleared prefix rather than showing a blank", () => {
        // "" is a deliberate write-at-the-asset-root, which a blank line cannot distinguish from a
        // field nobody touched.
        renderReview({ outputPathPrefix: "" });

        expect(screen.getByText(/Path prefix: None \(asset root\)/)).toBeInTheDocument();
    });

    it("says the workflow default applies when the field was never touched", () => {
        renderReview({ outputPathPrefix: undefined });

        expect(screen.getByText(/Path prefix: \(workflow default\)/)).toBeInTheDocument();
    });

    it("shows the target for a run that overrode nothing", () => {
        // Previously the whole block was withheld unless an output id was set, so a default-target run
        // confirmed no destination at all.
        renderReview({ outputPathPrefix: "/x/" });

        expect(screen.getByText(/Asset: \(default\) \/ \(default\)/)).toBeInTheDocument();
    });

    it("omits the target for a results-only workflow", () => {
        // Control: the block is conditional, so the assertions above are only evidence if it can also
        // be absent — a results-only run writes no asset output and has no destination to confirm.
        const resultsOnly: Workflow = {
            ...workflow,
            systemConfig: { outputTarget: { locationType: "none" } },
        } as Workflow;

        render(
            <WizardReviewStage
                workflow={resultsOnly}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={{}}
                inputFiles={[]}
                validationErrors={{}}
            />
        );

        expect(screen.queryByText("Output Target")).not.toBeInTheDocument();
    });
});

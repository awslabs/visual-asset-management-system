/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
        templateName: "Template One",
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

        expect(screen.getByText(/Template: Template One/)).toBeInTheDocument();
        // The id stays readable beside the name — it is what the request carries.
        expect(screen.getByText("tpl1")).toBeInTheDocument();
        // Tags read as a key/value list rather than a comma-joined line.
        expect(screen.getByText("quality")).toBeInTheDocument();
        expect(screen.getByText("high")).toBeInTheDocument();
        expect(screen.getByText("Overridden configuration")).toBeInTheDocument();
        // The error appears once, in the Blockers list, attributed to the step.
        expect(screen.getByText("Required tags missing: quality")).toBeInTheDocument();
        expect(screen.getByText("Blockers")).toBeInTheDocument();
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

        expect(screen.getByText(/Template: Template One/)).toBeInTheDocument();
    });

    it("shows the template id when the stage carries no name", () => {
        const idOnly = {
            "db1:pipeA": { ...pipelineData["db1:pipeA"], templateName: undefined },
        };
        render(
            <WizardReviewStage
                workflow={workflow}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={idOnly}
                inputFiles={[]}
                validationErrors={{}}
            />
        );
        expect(screen.getByText(/Template: tpl1/)).toBeInTheDocument();
    });
});

/**
 * Everything that disables Launch is stated ONCE, under Blockers, and every card links back to the
 * step that owns it — so the user never has to walk Back through every step to find the problem.
 */
describe("WizardReviewStage blockers and Edit links", () => {
    it("lists input, output and pipeline blockers once each and renders no alert role", () => {
        render(
            <WizardReviewStage
                workflow={workflow}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={pipelineData}
                inputFiles={[]}
                validationErrors={{ "db1:pipeA": ["Required tags missing: quality"] }}
                inputSelectionErrors={[
                    "Workflow requires exactly one input file but none were provided.",
                ]}
                outputAssetMissing
                offendingPipelines={[
                    { pipelineId: "pipeA", pipelineName: "Pipeline A", reason: "disabled" },
                ]}
            />
        );
        const blockers = screen.getByText("Blockers").closest("[aria-live]") as HTMLElement;
        expect(blockers).toHaveAttribute("aria-live", "polite");
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        const items = Array.from(blockers.querySelectorAll("li")).map((li) => li.textContent);
        expect(items).toHaveLength(4);
        expect(items[0]).toContain("Inputs");
        expect(items[0]).toContain("requires exactly one input file");
        expect(items[1]).toContain("span multiple assets");
        expect(items[2]).toContain("Pipeline A");
        expect(items[2]).toContain("This pipeline is disabled.");
        expect(items[3]).toContain("Required tags missing: quality");
    });

    it("jumps to the owning step from a blocker and from a card", async () => {
        const onEdit = jest.fn();
        render(
            <WizardReviewStage
                workflow={workflow}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={pipelineData}
                inputFiles={[]}
                validationErrors={{ "db1:pipeA": ["Required tags missing: quality"] }}
                outputPathPrefix="/x/"
                onEdit={onEdit}
            />
        );
        const blockers = screen.getByText("Blockers").closest("[aria-live]") as HTMLElement;
        await userEvent.click(within(blockers).getByRole("button", { name: "Edit Pipeline A" }));
        expect(onEdit).toHaveBeenLastCalledWith("pipeline-0");

        await userEvent.click(screen.getByRole("button", { name: "Edit Inputs" }));
        expect(onEdit).toHaveBeenLastCalledWith("input");
        await userEvent.click(screen.getByRole("button", { name: "Edit Output Target" }));
        expect(onEdit).toHaveBeenLastCalledWith("input");
        // Two "Edit Pipeline A" buttons exist (blocker + card); the card's is the one outside Blockers.
        const cardEdit = screen
            .getAllByRole("button", { name: "Edit Pipeline A" })
            .find((b) => !blockers.contains(b)) as HTMLElement;
        await userEvent.click(cardEdit);
        expect(onEdit).toHaveBeenLastCalledWith("pipeline-0");
    });

    it("names a disabled or archived workflow under Blockers, on the Inputs step", () => {
        const { rerender } = render(
            <WizardReviewStage
                workflow={workflow}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={{}}
                inputFiles={[]}
                validationErrors={{}}
                workflowBlockedReason="disabled"
            />
        );
        const blockers = screen.getByText("Blockers").closest("[aria-live]") as HTMLElement;
        const items = Array.from(blockers.querySelectorAll("li")).map((li) => li.textContent);
        expect(items).toHaveLength(1);
        expect(items[0]).toContain("Inputs");
        expect(items[0]).toContain("This workflow is disabled.");

        rerender(
            <WizardReviewStage
                workflow={workflow}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={{}}
                inputFiles={[]}
                validationErrors={{}}
                workflowBlockedReason="archived"
            />
        );
        expect(screen.getByText("This workflow is archived.")).toBeInTheDocument();
    });

    it("renders no Blockers section and no Edit links when nothing blocks and no handler is given", () => {
        render(
            <WizardReviewStage
                workflow={workflow}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={{}}
                inputFiles={[]}
                validationErrors={{}}
            />
        );
        expect(screen.queryByText("Blockers")).not.toBeInTheDocument();
        expect(screen.queryAllByRole("button", { name: /^Edit/ })).toHaveLength(0);
    });
});

/**
 * A pipeline card's "built-in settings" line is a statement about how the step WILL run, so it may not
 * appear while the same screen lists a blocker for that step: a require-template step with no template
 * runs with nothing, not with its defaults.
 */
describe("WizardReviewStage built-in settings line", () => {
    const requireTemplate: Pipeline = {
        ...pipeline,
        systemConfig: { requireTemplate: true },
    };
    const renderCard = (props: {
        pipelineData: Record<string, PipelineStageData>;
        validationErrors: Record<string, string[]>;
        offendingPipelines?: Array<{ pipelineId: string; pipelineName: string; reason: string }>;
    }) =>
        render(
            <WizardReviewStage
                workflow={workflow}
                databaseId="db1"
                pipelines={[requireTemplate]}
                inputFiles={[]}
                {...props}
            />
        );

    it("is shown for a template-less step that nothing blocks", () => {
        // Positive control: the line is conditional, so its absence below is only evidence if it can
        // also be present.
        renderCard({
            pipelineData: {
                "db1:pipeA": { pipelineId: "pipeA", tags: [], errors: [], params: {}, mode: 4 },
            },
            validationErrors: { "db1:pipeA": [] },
        });
        expect(screen.getByText("Runs with its built-in settings.")).toBeInTheDocument();
    });

    it("is withheld while the visited step reports a missing template", () => {
        // Mode 4 is also what a require-template step with nothing chosen resolves to, so the mode
        // alone cannot tell the two apart — the step's errors do.
        const error = "This pipeline requires a template (templateId) for execution";
        renderCard({
            pipelineData: {
                "db1:pipeA": {
                    pipelineId: "pipeA",
                    tags: [],
                    errors: [error],
                    params: {},
                    mode: 4,
                },
            },
            validationErrors: { "db1:pipeA": [error] },
        });
        expect(screen.getByText(error)).toBeInTheDocument();
        expect(screen.queryByText("Runs with its built-in settings.")).not.toBeInTheDocument();
    });

    it("is withheld for a step that was never visited and still needs a template", () => {
        renderCard({
            pipelineData: {},
            validationErrors: { "db1:pipeA": ["Template not selected"] },
        });
        expect(screen.getByText("Template not selected")).toBeInTheDocument();
        expect(screen.queryByText("Runs with its built-in settings.")).not.toBeInTheDocument();
    });

    it("is withheld for a disabled pipeline", () => {
        renderCard({
            pipelineData: {},
            validationErrors: {},
            offendingPipelines: [
                { pipelineId: "pipeA", pipelineName: "Pipeline A", reason: "disabled" },
            ],
        });
        expect(screen.getByText("This pipeline is disabled.")).toBeInTheDocument();
        expect(screen.queryByText("Runs with its built-in settings.")).not.toBeInTheDocument();
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

/**
 * The Inputs card at the scale the file manager can preset: a count and a per-asset spread first,
 * the first rows inline, and the full list windowed behind "Show all" — never hundreds of <li>s.
 */
describe("WizardReviewStage inputs at scale", () => {
    const filesFor = (count: number, assetId: string) =>
        Array.from({ length: count }, (_, i) => ({
            databaseId: "db1",
            assetId,
            relativeFileKey: `/bulk/${assetId}-${i}.txt`,
        }));
    const many = [
        ...filesFor(200, "asset-a"),
        ...filesFor(60, "asset-b"),
        ...filesFor(40, "asset-c"),
    ];

    const renderInputs = (inputFiles: any[]) =>
        render(
            <WizardReviewStage
                workflow={workflow}
                databaseId="db1"
                pipelines={[pipeline]}
                pipelineData={{}}
                inputFiles={inputFiles}
                validationErrors={{}}
            />
        );

    it("summarises 300 files per asset and lists only the first rows inline", () => {
        renderInputs(many);
        expect(screen.getByTestId("review-input-count")).toHaveTextContent(
            "300 files across 3 assets"
        );
        expect(screen.getByText("db1 / asset-a — 200 files")).toBeInTheDocument();
        expect(screen.getByText("db1 / asset-b — 60 files")).toBeInTheDocument();
        expect(screen.getByText("db1 / asset-c — 40 files")).toBeInTheDocument();
        const card = screen.getByText("Inputs").closest(".orch-outline") as HTMLElement;
        // Ten file rows plus the three per-asset lines — not three hundred.
        expect(card.querySelectorAll("li").length).toBe(13);
        expect(screen.getByRole("button", { name: "Show all 300" })).toBeInTheDocument();
    });

    it("shows all rows through a windowed list and folds back", async () => {
        renderInputs(many);
        await userEvent.click(screen.getByRole("button", { name: "Show all 300" }));
        const list = screen.getByTestId("review-input-list");
        const rows = within(list).getAllByRole("listitem");
        expect(rows.length).toBeLessThan(100);
        expect(rows[0]).toHaveTextContent("db1 / asset-a / /bulk/asset-a-0.txt");
        await userEvent.click(screen.getByRole("button", { name: "Show first 10" }));
        expect(screen.queryByTestId("review-input-list")).not.toBeInTheDocument();
    });

    it("keeps a small selection as the plain inline list with the pinned version", () => {
        renderInputs([
            { databaseId: "db1", assetId: "a", relativeFileKey: "/x.glb", versionId: "v9" },
            { databaseId: "db1", assetId: "a", relativeFileKey: "/" },
        ]);
        expect(screen.getByTestId("review-input-count")).toHaveTextContent(
            "2 files across 1 asset"
        );
        expect(screen.getByText("db1 / a / /x.glb (vv9)")).toBeInTheDocument();
        expect(screen.getByText("db1 / a / /")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Show all/ })).not.toBeInTheDocument();
        // One asset: no per-asset breakdown to add to the count.
        expect(screen.queryByText(/— 2 files/)).not.toBeInTheDocument();
    });

    it("folds a long per-asset breakdown", () => {
        const spread = Array.from({ length: 12 }, (_, i) => ({
            databaseId: "db1",
            assetId: `asset-${i}`,
            relativeFileKey: "/f.txt",
        }));
        renderInputs(spread);
        expect(screen.getByText("+4 more assets")).toBeInTheDocument();
    });
});

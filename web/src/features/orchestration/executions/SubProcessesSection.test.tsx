/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import SubProcessesSection from "./SubProcessesSection";
import type { SubExecution } from "../types";

const sub = (over: Partial<SubExecution>): SubExecution => ({
    resourceType: "stepFunctionsExecution",
    label: "3D thumbnail processing",
    resourceName: "Preview3dThumbnailStateMachine",
    status: "SUCCEEDED",
    startDate: "2026-09-11T10:00:00Z",
    stopDate: "2026-09-11T10:01:00Z",
    stageSource: "definition",
    stages: [
        { stageName: "PipelineStartTask", stateType: "Task", status: "SUCCEEDED" },
        { stageName: "Preview3dThumbnailBatchJob", stateType: "Task", status: "SUCCEEDED" },
    ],
    ...over,
});

describe("SubProcessesSection", () => {
    it("renders nothing when the details were read without sub-executions", () => {
        const { container } = render(<SubProcessesSection subExecutions={undefined} />);
        expect(container).toBeEmptyDOMElement();
    });

    it("says so when the step registered no sub-processes", () => {
        render(<SubProcessesSection subExecutions={[]} />);
        expect(screen.getByText("Sub-processes")).toBeInTheDocument();
        expect(
            screen.getByText("No sub-processes were registered for this step.")
        ).toBeInTheDocument();
        expect(screen.queryByTestId("stage-timeline")).not.toBeInTheDocument();
    });

    it("renders a header and a stage timeline per sub-process", () => {
        render(
            <SubProcessesSection
                subExecutions={[
                    sub({}),
                    sub({
                        resourceType: "batchJob",
                        label: "Preview3dThumbnailBatchJob container",
                        resourceName: "1234abcd-job",
                        stageName: "Preview3dThumbnailBatchJob",
                        status: "RUNNING",
                        stopDate: undefined,
                        stageSource: "none",
                        stages: undefined,
                    }),
                ]}
            />
        );
        expect(screen.getByText("Sub-processes (2)")).toBeInTheDocument();
        const cards = screen.getAllByTestId("sub-process");
        expect(cards).toHaveLength(2);

        expect(cards[0]).toHaveTextContent("3D thumbnail processing");
        expect(cards[0]).toHaveTextContent("Preview3dThumbnailStateMachine");
        expect(cards[0]).toHaveTextContent("Succeeded");
        expect(cards[0]).toHaveTextContent("1m 0s");
        expect(within(cards[0]).getByTestId("stage-timeline")).toBeInTheDocument();
        expect(within(cards[0]).getAllByRole("listitem")).toHaveLength(2);

        // A Batch job has no state machine, so no timeline — and the reason is stated.
        expect(cards[1]).toHaveTextContent("Preview3dThumbnailBatchJob container");
        expect(cards[1]).toHaveTextContent("in Preview3dThumbnailBatchJob");
        expect(cards[1]).toHaveTextContent("Running");
        expect(within(cards[1]).queryByTestId("stage-timeline")).not.toBeInTheDocument();
        expect(cards[1]).toHaveTextContent(
            "No stage information is available for this sub-process."
        );
    });

    it("names the farm, queue and job of a Deadline Cloud sub-process and of nothing else", () => {
        render(
            <SubProcessesSection
                subExecutions={[
                    sub({
                        resourceType: "deadlineCloudJob",
                        label: undefined,
                        resourceName: "step1-8b527-dcfix-pipe-a1",
                        status: "ABORTED",
                        stageSource: "none",
                        stages: [],
                        deadline: { farmId: "farm-0a1b", queueId: "queue-2c3d", jobId: "job-4e5f" },
                    }),
                    sub({
                        resourceType: "batchJob",
                        label: "Preview3dThumbnailBatchJob container",
                        resourceName: "1234abcd-job",
                        stageSource: "none",
                        stages: [],
                        batch: { jobId: "1234abcd-job" },
                    }),
                ]}
            />
        );
        const cards = screen.getAllByTestId("sub-process");
        expect(cards[0]).toHaveTextContent("step1-8b527-dcfix-pipe-a1");
        expect(cards[0]).toHaveTextContent("Aborted");
        expect(cards[0]).toHaveTextContent(
            "Deadline Cloud job job-4e5f · farm farm-0a1b · queue queue-2c3d"
        );
        expect(within(cards[0]).queryByTestId("stage-timeline")).not.toBeInTheDocument();
        // Control: the id line belongs to the Deadline Cloud job alone.
        expect(cards[1]).toHaveTextContent("1234abcd-job");
        expect(cards[1]).not.toHaveTextContent("Deadline Cloud job");
        expect(cards[1]).not.toHaveTextContent("farm");
    });

    it("states each way the stage list can be incomplete beside the sub-process it qualifies", () => {
        render(
            <SubProcessesSection
                subExecutions={[
                    sub({ stagesTruncated: true, historyTruncated: true }),
                    sub({ stageSource: "history" }),
                ]}
                subExecutionsTruncated
            />
        );
        const cards = screen.getAllByTestId("sub-process");
        expect(cards[0]).toHaveTextContent(
            "The state machine defines more stages than are listed here."
        );
        expect(cards[0]).toHaveTextContent(
            "Stage statuses come from a partial execution history and may lag behind the run."
        );
        expect(cards[1]).toHaveTextContent(
            "The state machine definition could not be read; stages are listed in the order they were entered."
        );
        // Control: a note does not leak onto the sub-process it does not describe.
        expect(cards[1]).not.toHaveTextContent("more stages than are listed");
        expect(
            screen.getByText("More sub-processes were registered than this view reports.")
        ).toBeInTheDocument();
    });

    it("keeps the summary but drops the timeline when the server left the stages out", () => {
        // The wire shape of a drop: each stage list the server emptied is flagged stagesTruncated; a
        // sub-process that never had stages (a Batch job) is left as it was.
        render(
            <SubProcessesSection
                subExecutions={[
                    sub({ stages: [], stagesTruncated: true }),
                    sub({
                        resourceType: "batchJob",
                        label: "Preview3dThumbnailBatchJob container",
                        stageSource: "none",
                        stages: [],
                        stagesTruncated: false,
                    }),
                ]}
                stagesDropped
            />
        );
        const cards = screen.getAllByTestId("sub-process");
        expect(cards[0]).toHaveTextContent("Succeeded");
        expect(within(cards[0]).queryByTestId("stage-timeline")).not.toBeInTheDocument();
        expect(cards[0]).toHaveTextContent(
            "Stage details were left out because this execution's details exceeded the response size limit."
        );
        // One note: the flag the drop set is not a second, definition-frame cap.
        expect(cards[0]).not.toHaveTextContent("more stages than are listed");
        expect(cards[0].querySelectorAll("p.text-yellow-700")).toHaveLength(1);
        // The Batch job keeps its own, accurate reason and is not blamed on the size limit.
        expect(cards[1]).toHaveTextContent(
            "No stage information is available for this sub-process."
        );
        expect(cards[1]).not.toHaveTextContent("left out");
        expect(cards[1].querySelectorAll("p.text-yellow-700")).toHaveLength(1);
    });

    it("renders the error of a failed sub-process and the warnings as a muted list", () => {
        render(
            <SubProcessesSection
                subExecutions={[
                    sub({ status: "FAILED", error: "States.TaskFailed", cause: "Job failed" }),
                ]}
                warnings={["DescribeStateMachine throttled for Preview3dThumbnailStateMachine"]}
            />
        );
        expect(screen.getByTestId("sub-process")).toHaveTextContent(
            "States.TaskFailed: Job failed"
        );
        expect(
            screen.getByText("DescribeStateMachine throttled for Preview3dThumbnailStateMachine")
        ).toBeInTheDocument();
    });

    it("renders a type it does not know by label, stage and raw type, with the not-abortable note", () => {
        // A type outside the abortable set (an external bridge's own compute) still gets a card: the
        // label and stage come from the registration, the type is shown raw, and the note says an
        // abort leaves it running. Nothing about an unknown type may blank the card.
        render(
            <SubProcessesSection
                subExecutions={[
                    sub({
                        resourceType: "slurmJob",
                        label: "Mock Slurm job",
                        resourceName: undefined,
                        stageName: "MockTrain",
                        status: "UNKNOWN",
                        abortable: false,
                        stageSource: "none",
                        stages: undefined,
                    }),
                ]}
            />
        );
        const card = screen.getByTestId("sub-process");
        expect(within(card).getByText("Mock Slurm job")).toBeInTheDocument();
        expect(within(card).getByText("in MockTrain")).toBeInTheDocument();
        expect(within(card).getByText(/^slurmJob ·/)).toBeInTheDocument();
        expect(
            within(card).getByText(/VAMS cannot stop this type of sub-process/)
        ).toBeInTheDocument();
    });

    it("shows the not-abortable note only when abortable is exactly false", () => {
        render(
            <SubProcessesSection
                subExecutions={[
                    sub({ abortable: true }),
                    sub({ abortable: undefined, resourceName: "legacy-response" }),
                ]}
            />
        );
        expect(screen.queryByText(/VAMS cannot stop this type of sub-process/)).toBeNull();
    });

    it("labels the SageMaker types and names the job or pipeline execution", () => {
        render(
            <SubProcessesSection
                subExecutions={[
                    sub({
                        resourceType: "sageMakerTrainingJob",
                        label: "Mock training",
                        resourceName: "mock-train-0001",
                        stageName: "Train",
                        status: "ABORTED",
                        abortable: true,
                        stageSource: "none",
                        stages: undefined,
                        sageMaker: { trainingJobName: "mock-train-0001" },
                    }),
                    sub({
                        resourceType: "sageMakerPipelineExecution",
                        label: "Nightly pipeline",
                        resourceName: "nightly",
                        status: "RUNNING",
                        abortable: true,
                        stageSource: "none",
                        stages: undefined,
                        sageMaker: { pipelineName: "mock", pipelineExecutionId: "a1b2" },
                    }),
                ]}
            />
        );
        const [training, pipeline] = screen.getAllByTestId("sub-process");
        expect(within(training).getByText(/^SageMaker training job ·/)).toBeInTheDocument();
        expect(
            within(training).getByText("SageMaker training job mock-train-0001")
        ).toBeInTheDocument();
        expect(within(pipeline).getByText(/^SageMaker pipeline execution ·/)).toBeInTheDocument();
        expect(
            within(pipeline).getByText("SageMaker pipeline execution a1b2 · pipeline mock")
        ).toBeInTheDocument();
        // Names and ids only — no ARN (and so no account id or Region) reaches the card.
        expect(screen.queryByText(/arn:/)).toBeNull();
        expect(screen.queryByText(/VAMS cannot stop this type of sub-process/)).toBeNull();
    });
});

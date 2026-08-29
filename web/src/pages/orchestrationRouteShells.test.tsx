/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The orchestration route shells: document title and the semantics of their terminal states.
 *
 * The app is a HashRouter SPA, so there is no document load to announce a route change and
 * `document.title` is the only programmatic signal that the page changed — and the only way several
 * open VAMS tabs can be told apart. Every pre-existing page calls `usePageTitle`; none of these nine
 * did, so all of them presented whatever title was last set.
 *
 * The loading and not-found states are asserted here too: they swap in place with nothing else on the
 * page saying which, so they carry `role="status"` / `role="alert"` like the module's own panels.
 *
 * The feature components are stubbed — the shells' own wiring is the subject.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

// Each factory is written out rather than built by a helper: jest hoists these above every other
// statement in the file, so a shared helper would not exist yet when the first factory runs.
jest.mock("../features/orchestration/executions/ExecutionsBoard", () => ({
    __esModule: true,
    default: () => <div data-testid="executions-board" />,
}));
jest.mock("../features/orchestration/executions/ExecutionDetailPage", () => ({
    __esModule: true,
    default: () => <div data-testid="execution-detail" />,
}));
jest.mock("../features/orchestration/pipelines/PipelinesPage", () => ({
    __esModule: true,
    default: () => <div data-testid="pipelines-page" />,
}));
jest.mock("../features/orchestration/pipelines/PipelineForm", () => ({
    __esModule: true,
    default: () => <div data-testid="pipeline-form" />,
}));
jest.mock("../features/orchestration/pipelines/TemplateEditor", () => ({
    __esModule: true,
    default: () => <div data-testid="template-editor" />,
}));
jest.mock("../features/orchestration/pipelines/TemplateForm", () => ({
    __esModule: true,
    default: () => <div data-testid="template-form" />,
    TemplateFormEditLoader: () => <div data-testid="template-form-edit" />,
}));
jest.mock("../features/orchestration/workflows/WorkflowsPage", () => ({
    __esModule: true,
    default: () => <div data-testid="workflows-page" />,
}));
jest.mock("../features/orchestration/workflows/WorkflowBuilder", () => ({
    __esModule: true,
    default: () => <div data-testid="workflow-builder" />,
}));
jest.mock("../features/orchestration/workflows/TriggersEditor", () => ({
    __esModule: true,
    default: () => <div data-testid="triggers-editor" />,
}));

jest.mock("../features/orchestration/api/queries", () => ({
    usePipeline: jest.fn(),
    useWorkflow: jest.fn(),
}));

import ExecutionsPage from "./ExecutionsPage";
import ExecutionDetail from "./ExecutionDetail";
import PipelinesPage2 from "./PipelinesPage2";
import PipelineBuilderPage from "./PipelineBuilderPage";
import TemplateListPage from "./TemplateListPage";
import TemplateBuilderPage from "./TemplateBuilderPage";
import WorkflowsPage2 from "./WorkflowsPage2";
import WorkflowBuilderPage from "./WorkflowBuilderPage";
import WorkflowTriggersPage from "./WorkflowTriggersPage";

const queries = () => require("../features/orchestration/api/queries");

/** Render one shell at `entry`, matched by `path` so its route params resolve. */
const at = (path: string, entry: string, element: React.ReactElement) =>
    render(
        <MemoryRouter initialEntries={[entry]}>
            <Routes>
                <Route path={path} element={element} />
            </Routes>
        </MemoryRouter>
    );

const LOADED_PIPELINE = {
    data: { pipelineId: "p1", pipelineName: "P1" },
    isLoading: false,
    isError: false,
};
const LOADED_WORKFLOW = {
    data: { workflowId: "wf1", workflowName: "WF One", specifiedPipelines: [] },
    isLoading: false,
};

describe("orchestration route shell titles", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        document.title = "VAMS - Databases";
        queries().usePipeline.mockReturnValue(LOADED_PIPELINE);
        queries().useWorkflow.mockReturnValue(LOADED_WORKFLOW);
    });

    it("does not inherit the previous route's title", () => {
        // Control for every assertion below: the starting title is a DIFFERENT page's, so a shell that
        // sets nothing leaves it in place and the checks would be measuring the seed value.
        expect(document.title).toBe("VAMS - Databases");
        at("/executions", "/executions", <ExecutionsPage />);
        expect(document.title).not.toBe("VAMS - Databases");
    });

    it("titles the executions board", () => {
        at("/executions", "/executions", <ExecutionsPage />);
        expect(document.title).toBe("VAMS - Executions");
    });

    it("titles an execution's detail page with its id", () => {
        at("/executions/:executionId", "/executions/exec-9", <ExecutionDetail />);
        expect(document.title).toBe("VAMS - Execution - exec-9");
    });

    it("titles the pipelines list with its database", () => {
        at("/databases/:databaseId/pipelines", "/databases/db1/pipelines", <PipelinesPage2 />);
        expect(document.title).toBe("VAMS - db1 - Pipelines");
    });

    it("titles the pipeline builder by mode", () => {
        at(
            "/databases/:databaseId/pipelines/create",
            "/databases/db1/pipelines/create",
            <PipelineBuilderPage />
        );
        expect(document.title).toBe("VAMS - db1 - Pipelines - Create Pipeline");
    });

    it("titles the pipeline editor by mode", () => {
        at(
            "/databases/:databaseId/pipelines/:pipelineId",
            "/databases/db1/pipelines/p1",
            <PipelineBuilderPage />
        );
        expect(document.title).toBe("VAMS - db1 - Pipelines - Edit Pipeline");
    });

    it("titles the templates list", () => {
        at(
            "/databases/:databaseId/pipelines/:pipelineId/templates",
            "/databases/db1/pipelines/p1/templates",
            <TemplateListPage />
        );
        expect(document.title).toBe("VAMS - db1 - Templates");
    });

    it("titles the template builder by mode", () => {
        at(
            "/databases/:databaseId/pipelines/:pipelineId/templates/:templateId",
            "/databases/db1/pipelines/p1/templates/t1",
            <TemplateBuilderPage />
        );
        expect(document.title).toBe("VAMS - db1 - Templates - Edit Template");
    });

    it("titles the workflows list with its database", () => {
        at("/databases/:databaseId/workflows", "/databases/db1/workflows", <WorkflowsPage2 />);
        expect(document.title).toBe("VAMS - db1 - Workflows");
    });

    it("titles the workflow builder by mode", () => {
        at(
            "/databases/:databaseId/workflows/:workflowId",
            "/databases/db1/workflows/wf1",
            <WorkflowBuilderPage />
        );
        expect(document.title).toBe("VAMS - db1 - Workflows - Edit Workflow");
    });

    it("titles the triggers page with the workflow it belongs to", () => {
        at(
            "/databases/:databaseId/workflows/:workflowId/triggers",
            "/databases/db1/workflows/wf1/triggers",
            <WorkflowTriggersPage />
        );
        expect(document.title).toBe("VAMS - db1 - Workflows - WF One - Triggers");
    });
});

describe("orchestration route shell terminal states", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        queries().usePipeline.mockReturnValue(LOADED_PIPELINE);
        queries().useWorkflow.mockReturnValue(LOADED_WORKFLOW);
    });

    it("announces a pipeline that could not be loaded", () => {
        queries().usePipeline.mockReturnValue({ data: undefined, isLoading: false, isError: true });

        at(
            "/databases/:databaseId/pipelines/:pipelineId",
            "/databases/db1/pipelines/nope",
            <PipelineBuilderPage />
        );

        expect(screen.getByRole("alert")).toHaveTextContent("Pipeline not found");
        expect(screen.queryByTestId("pipeline-form")).not.toBeInTheDocument();
    });

    it("announces the pipeline edit fetch while it is in flight", () => {
        queries().usePipeline.mockReturnValue({ data: undefined, isLoading: true, isError: false });

        at(
            "/databases/:databaseId/pipelines/:pipelineId",
            "/databases/db1/pipelines/p1",
            <PipelineBuilderPage />
        );

        expect(screen.getByRole("status")).toHaveTextContent("Loading pipeline");
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("makes no announcement once the pipeline form is up", () => {
        // Control: the two regions are terminal/transient states, not fixtures of the page.
        at(
            "/databases/:databaseId/pipelines/:pipelineId",
            "/databases/db1/pipelines/p1",
            <PipelineBuilderPage />
        );

        expect(screen.getByTestId("pipeline-form")).toBeInTheDocument();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });

    it("announces a workflow that could not be loaded on the triggers page", () => {
        queries().useWorkflow.mockReturnValue({ data: undefined, isLoading: false });

        at(
            "/databases/:databaseId/workflows/:workflowId/triggers",
            "/databases/db1/workflows/nope/triggers",
            <WorkflowTriggersPage />
        );

        expect(screen.getByRole("alert")).toHaveTextContent("Workflow not found");
    });

    it("announces the triggers page's own load", () => {
        queries().useWorkflow.mockReturnValue({ data: undefined, isLoading: true });

        at(
            "/databases/:databaseId/workflows/:workflowId/triggers",
            "/databases/db1/workflows/wf1/triggers",
            <WorkflowTriggersPage />
        );

        expect(screen.getByRole("status")).toHaveTextContent("Loading workflow");
    });
});

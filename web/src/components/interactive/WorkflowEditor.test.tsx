/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render } from "@testing-library/react";
import { act } from "react-dom/test-utils";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import WorkflowEditor, { workflowPipelineToElements } from "./WorkflowEditor";
import { WorkflowContext } from "../../context/WorkflowContex";

class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
}

describe("Workflow Editor", () => {
    window.ResizeObserver = ResizeObserver;

    it("renders", async () => {
        const setAsset = jest.fn();
        const setPipelines = jest.fn();
        const setWorkflowPipelines = jest.fn();
        const reloadPipelines = jest.fn();
        const setReloadPipelines = jest.fn();
        const setActiveTab = jest.fn();

        const asset = {};
        const pipelines: any[] = [];
        const workflowPipelines: any[] = [];

        render(
            <WorkflowContext.Provider
                value={{
                    asset,
                    setAsset,
                    pipelines,
                    setPipelines,
                    workflowPipelines,
                    setWorkflowPipelines,
                    reloadPipelines,
                    setReloadPipelines,
                    setActiveTab,
                }}
            >
                <div style={{ width: "800px", height: "600px" }}>
                    <WorkflowEditor />
                </div>
            </WorkflowContext.Provider>
        );
    });

    it("renders with wf pipeline", async () => {
        const setAsset = jest.fn();
        const setPipelines = jest.fn();
        const setWorkflowPipelines = jest.fn();
        const reloadPipelines = jest.fn();
        const setReloadPipelines = jest.fn();
        const setActiveTab = jest.fn();

        const asset = {};
        const pipelines: any[] = [];
        const workflowPipelines: any[] = [null];

        render(
            <div style={{ width: 800, height: 800 }}>
                <WorkflowContext.Provider
                    value={{
                        asset,
                        setAsset,
                        pipelines,
                        setPipelines,
                        workflowPipelines,
                        setWorkflowPipelines,
                        reloadPipelines,
                        setReloadPipelines,
                        setActiveTab,
                    }}
                >
                    <WorkflowEditor />
                </WorkflowContext.Provider>
            </div>
        );
    });

    it("makes elements a function of workflow pipelines", () => {
        const result = workflowPipelineToElements([], "databaseid");
        expect(result.find((x) => x.id === "asset0")).toBeTruthy();
        expect(result.length).toEqual(1);
    });

    it("makes elements a function of workflow pipelines with one pipeline", () => {
        const result = workflowPipelineToElements([null], "databaseid");
        expect(result.find((x) => x.id === "asset0")).toBeTruthy();
        expect(result.find((x) => x.id === "pipeline0")).toBeTruthy();
        expect(result.length).toEqual(5);
    });
});

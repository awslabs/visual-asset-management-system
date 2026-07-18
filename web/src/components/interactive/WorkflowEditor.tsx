/* eslint-disable react-hooks/exhaustive-deps */
/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useContext, useMemo } from "react";

import ReactFlow, {
    MiniMap,
    Controls,
    Background,
    Position,
    Node,
    Edge,
    ReactFlowProvider,
} from "reactflow";
import "reactflow/dist/style.css";
import { Button, Icon } from "@cloudscape-design/components";
import { useParams } from "react-router";
import WorkflowPipelineSelector from "../selectors/WorkflowPipelineSelector";
import { WorkflowContext } from "../../context/WorkflowContext";

const AssetID = (props: any) => {
    const { asset } = useContext(WorkflowContext) as any;

    return <>{asset ? asset.value : ""}</>;
};

const PipelineDetail = (props: any) => {
    const { index, prop } = props;
    const { pipelines, workflowPipelines } = useContext(WorkflowContext) as any;
    const [pipelineId, setPipelneId] = useState(null);
    useEffect(() => {
        if (workflowPipelines[index]) {
            setPipelneId(workflowPipelines[index].value);
        }
    }, [workflowPipelines]);
    return <>{pipelineId && pipelines[pipelineId] ? pipelines[pipelineId][prop] : "?"}</>;
};

let cacheInstance: any;

export const workflowPipelineToElements = (
    workflowPipelines: any,
    databaseId: string | undefined
): { nodes: Node[]; edges: Edge[] } => {
    let yPos = 0;
    let xPos = 0;
    let columnCounter = 0;
    const yOffsetIncrement = 75;

    const nodes: Node[] = [];
    const edges: Edge[] = [];

    workflowPipelines.forEach((elem: any, idx: number) => {
        if (yPos === 0) yPos = 75;
        else if (idx % 4 === 0) {
            xPos = 0;
            columnCounter = 0;
            yPos += 230;
        } else {
            xPos += 350;
        }

        columnCounter += 1;

        console.log("reducer", elem, idx);

        nodes.push({
            id: `pipeline${idx}`,
            position: { x: xPos, y: yPos },
            data: {
                label: (
                    <WorkflowPipelineSelector
                        database={databaseId}
                        index={idx}
                        data-testid="create-workflow-pipeline-selector"
                    />
                ),
            },
            sourcePosition: Position.Bottom,
            targetPosition: idx % 4 === 0 ? Position.Top : Position.Left,
        });

        edges.push({
            id: `asset${idx}-pipeline${idx}`,
            source: `asset${idx}`,
            target: `pipeline${idx}`,
            type: "smoothstep",
        });

        nodes.push({
            id: `asset${idx + 1}`,
            position: { x: xPos, y: yPos + yOffsetIncrement },
            data: {
                label: (
                    <>
                        <AssetID />-
                        <PipelineDetail index={idx} prop={"pipelineId"} />
                        <PipelineDetail index={idx} prop={"outputType"} />
                    </>
                ),
            },
            sourcePosition: columnCounter === 4 ? Position.Bottom : Position.Right,
            targetPosition: Position.Top,
        });

        edges.push({
            id: `pipeline${idx}-asset${idx + 1}`,
            source: `pipeline${idx}`,
            target: `asset${idx + 1}`,
            type: "smoothstep",
        });
    });

    return { nodes, edges };
};

const WorkflowEditor = (props: any) => {
    const { databaseId } = useParams();
    const { workflowPipelines, setWorkflowPipelines, setActiveTab } = useContext(
        WorkflowContext
    ) as any;

    const { nodes, edges } = workflowPipelineToElements(workflowPipelines, databaseId);

    // Detect dark mode for ReactFlow styling
    const isDark = useMemo(() => document.body.classList.contains("awsui-dark-mode"), []);

    const handleAddPipeline = () => {
        setActiveTab("pipelines");
        const newPipelines = workflowPipelines.slice();
        newPipelines.push(null);
        setWorkflowPipelines(newPipelines);
    };

    const onInit = (reactFlowInstance: any) => {
        cacheInstance = reactFlowInstance;
        reactFlowInstance.fitView();
    };

    // when nodes change, center and zoom the view so that the graph fills the center of the screen
    useEffect(() => {
        if (cacheInstance && cacheInstance.fitView) cacheInstance.fitView();
        setTimeout(() => cacheInstance && cacheInstance.fitView(), 100);
    }, [nodes]);

    return (
        <ReactFlowProvider>
            <div style={{ height: "56px", position: "absolute", zIndex: "200" }}>
                <Button variant="link" onClick={handleAddPipeline}>
                    <Icon name="add-plus" /> Pipeline
                </Button>
                {/*@todo implement undo redo*/}
                {/*<Button variant="link"><Icon name="undo"/> Undo</Button>*/}
                {/*<Button variant="link"><div style={{transform: "scaleX(-1)", display: "inline-block"}}><Icon name="undo"/></div> Redo</Button>*/}
                <Button
                    variant="link"
                    onClick={() => {
                        setWorkflowPipelines(workflowPipelines.slice(0, -1));
                    }}
                >
                    <Icon name="close" /> Remove
                </Button>
            </div>
            <div
                style={{
                    height: "743px",
                    width: "100%",
                    background: isDark ? "var(--vams-bg-primary)" : undefined,
                }}
            >
                <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onInit={onInit}
                    snapToGrid={true}
                    snapGrid={[15, 15]}
                    fitView
                    style={{ background: isDark ? "var(--vams-bg-secondary)" : undefined }}
                >
                    <MiniMap
                        nodeStrokeColor={(n) => {
                            if (n.style?.background) return n.style.background.toString();
                            if (n.type === "input") return "#0041d0";
                            if (n.type === "output") return "#ff0072";
                            if (n.type === "default") return isDark ? "#8d99a8" : "#1a192b";

                            return isDark ? "#354150" : "#eee";
                        }}
                        nodeColor={(n) => {
                            if (n.style?.background) return n.style.background.toString();

                            return isDark ? "#192534" : "#fff";
                        }}
                        nodeBorderRadius={2}
                        maskColor={isDark ? "rgba(15, 27, 42, 0.7)" : undefined}
                        style={isDark ? { backgroundColor: "#0f1b2a" } : undefined}
                    />
                    <Controls
                        style={
                            isDark
                                ? { backgroundColor: "#192534", borderColor: "#354150" }
                                : undefined
                        }
                    />
                    <Background color={isDark ? "#354150" : "#aaa"} gap={16} />
                </ReactFlow>
            </div>
        </ReactFlowProvider>
    );
};

export default WorkflowEditor;

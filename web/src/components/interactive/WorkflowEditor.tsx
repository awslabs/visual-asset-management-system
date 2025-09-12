/* eslint-disable react-hooks/exhaustive-deps */
/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useContext } from "react";

import ReactFlow, { MiniMap, Controls, Background, Elements, Position } from "react-flow-renderer";
import { Button, Icon } from "@cloudscape-design/components";
import { useParams } from "react-router";
//import AssetSelector from "../selectors/AssetSelector";
import WorkflowPipelineSelector from "../selectors/WorkflowPipelineSelector";
import { WorkflowContext } from "../../context/WorkflowContex";

const AssetID = (props: any) => {
    const { asset } = useContext(WorkflowContext);

    return <>{asset ? asset.value : ""}</>;
};

const PipelineDetail = (props: any) => {
    const { index, prop } = props;
    const { pipelines, workflowPipelines } = useContext(WorkflowContext);
    const [pipelineId, setPipelneId] = useState(null);
    useEffect(() => {
        if (workflowPipelines[index]) {
            setPipelneId(workflowPipelines[index].value);
        }
    }, [workflowPipelines]);
    return <>{pipelineId && pipelines[pipelineId] ? pipelines[pipelineId][prop] : "?"}</>;
};

let cacheInstance: any;

const onLoad = (reactFlowInstance: any) => {
    cacheInstance = reactFlowInstance;
    reactFlowInstance.fitView();
};

export const workflowPipelineToElements = (
    workflowPipelines: any,
    databaseId: string | undefined
): Elements => {
    let yPos = 0;
    let xPos = 0;
    let columnCounter = 0;
    const yOffsetIncrement = 75;
    return workflowPipelines.reduce(
        (arry: Elements, elem: any, idx: number) => {
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

            arry.push({
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
            arry.push({
                id: `asset${idx}-pipeline${idx}`,
                source: `asset${idx}`,
                target: `pipeline${idx}`,
                type: "smoothstep",
            });
            arry.push({
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
            arry.push({
                id: `pipeline${idx}-asset${idx + 1}`,
                source: `pipeline${idx}`,
                target: `asset${idx + 1}`,
                type: "smoothstep",
            });

            return arry;
        },
        [
            // {
            //     id: `asset0`,
            //     type: "input",
            //     data: {
            //         label: (
            //             <>
            //                 <AssetSelector database={databaseId} />
            //             </>
            //         ),
            //     },
            //     sourcePosition: Position.Bottom,
            //     position: { x: 0, y: 0 },
            // },
        ]
    );
};

const WorkflowEditor = (props: any) => {
    let { databaseId } = useParams();
    const { workflowPipelines, setWorkflowPipelines, setActiveTab } = useContext(WorkflowContext);

    const elements = workflowPipelineToElements(workflowPipelines, databaseId);

    const handleAddPipeline = () => {
        setActiveTab("pipelines");
        const newPipelines = workflowPipelines.slice();
        newPipelines.push(null);
        setWorkflowPipelines(newPipelines);
    };

    // when elements changes, center and zoom the view so that the graph fills the center of the screen
    useEffect(() => {
        if (cacheInstance && cacheInstance.fitView) cacheInstance.fitView();
        setTimeout(() => cacheInstance && cacheInstance.fitView(), 100);
    }, [elements]);

    return (
        <>
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
            <div style={{ height: "743px", width: "100%" }}>
                <ReactFlow
                    elements={elements}
                    onLoad={onLoad}
                    snapToGrid={true}
                    snapGrid={[25, 25]}
                >
                    <MiniMap
                        nodeStrokeColor={(n) => {
                            if (n.style?.background) return n.style.background.toString();
                            if (n.type === "input") return "#0041d0";
                            if (n.type === "output") return "#ff0072";
                            if (n.type === "default") return "#1a192b";

                            return "#eee";
                        }}
                        nodeColor={(n) => {
                            if (n.style?.background) return n.style.background.toString();

                            return "#fff";
                        }}
                        nodeBorderRadius={2}
                    />
                    <Controls />
                    <Background color="#aaa" gap={16} />
                </ReactFlow>
            </div>
        </>
    );
};

export default WorkflowEditor;

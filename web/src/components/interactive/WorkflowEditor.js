/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, {
  useState,
  useCallback,
  useRef,
  useEffect,
  useContext,
} from "react";

import ReactFlow, {
  removeElements,
  addEdge,
  MiniMap,
  Controls,
  Background,
} from "react-flow-renderer";
import { Button, Icon, Select } from "@cloudscape-design/components";
import { useParams } from "react-router";
import AssetSelector from "../selectors/AssetSelector";
import WorkflowPipelineSelector from "../selectors/WorkflowPipelineSelector";
import { WorkflowContext } from "../../context/WorkflowContex";

const AssetID = (props) => {
  const { asset } = useContext(WorkflowContext);

  return <>{asset ? asset.value : ""}</>;
};

const PipelineDetail = (props) => {
  const { index, prop } = props;
  const { pipelines, workflowPipelines } = useContext(WorkflowContext);
  const [pipelineId, setPipelneId] = useState(null);
  useEffect(() => {
    if (workflowPipelines[index]) {
      setPipelneId(workflowPipelines[index].value);
    }
  }, [workflowPipelines]);
  return (
    <>
      {pipelineId && pipelines[pipelineId] ? pipelines[pipelineId][prop] : "?"}
    </>
  );
};

let cacheInstance;

const onLoad = (reactFlowInstance) => {
  cacheInstance = reactFlowInstance;
  reactFlowInstance.fitView();
};

const WorkflowEditor = (props) => {
  let { databaseId } = useParams();
  const { loaded, loadedWorkflowPipelines, setLoadedWorkflowPipelines } = props;
  const { workflowPipelines, setWorkflowPipelines, setActiveTab } =
    useContext(WorkflowContext);
  const [firstload, setFirstLoad] = useState(false);

  const initialElements = [
    {
      id: "asset1",
      type: "input",
      data: {
        label: (
          <>
            <AssetSelector database={databaseId} />
          </>
        ),
      },
      sourcePosition: "bottom",
      position: { x: 0, y: 0 },
    },
  ];

  const [elements, setElements] = useState(initialElements);
  const yPos = useRef(0);
  const xPos = useRef(0);
  const columnCounter = useRef(0);
  const onElementsRemove = (elementsToRemove) =>
    setElements((els) => removeElements(elementsToRemove, els));
  const onConnect = (params) => setElements((els) => addEdge(params, els));

  const handleAddPipeline = useCallback(async () => {
    setActiveTab("pipelines");

    const newPipelines = workflowPipelines.slice();
    newPipelines.push(null);
    setWorkflowPipelines(newPipelines);

    if (yPos.current === 0) yPos.current = 75;
    else if (columnCounter.current === 4) {
      xPos.current = 0;
      columnCounter.current = 0;
      yPos.current += 230;
    } else {
      xPos.current += 350;
    }
    columnCounter.current += 1;
    setElements((els) => {
      const lastElement = elements[elements.length - 1];
      const lastId = Number(
        (lastElement.target || lastElement.id).replace("asset", "")
      );
      const currentId = lastId + 1;
      const pipelineIndex = currentId - 2;
      return [
        ...els,
        {
          id: currentId + "",
          position: { x: xPos.current, y: yPos.current },
          data: {
            label: (
              <WorkflowPipelineSelector
                database={databaseId}
                index={pipelineIndex}
              />
            ),
          },
          sourcePosition: "bottom",
          targetPosition: columnCounter.current === 1 ? "top" : "left",
        },
        {
          id: `asset${lastId}-${currentId}`,
          source: `asset${lastId}`,
          target: currentId + "",
          type: "smoothstep",
        },
        {
          id: `asset${currentId}`,
          position: { x: xPos.current, y: yPos.current + 65 },
          data: {
            label: (
              <>
                <AssetID />-
                <PipelineDetail index={pipelineIndex} prop={"pipelineId"} />
                <PipelineDetail index={pipelineIndex} prop={"outputType"} />
              </>
            ),
          },
          sourcePosition: columnCounter.current === 4 ? "bottom" : "right",
          targetPosition: "top",
        },
        {
          id: `${currentId}-asset${currentId}`,
          source: currentId + "",
          target: `asset${currentId}`,
          type: "smoothstep",
        },
      ];
    });
  });

  useEffect(() => {
    if (loaded && workflowPipelines.length === 0) {
      handleAddPipeline();
    }
  }, [0]);

  const handleRemovePipeline = useCallback(() => {
    setActiveTab("pipelines");
    const newPipelines = workflowPipelines.slice();
    newPipelines.pop();
    setWorkflowPipelines(newPipelines);

    if (yPos.current === 0 && columnCounter.current === 0) {
      return;
    }

    if (yPos.current === 75 && columnCounter.current === 1) {
      yPos.current = 0;
    }

    if (yPos.current > 75 && columnCounter.current === 1) {
      yPos.current -= 130;
    }

    if (columnCounter.current > 1 && columnCounter.current <= 4) {
      xPos.current -= 250;
    }

    if (columnCounter.current === 1 && yPos.current !== 0) {
      columnCounter.current = 4;
      xPos.current = 750;
    } else {
      columnCounter.current -= 1;
    }

    const newElements = elements.slice(0, -4);
    setElements(newElements);
  });

  useEffect(() => {
    if (cacheInstance && cacheInstance.fitView) cacheInstance.fitView();
    setTimeout(() => cacheInstance.fitView(), 100);
  }, [elements]);

  useEffect(() => {
    if (loaded && loadedWorkflowPipelines.length > 0) {
      setFirstLoad(true);
    }
  }, [loaded]);

  useEffect(() => {
    if (firstload) {
      if (loadedWorkflowPipelines.length > 0) {
        const shiftedPipeline = loadedWorkflowPipelines.shift();
        handleAddPipeline();
        const newPipelines = workflowPipelines.slice();
        newPipelines.push(shiftedPipeline);
        setWorkflowPipelines(newPipelines);
      } else {
        setFirstLoad(false);
      }
    }
  }, [firstload, elements]);

  return (
    <>
      <div style={{ height: "56px", position: "absolute", zIndex: "200" }}>
        <Button variant="link" onClick={handleAddPipeline}>
          <Icon name="add-plus" /> Pipeline
        </Button>
        {/*@todo implement undo redo*/}
        {/*<Button variant="link"><Icon name="undo"/> Undo</Button>*/}
        {/*<Button variant="link"><div style={{transform: "scaleX(-1)", display: "inline-block"}}><Icon name="undo"/></div> Redo</Button>*/}
        <Button variant="link" onClick={handleRemovePipeline}>
          <Icon name="close" /> Remove
        </Button>
      </div>
      <div style={{ height: "743px", width: "100%" }}>
        <ReactFlow
          elements={elements}
          onElementsRemove={onElementsRemove}
          onConnect={onConnect}
          onLoad={onLoad}
          snapToGrid={true}
          snapGrid={[25, 25]}
        >
          <MiniMap
            nodeStrokeColor={(n) => {
              if (n.style?.background) return n.style.background;
              if (n.type === "input") return "#0041d0";
              if (n.type === "output") return "#ff0072";
              if (n.type === "default") return "#1a192b";

              return "#eee";
            }}
            nodeColor={(n) => {
              if (n.style?.background) return n.style.background;

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

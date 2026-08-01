/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useRef } from "react";
import ReactFlow, { Node, Edge, ReactFlowProvider, Position } from "reactflow";
import "reactflow/dist/style.css";
import { SpecifiedPipelineRef } from "../types";

interface DagPreviewProps {
    refs: SpecifiedPipelineRef[];
}

const DagPreview: React.FC<DagPreviewProps> = ({ refs }) => {
    const isDark = useMemo(() => document.body.classList.contains("awsui-dark-mode"), []);

    const { nodes, edges } = useMemo(() => {
        const newNodes: Node[] = [];
        const newEdges: Edge[] = [];

        const xSpacing = 250;
        const ySpacing = 100;

        refs.forEach((ref, index) => {
            const xPos = index * xSpacing;
            const label = ref.jobName || ref.pipelineId || `Pipeline ${index + 1}`;

            newNodes.push({
                id: `node-${index}`,
                position: { x: xPos, y: 50 },
                data: { label },
                sourcePosition: Position.Right,
                targetPosition: Position.Left,
            });

            if (index > 0) {
                newEdges.push({
                    id: `edge-${index - 1}-${index}`,
                    source: `node-${index - 1}`,
                    target: `node-${index}`,
                    type: "smoothstep",
                });
            }
        });

        return { nodes: newNodes, edges: newEdges };
    }, [refs]);

    const reactFlowInstance = useRef<any>(null);

    const onInit = (instance: any) => {
        reactFlowInstance.current = instance;
        instance.fitView();
    };

    // `fitView` as a prop only applies on mount, so appended nodes need an explicit refit.
    useEffect(() => {
        reactFlowInstance.current?.fitView?.();
    }, [nodes]);

    return (
        <ReactFlowProvider>
            <div
                style={{
                    height: "300px",
                    width: "100%",
                    background: isDark ? "var(--vams-bg-primary)" : undefined,
                }}
            >
                <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onInit={onInit}
                    fitView
                    style={{ background: isDark ? "var(--vams-bg-secondary)" : undefined }}
                    nodesDraggable={false}
                    nodesConnectable={false}
                    elementsSelectable={false}
                />
            </div>
        </ReactFlowProvider>
    );
};

export default DagPreview;

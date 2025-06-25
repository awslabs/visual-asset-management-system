/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useContext, useEffect, useState } from "react";
import { fetchDatabasePipelines } from "../../services/APIService";
import { Select } from "@cloudscape-design/components";
import { WorkflowContext } from "../../context/WorkflowContex";

const WorkflowPipelineSelector = (props) => {
    const { database, index } = props;
    const [reload, setReload] = useState(true);
    const {
        reloadPipelines,
        setReloadPipelines,
        setPipelines,
        workflowPipelines,
        setWorkflowPipelines,
        setActiveTab,
    } = useContext(WorkflowContext);
    const [allItems, setAllItems] = useState([]);
useEffect(() => {
    const getData = async () => {
        let items = [];
        
        // If database is "GLOBAL" (Global workflow), only fetch global pipelines
        if (database === "GLOBAL") {
            const globalItems = await fetchDatabasePipelines({ databaseId: "GLOBAL" });
            if (globalItems !== false && Array.isArray(globalItems)) {
                items = globalItems;
            }
        } else {
            // For database-specific workflows, fetch both database-specific and global pipelines
            const databaseItems = await fetchDatabasePipelines({ databaseId: database });
            const globalItems = await fetchDatabasePipelines({ databaseId: "GLOBAL" });
            
            if (databaseItems !== false && Array.isArray(databaseItems)) {
                items = [...databaseItems];
            }
            
            if (globalItems !== false && Array.isArray(globalItems)) {
                items = [...items, ...globalItems];
            }
        }
        
        if (items.length > 0) {
            setReload(false);
            setAllItems(items);
            setPipelines(
                items.reduce((acc, cur) => {
                    acc[cur.pipelineId] = cur;
                    return acc;
                }, {})
            );
        }
    };
    if (reload) {
        getData();
    }
}, [database, reload, setPipelines]);

    useEffect(() => {
        if (reloadPipelines) {
            setReload(true);
            setTimeout(() => setReloadPipelines(false), 100);
        }
    }, [reloadPipelines, setReloadPipelines]);

    return (
        <Select
            selectedOption={workflowPipelines[index]}
            onChange={({ detail }) => {
                const newPipelines = workflowPipelines.slice();
                newPipelines[index] = detail.selectedOption;
                setWorkflowPipelines(newPipelines);
                setActiveTab("pipelines");
            }}
            placeholder={<>Select pipeline from {database} database.</>}
            options={allItems.map((item) => {
                return {
                    label: item.pipelineId,
                    value: item.pipelineId,
                    pipelineType: item.pipelineType,
                    pipelineExecutionType: item.pipelineExecutionType,
                    outputType: item.outputType,
                    waitForCallback: item.waitForCallback,
                    taskTimeout: item.taskTimeout,
                    taskHeartbeatTimeout: item.taskHeartbeatTimeout,
                    userProvidedResource: item.userProvidedResource,
                    inputParameters: item.inputParameters,
                    databaseId: item.databaseId,
                    tags: [
                        `input:${item.assetType}`,
                        `output:${item.outputType}`,
                        `pipelineType:${item.pipelineType}`,
                        `pipelineExecutionType:${item.pipelineExecutionType}`,
                    ],
                };
            })}
            filteringType="auto"
            selectedAriaLabel="Selected"
            data-testid={props["data-testid"] || "wfpipelinesel"}
        />
    );
};

export default WorkflowPipelineSelector;

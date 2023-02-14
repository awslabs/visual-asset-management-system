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
    pipelines,
    setPipelines,
    workflowPipelines,
    setWorkflowPipelines,
    setActiveTab,
  } = useContext(WorkflowContext);
  const [allItems, setAllItems] = useState([]);

  useEffect(() => {
    const getData = async () => {
      const items = await fetchDatabasePipelines({databaseId: database});
      if (items !== false && Array.isArray(items)) {
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
  }, [reload]);

  useEffect(() => {
    if (reloadPipelines) {
      setReload(true);
      setTimeout(() => setReloadPipelines(false), 100);
    }
  }, [reloadPipelines]);

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
          type: item.pipelineType,
          outputType: item.outputType,
          tags: [
            `input:${item.assetType}`,
            `output:${item.outputType}`,
            `type:${item.pipelineType}`,
          ],
        };
      })}
      filteringType="auto"
      selectedAriaLabel="Selected"
    />
  );
};

export default WorkflowPipelineSelector;

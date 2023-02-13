/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Modal, Select } from "@cloudscape-design/components";
import React, { useEffect, useState } from "react";
import { fetchDatabaseWorkflows, runWorkflow } from "../../services/APIService";
import { addColumnSortLabels } from "../../common/helpers/labels";

export default function WorkflowSelectorWithModal(props) {
  const { databaseId, assetId, setOpen, open } = props;
  const [reload, setReload] = useState(true);
  const [allItems, setAllItems] = useState([]);
  const [workflowId, setWorkflowId] = useState(null);

  useEffect(() => {
    const getData = async () => {
      const items = await fetchDatabaseWorkflows({databaseId: databaseId});
      if (items !== false && Array.isArray(items)) {
        setReload(false);
        setAllItems(items);
      }
    };
    if (reload) {
      getData();
    }
  }, [reload]);

  const handleExecuteWorkflow = async (event) => {
    const newWorkflowId = event.detail.selectedOption.value;
    setWorkflowId(newWorkflowId);
    const result = await runWorkflow({databaseId: databaseId, assetId: assetId, workflowId: workflowId});
    if (result !== false && Array.isArray(result)) {
      if (result[0] === false) {
        // TODO: error handling
      } else {
        window.location = result[1];
      }
    }
    handleClose();
  };

  const handleClose = () => {
    setOpen(false);
  };

  return (
    <Modal
      onDismiss={handleClose}
      visible={open}
      closeAriaLabel="Close modal"
      size="medium"
      header="Select Workflow"
    >
      <Select
        onChange={handleExecuteWorkflow}
        options={allItems.map((item) => {
          return {
            label: item.workflowId,
            value: item.workflowId,
          };
        })}
        filteringType="auto"
        selectedAriaLabel="Selected"
      />
    </Modal>
  );
}

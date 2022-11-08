import { Modal, Select } from "@awsui/components-react";
import React, { useEffect, useState } from "react";
import { fetchWorkflows } from "../../services/APIService";
import { API } from "aws-amplify";
import { addColumnSortLabels } from "../../common/helpers/labels";

export default function WorkflowSelectorWithModal(props) {
  const { databaseId, assetId, setOpen, open } = props;
  const [reload, setReload] = useState(true);
  const [allItems, setAllItems] = useState([]);
  const [workflowId, setWorkflowId] = useState(null);

  useEffect(() => {
    const getData = async () => {
      const items = await fetchWorkflows(databaseId);
      if (items !== false && Array.isArray(items)) {
        setReload(false);
        setAllItems(items);
      }
    };
    if (reload) {
      getData();
    }
  }, [reload]);

  const handleExecuteWorkflow = (event) => {
    const newWorkflowId = event.detail.selectedOption.value;
    setWorkflowId(newWorkflowId);
    API.post(
      "api",
      `database/${databaseId}/assets/${assetId}/workflows/${newWorkflowId}`,
      {}
    )
      .then((response) => {
        //@todo backend needs proper error codes
        if (
          response.message &&
          (response.message.indexOf("error") !== -1 ||
            response.message.indexOf("Error")) !== -1
        ) {
          //handle error
        } else {
          window.location = `/databases/${databaseId}/assets/${assetId}`;
        }
      })
      .catch((error) => {
        //handle error
        console.log(error);
      })
      .finally(() => {
        handleClose();
      });
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

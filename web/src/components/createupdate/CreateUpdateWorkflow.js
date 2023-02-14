/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
  Box,
  Button,
  Input,
  Grid,
  Spinner,
  SpaceBetween,
  Tabs,
  Textarea,
  Form,
  FormField,
  Container,
  Header,
  BreadcrumbGroup,
} from "@cloudscape-design/components";
import React, { useEffect, useState } from "react";
import { useParams } from "react-router";
import WorkflowEditor from "../interactive/WorkflowEditor";
import CreatePipeline from "./CreatePipeline";
import WorkflowPipelineSelector from "../selectors/WorkflowPipelineSelector";
import AssetSelector from "../selectors/AssetSelector";
import { Cache } from "aws-amplify";
import { fetchDatabaseWorkflows, saveWorkflow, runWorkflow } from "../../services/APIService";
import { WorkflowContext } from "../../context/WorkflowContex";
import { validateEntityId, verifyStringMaxLength } from "./entity-types/EntityPropTypes";

const sleep = (ms) => {
  return new Promise((resolve) => setTimeout(resolve, ms));
};

export default function CreateUpdateWorkflow(props) {
  const { databaseId, workflowId } = useParams();
  const [reload, setReload] = useState(true);
  const [loaded, setLoaded] = useState(!workflowId);
  const [saving, setSaving] = useState(false);
  const [reloadPipelines, setReloadPipelines] = useState(true);
  const [openCreatePipeline, setOpenCreatePipeline] = useState(false);
  const [asset, setAsset] = useState(null);
  const [pipelines, setPipelines] = useState([]);
  const [workflowPipelines, setWorkflowPipelines] = useState([]);
  const [loadedWorkflowPipelines, setLoadedWorkflowPipelines] = useState([]);
  const [activeTab, setActiveTab] = useState("asset");
  const [workflowIdNew, setWorkflowIDNew] = useState(workflowId);
  const [workflowDescription, setWorkflowDescription] = useState("");
  const [workflowIdError, setWorkflowIDError] = useState("");
  const [workflowDescriptionError, setWorkflowDescriptionError] = useState("");
  const [pipelinesError, setPipelinesError] = useState("");
  // state
  const [createUpdateWorkflowError, setCreateUpdateWorkflowError] = useState("");
  const [runWorkflowError, setRunWorkflowError] = useState("");
  // clear all workflow related error messages
  const clearWorkflowErrors = () => {
    setWorkflowIDError("");
    setWorkflowDescriptionError("");
    setPipelinesError("");
    setCreateUpdateWorkflowError("");
    setRunWorkflowError("");
  }

  useEffect(() => {
    const getData = async () => {
      const items = await fetchDatabaseWorkflows({databaseId: databaseId});
      if (items !== false && Array.isArray(items)) {
        setReload(false);
        const currentItem = items.find(
          ({ workflowId }) => workflowId === workflowIdNew
        );
        
        setWorkflowIDNew(currentItem.workflowId);
        setWorkflowDescription(currentItem.description);
        const loadedPipelines = currentItem?.specifiedPipelines?.functions.map(
          (item) => {
            return {
              value: item.name,
              type: item.pipelineType, 
              outputType: item.outputType
            };
          }
        );
        console.log(loadedPipelines)
        setLoadedWorkflowPipelines(loadedPipelines);
        setLoaded(true);
      }
    };
    if (reload && workflowId) {
      getData();
    }
  }, [reload]);

  useEffect(() => {
    const cachedActiveTab = Cache.getItem("workflowActiveTab");
    if (
      cachedActiveTab === "details" ||
      cachedActiveTab === "pipelines" ||
      cachedActiveTab === "asset"
    ) {
      setActiveTab(cachedActiveTab);
    }
  }, [null]);

  useEffect(() => {
    const cachedActiveTab = Cache.getItem("workflowActiveTab");
    if (activeTab !== cachedActiveTab) {
      Cache.setItem("workflowActiveTab", activeTab);
    }
  }, [activeTab]);

  const handleOpenCreatePipeline = () => {
    setOpenCreatePipeline(true);
  };

  const handleSaveWorkflow = async (event) => {
    event.preventDefault();
    setSaving(true);
    // reset all workflow-related error messages when either save or run workflow is executed
    clearWorkflowErrors();
    if (!workflowIdNew || workflowIdNew === "") {
      setWorkflowIDError("Invalid value for workflowId. Value cannot be empty.");
      setActiveTab("details");
    } else if (!validateEntityId(workflowIdNew)) {
      setWorkflowIDError("Invalid value for workflowId. Expected a valid entity id.");
      setActiveTab("details");
    } else if (!workflowDescription || workflowDescription === "") {
      setWorkflowIDError("");
      setWorkflowDescriptionError("Invalid prop description. Value cannot be empty.");
      setActiveTab("details");
    } else if (!verifyStringMaxLength(workflowDescription, 256)) {
      setWorkflowIDError("");
      setWorkflowDescriptionError("Invalid prop description. Value exceeds maximum length of 256.");
      setActiveTab("details");
    } else if (
      workflowPipelines.length === 0 ||
      workflowPipelines[0] === null
    ) {
      setWorkflowDescriptionError("");
      setPipelinesError("Must select pipelines.");
      setActiveTab("pipelines");
    } else {
      const functions = workflowPipelines.map((item) => {
        return { name: item.value, pipelineType: item.type, outputType: item.outputType };
      });
      const config = {
        body: {
          workflowId: workflowIdNew,
          databaseId: databaseId,
          description: workflowDescription,
          specifiedPipelines: { functions: functions },
        },
      };
      const result = await saveWorkflow({config: config});
      if (result !== false && Array.isArray(result)) {
        if (result[0] === false) {
          setCreateUpdateWorkflowError(`Unable to save workflow. Error: ${result[1]}`);
        } else {
          setCreateUpdateWorkflowError("");
          setReload(true);
        }
      }
    }
    setSaving(false);
  };

  const handleExecuteWorkflow = async (event) => {
    event.preventDefault();
    setSaving(true);
    // reset all workflow-related error messages when either save or run workflow is executed
    clearWorkflowErrors();
    setActiveTab("asset");
    const result = await runWorkflow({databaseId: databaseId, assetId: asset?.value, workflowId: workflowId});
    if (result !== false && Array.isArray(result)) {
      if (result[0] === false) {
        setRunWorkflowError(`Unable to run workflow. Error: ${result[1]}`);
      } else {
        window.location = result[1];
        setSaving(false);
      }
    }
    setSaving(false);
  };

  return (
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
      {saving && (
        <div
          style={{
            position: "absolute",
            zIndex: 4000,
            top: 0,
            left: 0,
            textAlign: "center",
            paddingTop: "300px",
            height: "100vh",
            width: "100%",
            pointerEvents: "none",
            background: "rgba(255,255,255,.5)",
          }}
        >
          <Spinner size="large" />
        </div>
      )}
      <Box padding={{ top: "s", horizontal: "l" }}>
        <SpaceBetween direction="vertical" size="xs">
          <BreadcrumbGroup
            items={[
              { text: "Databases", href: "/databases/" },
              {
                text: databaseId,
                href: "/databases/" + databaseId + "/workflows/",
              },
              { text: "Create Workflow" },
            ]}
            ariaLabel="Breadcrumbs"
          />
          <Container
            header={
              <Header
                variant="h2"
                actions={
                  <SpaceBetween direction="horizontal" size="xs">
                    <Button onClick={handleSaveWorkflow}>Save</Button>
                    <Button onClick={handleExecuteWorkflow} variant="primary">
                      Run Workflow
                    </Button>
                  </SpaceBetween>
                }
              >
                Container Workflow
              </Header>
            }
          >
            <Grid disableGutters gridDefinition={[{ colspan: 12 }]}>
              <div
                style={{ borderRight: "solid 1px #eaeded", minHeight: "800px" }}
              >
                <WorkflowEditor
                  loaded={loaded}
                  loadedWorkflowPipelines={loadedWorkflowPipelines}
                  setLoadedWorkflowPipelines={setLoadedWorkflowPipelines}
                />
                <div
                  style={{
                    maxWidth: "400px",
                    position: "absolute",
                    top: 0,
                    right: 0,
                    zIndex: 100,
                  }}
                >
                  <Tabs
                    activeTabId={activeTab}
                    onChange={({ detail }) => {
                      setActiveTab(detail.activeTabId);
                    }}
                    variant={"container"}
                    tabs={[
                      {
                        label: "Workflow Details",
                        id: "details",
                        content: (
                          <Form 
                          errorText={createUpdateWorkflowError}
                          style={{ padding: "5px 20px" }}
                          >
                            <SpaceBetween direction="vertical" size="xs">
                              <FormField
                                label={"Workflow Name"}
                                constraintText={"Required. All lower case, no special chars or spaces except - and _ only letters for first character min 4 and max 64."}
                                errorText={workflowIdError}
                              >
                                <Input
                                  placeholder="Workflow Name"
                                  name="workflowId"
                                  value={workflowIdNew}
                                  onChange={(event) =>
                                    setWorkflowIDNew(event.detail.value)
                                  }
                                />
                              </FormField>
                              <FormField
                                label={"Description"}
                                constraintText={"Required. Max 256 characters."}
                                errorText={workflowDescriptionError}
                              >
                                <Textarea
                                  placeholder="Description"
                                  name="workflowDescription"
                                  rows={4}
                                  value={workflowDescription}
                                  onChange={(event) =>
                                    setWorkflowDescription(event.detail.value)
                                  }
                                />
                              </FormField>
                            </SpaceBetween>
                          </Form>
                        ),
                      },
                      {
                        label: "Pipelines",
                        id: "pipelines",
                        content: (
                          <Form errorText={createUpdateWorkflowError}>
                            <FormField errorText={pipelinesError}>
                              <table style={{ width: "100%" }}>
                                <tbody>
                                  {workflowPipelines.length === 0 && (
                                    <tr>
                                      <td colSpan={2}>
                                        Click <strong>[+ Pipeline]</strong> on
                                        the left to begin.
                                      </td>
                                    </tr>
                                  )}
                                  {workflowPipelines.map((pipeline, i) => {
                                    return (
                                      <tr key={i}>
                                        <td>{i + 1}:</td>
                                        <td>
                                          <WorkflowPipelineSelector
                                            database={databaseId}
                                            index={i}
                                          />
                                        </td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                                <tfoot>
                                  <tr>
                                    <td colSpan={2}>
                                      <div style={{ marginTop: "10px" }}>
                                        <Button
                                          onClick={handleOpenCreatePipeline}
                                          variant="primary"
                                        >
                                          Create Pipeline
                                        </Button>
                                      </div>
                                    </td>
                                  </tr>
                                </tfoot>
                              </table>
                            </FormField>
                          </Form>
                        ),
                      },
                      {
                        label: "Source Asset",
                        id: "asset",
                        content: (
                          <Form
                            errorText={runWorkflowError} 
                            style={{ padding: "5px 20px" }}>
                            <AssetSelector database={databaseId} />
                          </Form>
                        ),
                      },
                    ]}
                  />
                </div>
              </div>
            </Grid>
          </Container>
        </SpaceBetween>
      </Box>
      <CreatePipeline
        open={openCreatePipeline}
        setOpen={setOpenCreatePipeline}
        setReload={setReloadPipelines}
        database={databaseId}
      />
    </WorkflowContext.Provider>
  );
}

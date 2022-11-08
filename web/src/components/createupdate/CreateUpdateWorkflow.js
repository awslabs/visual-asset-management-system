import {
  Box,
  Button,
  Input,
  Grid,
  Spinner,
  SpaceBetween,
  Tabs,
  Textarea,
  TextContent,
  FormField,
  Container,
  Header,
  BreadcrumbGroup,
} from "@awsui/components-react";
import React, { useEffect, useState } from "react";
import { useParams } from "react-router";
import WorkflowEditor from "../interactive/WorkflowEditor";
import CreatePipeline from "./CreatePipeline";
import WorkflowPipelineSelector from "../selectors/WorkflowPipelineSelector";
import AssetSelector from "../selectors/AssetSelector";
import { API, Cache } from "aws-amplify";
import { fetchWorkflows } from "../../services/APIService";
import { WorkflowContext } from "../../context/WorkflowContex";

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

  useEffect(() => {
    const getData = async () => {
      const items = await fetchWorkflows(databaseId);
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
    if (!workflowIdNew || workflowIdNew === "") {
      setWorkflowIDError("Required.");
      setActiveTab("details");
    } else if (!workflowDescription || workflowDescription === "") {
      setWorkflowDescriptionError("Required.");
      setActiveTab("details");
    } else if (
      workflowPipelines.length === 0 ||
      workflowPipelines[0] === null
    ) {
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
      await API.put("api", "workflows", config);
      window.location = `/databases/${databaseId}/workflows/${workflowIdNew}`;
    }
    setSaving(false);
  };

  const handleExecuteWorkflow = async (event) => {
    event.preventDefault();
    setSaving(true);
    API.post(
      "api",
      `database/${databaseId}/assets/${asset?.value}/workflows/${workflowId}`,
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
          window.location = `/databases/${databaseId}/assets/${asset?.value}`;
          setSaving(false);
        }
      })
      .catch((error) => {
        //handle error
        console.log(error);
      })
      .finally(() => {
        setSaving(false);
      });
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
                          <div style={{ padding: "5px 20px" }}>
                            <FormField
                              label={"Workflow Name"}
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
                            .
                          </div>
                        ),
                      },
                      {
                        label: "Pipelines",
                        id: "pipelines",
                        content: (
                          <>
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
                          </>
                        ),
                      },
                      {
                        label: "Source Asset",
                        id: "asset",
                        content: (
                          <div style={{ padding: "5px 20px" }}>
                            <AssetSelector database={databaseId} />
                          </div>
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

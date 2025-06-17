/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Container,
  Form,
  FormField,
  Grid,
  Header,
  Input,
  Link,
  Modal,
  Select,
  SpaceBetween,
  Table,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { deleteAssetLink, fetchAssetLinks, fetchAllAssets } from "../../services/APIService";
import ErrorBoundary from "../common/ErrorBoundary";
import { useStatusMessage } from "../common/StatusMessage";
import CustomTable from "../table/CustomTable";
import { featuresEnabled } from "../../common/constants/featuresEnabled";
import Synonyms from "../../synonyms";

interface AssetLinksProps {
  assetId: string;
  databaseId: string;
  assetLinks: any;
  onLinksUpdated: (links: any) => void;
}

export const AssetLinks: React.FC<AssetLinksProps> = ({
  assetId,
  databaseId,
  assetLinks,
  onLinksUpdated,
}) => {
  const { showMessage } = useStatusMessage();
  const [showLinkModal, setShowLinkModal] = useState(false);
  const [showLinkDeleteModal, setShowLinkDeleteModal] = useState(false);
  const [selectedLinkType, setSelectedLinkType] = useState<any>();
  const [searchedEntity, setSearchedEntity] = useState("");
  const [showTable, setShowTable] = useState(false);
  const [searchResult, setSearchResult] = useState<any[]>([]);
  const [selectedItems, setSelectedItems] = useState<any[]>([]);
  const [nameError, setNameError] = useState("");
  const [formError, setFormError] = useState("");
  const [modalTitle, setModalTitle] = useState("");
  const [modalAsset, setModalAsset] = useState({
    assetName: "",
    relationId: "",
  });
  const [delDisabled, setDelDisable] = useState(false);
  const [addDisabled, setAddDisable] = useState(false);
  const [modalDisable, setModalDisable] = useState(false);
  const [showLinkAlert, setShowLinkAlert] = useState(false);
  const [linkAlertMsg, setLinkAlertMsg] = useState("");
  const [useNoOpenSearch] = useState(false); // Default to false, can be updated based on config

  // Handle entity search
  const handleEntitySearch = async () => {
    try {
      if (searchedEntity) {
        let result;
        if (!useNoOpenSearch) {
          // Use OpenSearch API
          const body = {
            tokens: [],
            operation: "AND",
            from: 0,
            size: 100,
            query: searchedEntity,
            filters: [
              {
                query_string: {
                  query: '(_rectype:("asset"))',
                },
              },
            ],
          };
          result = await API.post("api", "search", {
            "Content-type": "application/json",
            body: body,
          });
          result = result?.hits?.hits;
        } else {
          // Use assets API
          result = await fetchAllAssets();
          result = result?.filter(
            (item: any) => item.databaseId.indexOf("#deleted") === -1
          );
          result = result?.filter((item: any) =>
            item.assetName.toLowerCase().includes(searchedEntity.toLowerCase())
          );
        }

        if (result && Object.keys(result).length > 0) {
          setSearchResult(result);
        } else {
          setSearchResult([]);
        }
        setShowTable(true);
      }
    } catch (error) {
      console.error("Error fetching data:", error);
      showMessage({
        type: "error",
        message: "Failed to search for assets. Please try again.",
        dismissible: true,
      });
    }
  };

  // Delete link modal
  const openDeleteModal = (item: any, title: string) => {
    setDelDisable(true);
    setShowLinkDeleteModal(true);
    setModalTitle(title);
    setModalAsset({
      assetName: item.assetName,
      relationId: item.relationId,
    });
  };

  // Delete link
  const deleteLink = async () => {
    try {
      let relationId = modalAsset.relationId;
      setModalDisable(true);

      let res = await deleteAssetLink({ relationId });
      const apiRes = await fetchAssetLinks({ assetId });
      onLinksUpdated(apiRes);
      setDelDisable(false);
      setShowLinkDeleteModal(false);
      setModalDisable(false);
      
      if (res.response && res.response.status === 403) {
        setLinkAlertMsg(res.message);
        setShowLinkAlert(true);
        setTimeout(() => {
          setShowLinkAlert(false);
        }, 10000);
      } else {
        showMessage({
          type: "success",
          message: `Successfully deleted ${modalTitle.toLowerCase()} link to ${modalAsset.assetName}`,
          dismissible: true,
          autoDismiss: true,
        });
      }
    } catch (error) {
      console.error("Error deleting asset link:", error);
      showMessage({
        type: "error",
        message: "Failed to delete link. Please try again.",
        dismissible: true,
      });
      setDelDisable(false);
      setShowLinkDeleteModal(false);
      setModalDisable(false);
    }
  };

  // Add link
  const addLink = (linktype: string, selectAsset: string) => {
    const assetLinkBody = {
      assetIdFrom: "",
      assetIdTo: "",
      relationshipType: "",
    };

    let currAsset = assetId;
    switch (linktype) {
      case "parent":
        assetLinkBody.assetIdFrom = currAsset;
        assetLinkBody.assetIdTo = selectAsset;
        assetLinkBody.relationshipType = "parent-child";
        break;
      case "child":
        assetLinkBody.assetIdFrom = selectAsset;
        assetLinkBody.assetIdTo = currAsset;
        assetLinkBody.relationshipType = "parent-child";
        break;
      case "related":
        assetLinkBody.assetIdFrom = currAsset;
        assetLinkBody.assetIdTo = selectAsset;
        assetLinkBody.relationshipType = "related";
        break;
    }

    API.post("api", "asset-links", {
      body: assetLinkBody,
    })
      .then((response) => {
        setShowLinkModal(false);
        setSearchedEntity("");
        setSelectedLinkType(undefined);
        setAddDisable(false);
        setShowTable(false);
        fetchAssetLinks({ assetId }).then((res) => {
          onLinksUpdated(res);
        });
        setFormError("");
        setNameError("");
        
        showMessage({
          type: "success",
          message: `Successfully added ${linktype} link`,
          dismissible: true,
          autoDismiss: true,
        });
      })
      .catch((err) => {
        if (err.response?.status === 400) {
          setNameError(err.response.data.message);
        } else {
          let msg = `Unable to add ${linktype} link. Error: Request failed with status code ${err.response?.status || "unknown"}`;
          setFormError(msg);
        }
        setAddDisable(false);
      });
  };

  // Table columns for asset search results
  const assetCols = [
    {
      id: "assetId",
      header: "Asset Name",
      cell: (item: any) => (
        <Link href={`#/databases/${item.databaseName}/assets/${item.assetId}`}>
          {item.assetName}
        </Link>
      ),
      sortingField: "name",
      isRowHeader: true,
    },
    {
      id: "databaseId",
      header: "Database Name",
      cell: (item: any) => item.databaseName,
      sortingField: "name",
      isRowHeader: true,
    },
    {
      id: "description",
      header: "Description",
      cell: (item: any) => item.description,
      sortingField: "alt",
    },
  ];

  // Format search results
  const assetItems = Array.isArray(searchResult)
    ? !useNoOpenSearch
      ? searchResult.map((result: any) => ({
          // Search API results
          assetName: result._source.str_assetname || "",
          databaseName: result._source.str_databaseid || "",
          description: result._source.str_description || "",
          assetId: result._source.str_assetid || "",
        }))
      : // FetchAllAssets API Results (No OpenSearch)
        searchResult.map((result: any) => ({
          assetName: result.assetName || "",
          databaseName: result.databaseId || "",
          description: result.description || "",
          assetId: result.assetId || "",
        }))
    : []; // No result

  return (
    <ErrorBoundary componentName="Asset Links">
      {/* Add Link Modal */}
      <Modal
        visible={showLinkModal}
        onDismiss={() => {
          setShowLinkModal(false);
          setSearchedEntity("");
          setSelectedLinkType(undefined);
          setShowTable(false);
          setFormError("");
          setNameError("");
        }}
        size="large"
        header={"Add Linked Assets"}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => {
                  setShowLinkModal(false);
                  setSearchedEntity("");
                  setSelectedLinkType(undefined);
                  setShowTable(false);
                  setFormError("");
                  setNameError("");
                }}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={
                  addDisabled ||
                  !selectedLinkType ||
                  !selectedLinkType.value ||
                  !selectedItems[0]?.assetId
                }
                onClick={() => {
                  setAddDisable(true);
                  addLink(selectedLinkType.value, selectedItems[0]?.assetId);
                }}
              >
                Add Links
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Form errorText={formError}>
          <SpaceBetween direction="vertical" size="l">
            <FormField
              label="Relationship Type"
              constraintText="Required. Select one event type"
            >
              <Select
                selectedOption={selectedLinkType}
                placeholder="Relationship Types"
                options={[
                  {
                    label: "Parent To",
                    value: "parent",
                  },
                  {
                    label: "Child To",
                    value: "child",
                  },
                  {
                    label: "Related To",
                    value: "related",
                  },
                ]}
                onChange={({ detail }) => {
                  setSelectedLinkType(detail.selectedOption);
                  setShowTable(false);
                  setSearchedEntity("");
                }}
              />
            </FormField>

            <FormField
              label="Entity Name"
              constraintText="Input asset name. Press Enter to search."
              errorText={nameError}
            >
              <Input
                placeholder="Search"
                type="search"
                value={searchedEntity || ""}
                onChange={({ detail }) => {
                  setSearchedEntity(detail.value);
                  setShowTable(false);
                  setSelectedItems([]);
                  setNameError("");
                }}
                onKeyDown={({ detail }) => {
                  if (detail.key === "Enter") {
                    handleEntitySearch();
                  }
                }}
              />
            </FormField>
            {showTable && (
              <FormField label="Entity">
                <CustomTable
                  columns={assetCols}
                  items={assetItems}
                  selectedItems={selectedItems}
                  setSelectedItems={setSelectedItems}
                  trackBy={"assetId"}
                />
              </FormField>
            )}
          </SpaceBetween>
        </Form>
      </Modal>

      {/* Delete Link Modal */}
      <Modal
        onDismiss={() => {
          setShowLinkDeleteModal(false);
          setDelDisable(false);
        }}
        visible={showLinkDeleteModal}
        size="medium"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => {
                  setShowLinkDeleteModal(false);
                  setDelDisable(false);
                }}
              >
                No
              </Button>
              <Button
                variant="primary"
                disabled={modalDisable}
                onClick={() => {
                  deleteLink();
                }}
              >
                Yes
              </Button>
            </SpaceBetween>
          </Box>
        }
        header={"Delete Link"}
      >
        <div>
          <p>
            {`Do you want to delete ${modalTitle} link: '`}
            <i>{modalAsset.assetName}</i>
            {`' ?`}
          </p>
        </div>
      </Modal>

      {/* Main Container */}
      <Container
        header={
          <>
            <Header
              variant="h2"
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button
                    variant="primary"
                    onClick={() => {
                      setShowLinkModal(true);
                    }}
                  >
                    Add Link
                  </Button>
                </SpaceBetween>
              }
            >
              Linked {Synonyms.Asset}s
            </Header>
            {showLinkAlert && (
              <Alert
                statusIconAriaLabel="Error"
                type="error"
                header="Forbidden"
                dismissible={true}
                onDismiss={() => {
                  setShowLinkAlert(false);
                }}
              >
                {linkAlertMsg}
              </Alert>
            )}
          </>
        }
      >
        <Grid
          gridDefinition={[
            { colspan: 4 },
            { colspan: 4 },
            { colspan: 4 },
          ]}
        >
          {/* Parent Assets Table */}
          <Table
            columnDefinitions={[
              {
                id: "assetName",
                header: "Asset Name",
                cell: (item: any) => (
                  <Link href={`#/databases/${item.databaseId}/assets/${item.assetId}`}>
                    {item.assetName || "-"}
                  </Link>
                ),
                sortingField: "assetName",
                isRowHeader: true,
              },
              {
                id: "actions",
                header: "",
                cell: (item) => (
                  <Box float="right">
                    <Button
                      disabled={delDisabled}
                      iconName="remove"
                      variant="icon"
                      onClick={() => openDeleteModal(item, "Parent")}
                    ></Button>
                  </Box>
                ),
              },
            ]}
            items={assetLinks?.parent || []}
            loadingText="Loading Assets"
            sortingDisabled
            empty={
              <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
                <SpaceBetween size="m">
                  <b>No parent asset</b>
                </SpaceBetween>
              </Box>
            }
            header={<Header variant="h3">Parent Assets</Header>}
          />

          {/* Child Assets Table */}
          <Table
            columnDefinitions={[
              {
                id: "assetName",
                header: "Asset Name",
                cell: (item: any) => (
                  <Link href={`#/databases/${item.databaseId}/assets/${item.assetId}`}>
                    {item.assetName || "-"}
                  </Link>
                ),
                sortingField: "assetName",
                isRowHeader: true,
              },
              {
                id: "actions",
                header: "",
                cell: (item) => (
                  <Box float="right">
                    <Button
                      disabled={delDisabled}
                      iconName="remove"
                      variant="icon"
                      onClick={() => openDeleteModal(item, "Child")}
                    ></Button>
                  </Box>
                ),
              },
            ]}
            items={assetLinks?.child || []}
            loadingText="Loading Assets"
            sortingDisabled
            empty={
              <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
                <SpaceBetween size="m">
                  <b>No child asset</b>
                </SpaceBetween>
              </Box>
            }
            header={<Header variant="h3">Child Assets</Header>}
          />

          {/* Related Assets Table */}
          <Table
            columnDefinitions={[
              {
                id: "assetName",
                header: "Asset Name",
                cell: (item: any) => (
                  <Link href={`#/databases/${item.databaseId}/assets/${item.assetId}`}>
                    {item.assetName || "-"}
                  </Link>
                ),
                sortingField: "assetName",
                isRowHeader: true,
              },
              {
                id: "actions",
                header: "",
                cell: (item) => (
                  <Box float="right">
                    <Button
                      disabled={delDisabled}
                      iconName="remove"
                      variant="icon"
                      onClick={() => openDeleteModal(item, "Related")}
                    ></Button>
                  </Box>
                ),
              },
            ]}
            items={assetLinks?.relatedTo || []}
            loadingText="Loading Assets"
            sortingDisabled
            empty={
              <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
                <SpaceBetween size="m">
                  <b>No related asset</b>
                </SpaceBetween>
              </Box>
            }
            header={<Header variant="h3">Related Assets</Header>}
          />
        </Grid>
      </Container>
    </ErrorBoundary>
  );
};

export default AssetLinks;

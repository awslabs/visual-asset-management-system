/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useReducer, useState } from "react";
import {
  Alert,
  Box,
  BreadcrumbGroup,
  Container,
  Header,
  SpaceBetween,
  AlertProps,
} from "@cloudscape-design/components";
import { useNavigate, useParams } from "react-router";
import { Cache, API } from "aws-amplify";
import { AssetDetailContext, assetDetailReducer } from "../../context/AssetDetailContext";
import { AssetDetail } from "../../pages/AssetUpload/AssetUpload";
import { 
  fetchAsset, 
  fetchAssetLinks,
  fetchtagTypes 
} from "../../services/APIService";
import { fetchAssetS3Files } from "../../services/AssetVersionService";
import { StatusMessageProvider } from "../common/StatusMessage";
import ErrorBoundary from "../common/ErrorBoundary";
import AssetDetailsPane from "./AssetDetailsPane";
import TabbedContainer from "./TabbedContainer";
import AssetDeleteModal from "../modals/AssetDeleteModal";
import { UpdateAsset } from "../createupdate/UpdateAsset";
import WorkflowSelectorWithModal from "../selectors/WorkflowSelectorWithModal";
import ControlledMetadata from "../metadata/ControlledMetadata";
import AssetLinks from "./AssetLinks";
import localforage from "localforage";
import Synonyms from "../../synonyms";
import { featuresEnabled } from "../../common/constants/featuresEnabled";

// Fetch tag types and store in localStorage
fetchtagTypes().then((res) => {
  const tagTypesString = JSON.stringify(res);
  localStorage.setItem("tagTypes", tagTypesString);
});

export default function ViewAsset() {
  const { databaseId, assetId } = useParams();
  const navigate = useNavigate();

  // State
  const [state, dispatch] = useReducer(assetDetailReducer, {
    isMultiFile: false,
    isDistributable: true,
    Asset: []
  } as AssetDetail);
  const [asset, setAsset] = useState<any>({});
  const [assetFiles, setAssetFiles] = useState<any[]>([]);
  const [loadingAssetFiles, setLoadingAssetFiles] = useState(false);
  const [assetLinks, setAssetLinks] = useState<any>({});
  const [openUpdateAsset, setOpenUpdateAsset] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [workflowOpen, setWorkflowOpen] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [showApiError, setShowApiError] = useState(false);
  const [apiErrorType, setApiErrorType] = useState<AlertProps.Type>("error");

  // Config
  const config = Cache.getItem("config");
  const [useNoOpenSearch] = useState(
    config?.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH)
  );

  // Fetch asset data
  useEffect(() => {
    const fetchAssetData = async (): Promise<void> => {
      if (!databaseId || !assetId) return;

      try {
        const item = await fetchAsset({ 
          databaseId, 
          assetId, 
          showArchived: true 
        });
        
        if (item !== false) {
          setAsset(item);
        } else if (typeof item === 'string' && item.includes('not found')) {
          setApiError("Asset not found. The requested asset may have been deleted or you may not have permission to access it.");
          setShowApiError(true);
          setApiErrorType("error");
        }
      } catch (error) {
        console.error("Error fetching asset:", error);
        setApiError("Failed to load asset data. Please try again later.");
        setShowApiError(true);
        setApiErrorType("error");
      }

      // Load from localforage if available
      try {
        const value: any = await localforage.getItem(assetId);
        if (value && value.Asset) {
          dispatch({
            type: "SET_ASSET_DETAIL",
            payload: {
              isMultiFile: value.isMultiFile,
              assetId: assetId,
              assetName: value.assetName,
              databaseId: databaseId,
              description: value.description,
              key: value.key || value.assetLocation["Key"],
              assetLocation: {
                Key: value.key || value.assetLocation["Key"],
              },
              assetType: value.assetType,
              isDistributable: value.isDistributable,
              Asset: value.Asset,
            },
          });
        } else if (asset && asset.assetId) {
          dispatch({
            type: "SET_ASSET_DETAIL",
            payload: {
              isMultiFile: asset.isMultiFile,
              assetId: assetId,
              assetName: asset.assetName,
              databaseId: databaseId,
              description: asset.description,
              key: asset.key || (asset.assetLocation && asset.assetLocation["Key"]),
              assetLocation: {
                Key: asset.key || (asset.assetLocation && asset.assetLocation["Key"]),
              },
              assetType: asset.assetType,
              isDistributable: asset.isDistributable,
              Asset: [],
            },
          });
        }
      } catch (error) {
        console.error("Error loading from localforage:", error);
      }
    };

    fetchAssetData();
  }, [databaseId, assetId, asset?.assetId]);

  // Fetch asset files
  useEffect(() => {
    const fetchFiles = async () => {
      if (!asset?.assetId) return;
      
      setLoadingAssetFiles(true);
      try {
        const [success, files] = await fetchAssetS3Files({ databaseId: databaseId!, assetId: assetId!, includeArchived: false });
        if (success && files && Array.isArray(files)) {
          // Sort files by relativePath in ascending order
          const sortedFiles = [...files].sort((a, b) => 
            a.relativePath.localeCompare(b.relativePath)
          );
          setAssetFiles(sortedFiles);
        } else if (!success && typeof files === 'string' && files.includes('not found')) {
          setApiError("Asset files not found. The requested asset may have been deleted or you may not have permission to access it.");
          setShowApiError(true);
          setApiErrorType("error");
        } else if (!success) {
          setApiError("Failed to load asset files. Please try again later.");
          setShowApiError(true);
          setApiErrorType("error");
        }
      } catch (error) {
        console.error("Error fetching asset files:", error);
        setApiError("Failed to load asset files. Please try again later.");
        setShowApiError(true);
        setApiErrorType("error");
      } finally {
        setLoadingAssetFiles(false);
      }
    };
    
    fetchFiles();
  }, [asset?.assetId, assetId, databaseId]);

  // Fetch asset links
  useEffect(() => {
    const fetchLinks = async () => {
      if (!assetId) return;
      
      try {
        const res = await fetchAssetLinks({ assetId });
        if (res && typeof res === 'object') {
          setAssetLinks(res);
        } else if (typeof res === 'string' && res.includes('not found')) {
          setApiError("Asset links not found. The requested asset may have been deleted or you may not have permission to access it.");
          setShowApiError(true);
          setApiErrorType("error");
        }
      } catch (error) {
        console.error("Error fetching asset links:", error);
        setApiError("Failed to load asset links. Please try again later.");
        setShowApiError(true);
        setApiErrorType("error");
      }
    };
    
    fetchLinks();
  }, [assetId]);

  // Handle opening the update asset modal
  const handleOpenUpdateAsset = () => {
    setOpenUpdateAsset(true);
  };

  // Handle opening the delete modal
  const handleOpenDeleteModal = () => {
    setShowDeleteModal(true);
  };

  // Handle opening the workflow selector modal
  const handleExecuteWorkflow = () => {
    setWorkflowOpen(true);
  };

  return (
    <AssetDetailContext.Provider value={{ state, dispatch }}>
      <StatusMessageProvider>
        <Box padding={{ top: "s", horizontal: "l" }}>
          <SpaceBetween direction="vertical" size="l">
            {/* Breadcrumbs */}
            <BreadcrumbGroup
              items={[
                { text: Synonyms.Databases, href: "#/databases/" },
                {
                  text: databaseId,
                  href: "#/databases/" + databaseId + "/assets/",
                },
                { text: asset?.assetName, href: "" },
              ]}
              ariaLabel="Breadcrumbs"
            />

            {/* API Error Alert */}
            {showApiError && (
              <Alert 
                type={apiErrorType}
                statusIconAriaLabel="Error"
                header="API Error"
                dismissible
                onDismiss={() => setShowApiError(false)}
              >
                {apiError}
              </Alert>
            )}

            {/* Asset Header */}
            <Header variant="h1">
              {asset?.assetName} {asset?.status === 'archived' && <span style={{ color: '#888' }}>(Archived)</span>}
            </Header>

            {/* Asset Details Pane */}
            <AssetDetailsPane
              asset={asset}
              databaseId={databaseId || ""}
              onOpenUpdateAsset={handleOpenUpdateAsset}
              onOpenDeleteModal={handleOpenDeleteModal}
            />

            {/* Tabbed Container */}
            <TabbedContainer
              assetName={asset?.assetName || ""}
              assetFiles={assetFiles}
              assetId={assetId || ""}
              databaseId={databaseId || ""}
              loadingFiles={loadingAssetFiles}
              onExecuteWorkflow={handleExecuteWorkflow}
            />

            {/* Asset Links */}
            <AssetLinks
              assetId={assetId || ""}
              databaseId={databaseId || ""}
              assetLinks={assetLinks}
              onLinksUpdated={(links: any) => setAssetLinks(links)}
            />

            {/* Metadata */}
            <ErrorBoundary componentName="Metadata">
              {databaseId && assetId && (
                <ControlledMetadata databaseId={databaseId} assetId={assetId} />
              )}
            </ErrorBoundary>
          </SpaceBetween>
        </Box>

        {/* Modals */}
        {asset && (
          <UpdateAsset
            asset={asset}
            isOpen={openUpdateAsset}
            onClose={() => setOpenUpdateAsset(false)}
            onComplete={() => {
              setOpenUpdateAsset(false);
              window.location.reload();
            }}
          />
        )}

        <WorkflowSelectorWithModal
          assetId={assetId || ""}
          databaseId={databaseId || ""}
          open={workflowOpen}
          setOpen={setWorkflowOpen}
          assetFiles={assetFiles}
        />

        <AssetDeleteModal
          visible={showDeleteModal}
          onDismiss={() => setShowDeleteModal(false)}
          mode="asset"
          selectedAssets={asset ? [asset] : []}
          databaseId={databaseId}
          onSuccess={(operation) => {
            setShowDeleteModal(false);
            // Navigate back to search page after successful deletion / archival
            navigate(databaseId ? `/search/${databaseId}/assets` : '/search');
          }}
        />
      </StatusMessageProvider>
    </AssetDetailContext.Provider>
  );
}

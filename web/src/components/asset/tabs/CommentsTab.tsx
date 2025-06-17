/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useRef } from "react";
import { Box, Button, TextContent, Alert } from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { generateUUID } from "../../../common/utils/utils";
import { fetchAllComments } from "../../../services/APIService";
import JoditEditor from "jodit-react";
import LoadingIcons from "react-loading-icons";
import PopulateComments from "./comments/PopulateComments";
import "./comments/Comments.css";

interface CommentsTabProps {
  assetId: string;
  databaseId: string;
  isActive: boolean;
}

export const CommentsTab: React.FC<CommentsTabProps> = ({ assetId, databaseId, isActive }) => {
  const [reload, setReload] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [showLoadingIcon, setShowLoadingIcon] = useState<boolean>(false);
  const [displayItemsWhileLoading, setDisplayItemsWhileLoading] = useState<boolean>(false);
  const [allComments, setAllComments] = useState<Array<any>>([]);
  const [userId, setUserId] = useState<string>("");
  const [content, setContent] = useState<string>("");
  const [showAlert, setShowAlert] = useState(false);
  const [alertMsg, setAlertMsg] = useState("");
  const [asset, setAsset] = useState<any>(null);
  const editor = useRef(null);
  const listInnerRef = useRef(null);

  // Config for the Jodit editor
  const config = {
    readonly: false,
    minHeight: 100,
    showCharsCounter: false,
    showWordsCounter: false,
    showXPathInStatusbar: false,
    maxWidth: "auto",
    placeholder: "",
    buttons: [
      "bold",
      "italic",
      "strikethrough",
      "underline",
      "|",
      "ul",
      "ol",
      "link",
      "|",
      "eraser",
    ],
    buttonsMD: [
      "bold",
      "italic",
      "strikethrough",
      "underline",
      "|",
      "ul",
      "ol",
      "link",
      "|",
      "eraser",
    ],
    buttonsXS: [
      "bold",
      "italic",
      "strikethrough",
      "underline",
      "|",
      "ul",
      "ol",
      "link",
      "|",
      "eraser",
    ],
  };

  const handleUpdate = (event: string) => {
    const editorContent = event;
    setContent(editorContent);
  };

  // Load comments when the tab becomes active or when reload is triggered
  useEffect(() => {
    if (isActive || reload) {
      const status = localStorage.getItem("status");
      if (status === "403post") {
        setShowAlert(true);
        setAlertMsg("Unable to edit comment. Error: Request failed with status code 403");
        setTimeout(() => {
          setShowAlert(false);
        }, 10000);
        localStorage.setItem("status", "");
      }
      if (status === "403") {
        setShowAlert(true);
        setAlertMsg("Unable to delete comment. Error: Request failed with status code 403");
        setTimeout(() => {
          setShowAlert(false);
        }, 10000);
        localStorage.setItem("status", "");
      }

      const getUserId = async () => {
        try {
          let userName = JSON.parse(localStorage.getItem("user")!).username;
          setUserId(userName);
        } catch {
          setUserId("");
        }
      };

      const getData = async () => {
        if (!displayItemsWhileLoading) {
          setAllComments([]);
        }

        if (assetId) {
          setLoading(true);
          try {
            const items = await fetchAllComments({ assetId: assetId });
            if (items !== false && Array.isArray(items)) {
              setReload(false);
              setDisplayItemsWhileLoading(false);
              setAllComments(items.filter((item) => item.assetId.indexOf("#deleted") === -1));
            }
          } catch (error) {
            console.error("Error fetching comments:", error);
          } finally {
            setLoading(false);
            setShowLoadingIcon(false);
          }
        }
      };

      // Fetch the asset data to get version information
      const fetchAsset = async () => {
        if (assetId && databaseId) {
          try {
            const response = await API.get("api", `database/${databaseId}/assets/${assetId}`, {});
            setAsset(response);
          } catch (error) {
            console.error("Error fetching asset:", error);
          }
        }
      };

      getUserId();
      fetchAsset();
      getData();
    }
  }, [isActive, reload, assetId, databaseId, displayItemsWhileLoading]);

  // Add a comment to the database
  const addComment = async (event: { preventDefault: () => void }) => {
    // prevent page from reloading
    event.preventDefault();
    if (content.replace(/(<([^>]+)>)/gi, "") === "") {
      console.log("empty textbox, not adding comment");
      return;
    }
    setContent("");
    
    // Get the current version ID from the asset
    // Debug the asset structure
    console.log("Asset structure:", asset);
    console.log("Asset keys:", asset ? Object.keys(asset) : "No asset");
    
    // Try different ways to access the version ID
    let versionId = null;
    
    // Log all possible paths where version ID might be stored
    if (asset) {
      console.log("currentVersion:", asset.currentVersion);
      console.log("versions:", asset.versions);
      console.log("assetLocation:", asset.assetLocation);
      
      if (asset.currentVersion) {
        console.log("currentVersion keys:", Object.keys(asset.currentVersion));
      }
    }
    
    if (asset?.currentVersion?.S3Version) {
      versionId = asset.currentVersion.S3Version;
      console.log("Found version ID in currentVersion.S3Version:", versionId);
    } else if (asset?.versions && asset.versions.length > 0) {
      // Get the latest version
      const latestVersion = asset.versions[asset.versions.length - 1];
      versionId = latestVersion.S3Version;
      console.log("Found version ID in versions array:", versionId);
    } else if (asset?.assetLocation?.VersionId) {
      versionId = asset.assetLocation.VersionId;
      console.log("Found version ID in assetLocation.VersionId:", versionId);
    } else if (asset?.assetId) {
      // Use the assetId as a fallback
      versionId = asset.assetId;
      console.log("Using assetId as fallback version ID:", versionId);
    } else {
      // Use a hardcoded UUID as a last resort
      versionId = "default-version-" + generateUUID();
      console.log("Using generated UUID as version ID:", versionId);
    }
    
    const uuid = "x" + generateUUID();
    const assetVersionIdAndCommentId = `${versionId}:${uuid}`;
    
    // call the API to post the comment
    setDisplayItemsWhileLoading(true);
    setLoading(true);
    setShowLoadingIcon(true);
    try {
      const response = await API.post(
        "api",
        `comments/assets/${assetId}/assetVersionId:commentId/${assetVersionIdAndCommentId}`,
        {
          body: {
            commentBody: content,
          },
        }
      );
      console.log(response);
    } catch (e: any) {
      console.log("create comment error");
      if (e.response && e.response.status === 403) {
        setShowLoadingIcon(false);
        setShowAlert(true);
        setAlertMsg("Unable to add comment. Error: Request failed with status code 403");
        setTimeout(() => {
          setShowAlert(false);
        }, 10000);
      }
    } finally {
      setReload(true);
    }
  };

  const showLoading = (keepItemsDisplayed: boolean | ((prevState: boolean) => boolean)) => {
    setDisplayItemsWhileLoading(keepItemsDisplayed);
    setLoading(true);
  };

  return (
    <Box padding={{ top: "m", horizontal: "l" }}>
      <div className="commentSectionDiv">
        <table className="commentSectionTable commentSectionTableBorder">
          <tbody>
            <tr className="commentSectionAssetsContainerTable">
              <td>
                <div className="populateCommentsDiv" ref={listInnerRef}>
                  <div hidden={(!loading || displayItemsWhileLoading) && !showLoadingIcon}>
                    <LoadingIcons.SpinningCircles className="center" fill="#000716" />
                  </div>
                  {asset && (
                    <PopulateComments
                      loading={loading}
                      showLoading={showLoading}
                      userId={userId}
                      asset={asset}
                      allComments={allComments}
                      setReload={setReload}
                    />
                  )}
                </div>
              </td>
            </tr>
            {showAlert && (
              <tr>
                <Alert
                  statusIconAriaLabel="Error"
                  type="error"
                  dismissible={true}
                  onDismiss={() => setShowAlert(false)}
                >
                  {alertMsg}
                </Alert>
              </tr>
            )}
            <tr>
              <td className="commentSectionTableBorder">
                <div>
                  <form onSubmit={addComment}>
                    <div className="container">
                      <div className="commentSectionTextBoxContainer">
                        <JoditEditor
                          ref={editor}
                          value={content}
                          config={config}
                          onBlur={handleUpdate}
                          onChange={(newContent) => {}}
                        />
                      </div>
                      <div className="commentSectionSubmitButton">
                        <Button
                          data-testid="submitButton"
                          variant="primary"
                          disabled={!assetId}
                        >
                          Submit
                        </Button>
                      </div>
                    </div>
                  </form>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </Box>
  );
};

export default CommentsTab;

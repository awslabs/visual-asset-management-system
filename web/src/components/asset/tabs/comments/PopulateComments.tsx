/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import VersionComments from "./VersionComments";

interface CommentType {
  assetId: string;
  "assetVersionId:commentId": string;
  commentBody: string;
  commentOwnerID: string;
  commentOwnerUsername: string;
  dateCreated: string;
  dateEdited?: string;
}

interface AssetType {
  assetId: string;
  assetLocation: Object;
  assetName: string;
  assetType: string;
  currentVersion: {
    Comment: string;
    dateModified: string;
    S3Version: string;
    Version: string;
    description: string;
  };
  databaseId: string;
  description: string;
  executionId?: string;
  isDistributable: boolean;
  pipelineId?: string;
  specifiedPipelines?: Array<any>;
  versions?: Array<any>;
}

interface PopulateCommentsProps {
  loading: boolean;
  showLoading: (keepItemsDisplayed: boolean) => void;
  userId: string;
  asset: AssetType;
  allComments: CommentType[];
  setReload: (reload: boolean) => void;
}

export default function PopulateComments(props: PopulateCommentsProps) {
  const { loading, showLoading, userId, asset, allComments, setReload } = props;

  // Sort the comments so that the most recent comment appears at the bottom of the list
  allComments.sort(function (a: CommentType, b: CommentType) {
    return new Date(a.dateCreated).valueOf() - new Date(b.dateCreated).valueOf();
  });

  if (asset) {
    return (
      <div data-testid="expandableSectionDiv">
        {(() => {
          let allVersions = [];
          
          // Handle case where asset structure might be different
          try {
            if (asset.versions === undefined) {
              // If currentVersion exists and has required properties
              if (asset.currentVersion && asset.currentVersion.S3Version) {
                allVersions = [asset.currentVersion];
              } else {
                // Create a default version object if currentVersion is missing or incomplete
                allVersions = [{
                  Comment: "Current Version",
                  dateModified: new Date().toISOString(),
                  S3Version: asset.assetId || "default-version",
                  Version: "1.0",
                  description: asset.description || ""
                }];
              }
            } else {
              // Shallow copy of the item.versions array
              allVersions = [...asset.versions];
              
              // Add currentVersion if it exists
              if (asset.currentVersion && asset.currentVersion.S3Version) {
                allVersions.push(asset.currentVersion);
              }
            }
          } catch (error) {
            console.error("Error processing versions:", error);
            // Fallback to a default version
            allVersions = [{
              Comment: "Current Version",
              dateModified: new Date().toISOString(),
              S3Version: asset.assetId || "default-version",
              Version: "1.0",
              description: asset.description || ""
            }];
          }
          
          return allVersions.map((version, index) => {
            // Filter comments and only return comments for this versionId
            const comments = allComments.filter(
              (comment: CommentType) => {
                try {
                  return comment["assetVersionId:commentId"].split(":")[0] === version.S3Version;
                } catch (error) {
                  return false;
                }
              }
            );
            
            return (
              <VersionComments
                key={`${version.S3Version}:${version.Version}`}
                loading={loading}
                showLoading={showLoading}
                userId={userId}
                setReload={setReload}
                defaultExpanded={allVersions.length - 1 === index}
                version={version}
                comments={comments}
              />
            );
          });
        })()}
      </div>
    );
  } else {
    return <div data-testid="expandableSectionDiv"></div>;
  }
}

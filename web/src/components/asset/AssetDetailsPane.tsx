/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
  Box,
  Button,
  Container,
  Grid,
  Header,
  SpaceBetween,
} from "@cloudscape-design/components";
import { useNavigate } from "react-router";
import { API } from "aws-amplify";
import BellIcon from "../../resources/img/bellIcon.svg";
import { useStatusMessage } from "../common/StatusMessage";
import ErrorBoundary from "../common/ErrorBoundary";

interface AssetDetailsPaneProps {
  asset: any;
  databaseId: string;
  onOpenUpdateAsset: () => void;
  onOpenDeleteModal: () => void;
}

export const AssetDetailsPane: React.FC<AssetDetailsPaneProps> = ({
  asset,
  databaseId,
  onOpenUpdateAsset,
  onOpenDeleteModal,
}) => {
  const navigate = useNavigate();
  const { showMessage } = useStatusMessage();
  const [subscribed, setSubscribed] = useState<boolean>(false);
  const [isSubscribing, setIsSubscribing] = useState<boolean>(false);
  const [userName, setUserName] = useState<string>("");

  // Get username and check subscription status on mount or when asset changes
  useEffect(() => {
    const user = JSON.parse(localStorage.getItem("user") || '{"username": ""}');
    setUserName(user.username);
    
    if (asset?.assetId && user.username) {
      checkSubscriptionStatus(user.username);
    }
  }, [asset?.assetId]);

  // Check if user is subscribed to this asset
  const checkSubscriptionStatus = async (username: string): Promise<void> => {
    if (!asset?.assetId) return;
    
    try {
      const response = await API.post("api", "check-subscription", {
        body: {
          userId: username,
          assetId: asset.assetId,
        },
      });

      if (response.message === "success") {
        setSubscribed(true);
      } else {
        setSubscribed(false);
      }
    } catch (error) {
      console.error("Error checking subscription status:", error);
      setSubscribed(false);
    }
  };

  // Subscription handling
  const handleSubscriptionToggle = async () => {
    if (!asset?.assetId) return;

    setIsSubscribing(true);
    
    const subscriptionBody = {
      eventName: "Asset Version Change",
      entityName: "Asset",
      subscribers: [userName],
      entityId: asset.assetId,
    };

    try {
      if (subscribed) {
        // Unsubscribe
        const response = await API.del("api", "unsubscribe", {
          body: subscriptionBody,
        });

        if (response) {
          setSubscribed(false);
          showMessage({
            type: "success",
            message: (
              <span>
                You've successfully unsubscribed from <i>{subscriptionBody.eventName}</i> updates for <i>{asset.assetName}</i>.
                To resume receiving updates, please subscribe again.
              </span>
            ),
            autoDismiss: true,
            dismissible: true,
          });
        }
      } else {
        // Subscribe
        const response = await API.post("api", "subscriptions", {
          body: subscriptionBody,
        });

        if (response) {
          setSubscribed(true);
          showMessage({
            type: "success",
            message: (
              <span>
                You've successfully signed up for receiving updates on <i>{subscriptionBody.eventName}</i> for <i>{asset.assetName}</i>.
                Please check your inbox and confirm the subscription.
              </span>
            ),
            autoDismiss: true,
            dismissible: true,
          });
        }
      }
    } catch (error: any) {
      console.error("Subscription error:", error);
      showMessage({
        type: "error",
        message: `Failed to ${subscribed ? "unsubscribe" : "subscribe"}: ${error.message || "Unknown error"}`,
        dismissible: true,
      });
    } finally {
      setIsSubscribing(false);
    }
  };

  return (
    <ErrorBoundary componentName="Asset Details">
      <Container
        header={
          <Header
            variant="h2"
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={onOpenDeleteModal}>Delete</Button>
                <Button onClick={onOpenUpdateAsset}>Edit</Button>
                {asset && (
                  <Button
                    iconAlign="right"
                    iconUrl={subscribed ? BellIcon : ""}
                    variant={subscribed ? "normal" : "primary"}
                    onClick={handleSubscriptionToggle}
                    loading={isSubscribing}
                  >
                    {subscribed ? "Subscribed" : "Subscribe"}
                  </Button>
                )}
              </SpaceBetween>
            }
          >
            Asset Details
          </Header>
        }
      >
        <Grid
          gridDefinition={[
            { colspan: 4 },
            { colspan: 4 },
            { colspan: 4 },
          ]}
        >
          {/* Column 1 */}
          <div>
            <div style={{ fontSize: "16px", fontWeight: "bold", marginBottom: "4px" }}>Asset Id</div>
            <div style={{ marginBottom: "16px" }}>{asset?.assetId}</div>
            <div style={{ fontSize: "16px", fontWeight: "bold", marginBottom: "4px" }}>Description</div>
            <div style={{ marginBottom: "16px" }}>{asset?.description}</div>
            <div style={{ fontSize: "16px", fontWeight: "bold", marginBottom: "4px" }}>Tags</div>
            <div style={{ marginBottom: "16px" }}>
              {Array.isArray(asset?.tags) && asset.tags.length > 0
                ? asset.tags
                    .map((tag: any) => {
                      const tagType = JSON.parse(
                        localStorage.getItem("tagTypes") ||
                          '{"tagTypeName": "", "tags": []}'
                      ).find((type: any) =>
                        type.tags.includes(tag)
                      );

                      //If tagType has required field add [R] to tag type name
                      if (tagType && tagType.required === "True") {
                        tagType.tagTypeName += " [R]";
                      }

                      return tagType
                        ? `${tag} (${tagType.tagTypeName})`
                        : tag;
                    })
                    .join(", ")
                : "No tags assigned"}
            </div>
          </div>
          
          {/* Column 2 */}
          <div>
            <div style={{ fontSize: "16px", fontWeight: "bold", marginBottom: "4px" }}>Asset Type</div>
            <div style={{ marginBottom: "16px" }}>{asset?.assetType}</div>
            <div style={{ fontSize: "16px", fontWeight: "bold", marginBottom: "4px" }}>Is Distributable</div>
            <div style={{ marginBottom: "16px" }}>{asset?.isDistributable === true ? "Yes" : "No"}</div>
          </div>
          
          {/* Column 3 */}
          <div>
            <div style={{ fontSize: "16px", fontWeight: "bold", marginBottom: "4px" }}>Version</div>
            <div style={{ marginBottom: "16px" }}>
              v{asset?.currentVersion?.Version}
            </div>
            <div style={{ fontSize: "16px", fontWeight: "bold", marginBottom: "4px" }}>Version Date</div>
            <div style={{ marginBottom: "16px" }}>
              {asset?.currentVersion?.DateModified 
                ? new Date(asset.currentVersion.DateModified).toLocaleString('en-US', {
                    year: 'numeric',
                    month: 'numeric',
                    day: 'numeric',
                    hour: 'numeric',
                    minute: 'numeric',
                    second: 'numeric',
                    hour12: true
                  })
                : ''}
            </div>
          </div>
        </Grid>
      </Container>
    </ErrorBoundary>
  );
};

export default AssetDetailsPane;

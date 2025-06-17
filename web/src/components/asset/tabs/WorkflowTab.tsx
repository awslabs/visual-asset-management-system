/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Button, Container, Header, SpaceBetween } from "@cloudscape-design/components";
import ErrorBoundary from "../../common/ErrorBoundary";
import { LoadingSpinner } from "../../common/LoadingSpinner";
import { WorkflowExecutionListDefinition } from "../../list/list-definitions/WorkflowExecutionListDefinition";
import { fetchDatabaseWorkflows, fetchWorkflowExecutions } from "../../../services/APIService";
import { useNavigate } from "react-router";
import { useStatusMessage } from "../../common/StatusMessage";
import RelatedTableList from "../../list/RelatedTableList";

interface WorkflowTabProps {
  databaseId: string;
  assetId: string;
  isActive: boolean;
  onExecuteWorkflow: () => void;
}

export const WorkflowTab: React.FC<WorkflowTabProps> = ({
  databaseId,
  assetId,
  isActive,
  onExecuteWorkflow,
}) => {
  const navigate = useNavigate();
  const { showMessage } = useStatusMessage();
  const [loading, setLoading] = useState(true);
  const [allItems, setAllItems] = useState<any[]>([]);
  const [reload, setReload] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const WorkflowHeaderControls = () => {
      return (
          <>
              <div
                  style={{
                      width: "calc(100% - 40px)",
                      textAlign: "right",
                      position: "absolute",
                  }}
              >
                  <Button 
                  variant={"primary"}
                  onClick={() => onExecuteWorkflow()}>Execute Workflow</Button>
              </div>
          </>
      );
  };

  // Fetch workflows and executions when the tab is active or when reload is triggered
  useEffect(() => {
    // Only fetch data when the tab is active or when we need to reload
    if (!isActive && !reload) return;

    const getData = async () => {
      setLoading(true);
      setError(null);
      
      try {
        const items = await fetchDatabaseWorkflows({ databaseId });
        
        if (items !== false && Array.isArray(items)) {
          const newRows = [];
          
          for (let i = 0; i < items.length; i++) {
            const newParentRow = Object.assign({}, items[i]);
            newParentRow.name = newParentRow?.workflowId;
            newRows.push(newParentRow);
            
            const workflowId = newParentRow?.workflowId;
            try {
              const subItems = await fetchWorkflowExecutions({
                databaseId,
                assetId,
                workflowId,
              });
              
              if (subItems !== false && Array.isArray(subItems)) {
                for (let j = 0; j < subItems.length; j++) {
                  const newParentRowChild = Object.assign({}, subItems[j]);
                  newParentRowChild.parentId = workflowId;
                  newParentRowChild.name = newParentRowChild.executionId;
                  
                  if (newParentRowChild.stopDate === "") {
                    newParentRowChild.stopDate = "N/A";
                  }
                  
                  newRows.push(newParentRowChild);
                }
              }
            } catch (execError) {
              console.error("Error fetching workflow executions:", execError);
            }
          }
          
          setAllItems(newRows);
          setLoading(false);
          setReload(false);
        } else if (typeof items === 'string' && items.includes('not found')) {
          setError("Workflow data not found. The requested asset may have been deleted or you may not have permission to access it.");
          setLoading(false);
          setReload(false);
        }
      } catch (error: any) {
        console.error("Error fetching workflows:", error);
        setError(`Failed to load workflow data: ${error.message || "Unknown error"}`);
        setLoading(false);
        setReload(false);
        
        showMessage({
          type: "error",
          message: `Failed to load workflow data: ${error.message || "Unknown error"}`,
          dismissible: true,
        });
      }
    };

    getData();
  }, [isActive, reload, databaseId, assetId, showMessage]);

  // If there's an error, show it
  if (error) {
    return (
      <ErrorBoundary componentName="Workflows">
        <Container header={<Header variant="h2">Workflows</Header>}>
          <div className="error-message">
            {error}
            <Button onClick={() => setReload(true)}>Retry</Button>
          </div>
        </Container>
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary componentName="Workflows">
      {loading ? (
        <LoadingSpinner text="Loading workflows..." />
      ) : (
        <RelatedTableList
          allItems={allItems}
          loading={loading}
          listDefinition={WorkflowExecutionListDefinition}
          databaseId={databaseId}
          setReload={setReload}
          parentId={"workflowId"}
          //@ts-ignore
          HeaderControls={WorkflowHeaderControls}
        />
      )}
    </ErrorBoundary>
  );
};

export default WorkflowTab;

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
  BreadcrumbGroup,
  Grid,
  Input,
  SpaceBetween,
  Textarea,
  TextContent,
} from "@cloudscape-design/components";
import React, { useEffect, useState } from "react";
import { useParams } from "react-router";
import { fetchAllPipelines } from "../../services/APIService";

export default function ViewPipeline() {
  const { pipelineName } = useParams();
  const [reload, setReload] = useState(true);
  const [databaseId, setDatabaseId] = useState("");
  const [pipelineDescription, setPipelineDescription] = useState("");
  const [assetType, setAssetType] = useState("");
  const [outputType, setOutputType] = useState("");
  const [pipelineType, setPipelineType] = useState("SageMaker");

  useEffect(() => {
    const getData = async () => {
      const items = await fetchAllPipelines();
      if (items !== false && Array.isArray(items)) {
        setReload(false);
        const currentItem = items.find(
          ({ pipelineId }) => pipelineId === pipelineName
        );
        setDatabaseId(currentItem?.databaseId);
        setPipelineDescription(currentItem?.description);
        setAssetType(currentItem?.assetType);
        setOutputType(currentItem?.outputType);
        setPipelineType(currentItem?.pipelineType);
      }
    };
    if (reload) {
      getData();
    }
  }, [reload]);

  return (
    <Grid padding={{ top: "s", horizontal: "l" }}>
      <SpaceBetween direction="vertical" size="xs">
        <BreadcrumbGroup
          items={[
            { text: "Databases", href: "/databases/" },
            { text: databaseId, href: "/databases/" + databaseId },
            { text: "Pipelines", href: "/pipelines/" },
            { text: pipelineName, href: "/pipelines/" + pipelineName },
          ]}
          ariaLabel="Breadcrumbs"
        />
        <h1>{pipelineName}</h1>
        <Grid gridDefinition={[{ colspan: 4 }, { colspan: 8 }]}>
          <TextContent>Pipeline Name</TextContent>
          <Input
            placeholder="Pipeline Name"
            name="pipelineId"
            value={pipelineName}
            disabled
          />
          <TextContent>Database Name</TextContent>
          <Input
            placeholder="Database Name"
            name="databaseId"
            value={databaseId}
            disabled
          />
          <TextContent>Pipeline Type</TextContent>
          <Input name="pipelineType" value={pipelineType} disabled />
          <TextContent>Description</TextContent>
          <Textarea
            placeholder="Description"
            name="pipelineDescription"
            rows={4}
            value={pipelineDescription}
            onChange={({ detail }) => setPipelineDescription(detail.value)}
          />
          <TextContent>Asset Type</TextContent>
          <Input
            placeholder=".csv, .glb, etc."
            name="assetType"
            value={assetType}
            onChange={({ detail }) => setAssetType(detail.value)}
          />
          <TextContent>Output Type</TextContent>
          <Input
            placeholder=".csv, .glb, etc."
            name="outputType"
            value={outputType}
            onChange={({ detail }) => setOutputType(detail.value)}
          />
        </Grid>
      </SpaceBetween>
    </Grid>
  );
}

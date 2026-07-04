/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Alert from "@cloudscape-design/components/alert";
import BreadcrumbGroup from "@cloudscape-design/components/breadcrumb-group";
import Button from "@cloudscape-design/components/button";
import Grid from "@cloudscape-design/components/grid";
import Input from "@cloudscape-design/components/input";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Textarea from "@cloudscape-design/components/textarea";
import TextContent from "@cloudscape-design/components/text-content";
import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { fetchAllPipelines } from "../../services/APIService";
import Synonyms from "../../synonyms";
import { usePageTitle } from "../../hooks/usePageTitle";

export default function ViewPipeline() {
    const { pipelineName } = useParams();
    usePageTitle("Pipeline", pipelineName);
    const [reload, setReload] = useState(true);
    const [databaseId, setDatabaseId] = useState("");
    const [pipelineDescription, setPipelineDescription] = useState("");
    const [assetType, setAssetType] = useState("");
    const [outputType, setOutputType] = useState("");
    const [pipelineType, setPipelineType] = useState("standardFile");
    const [pipelineExecutionType, setPipelineExecutionType] = useState("Lambda");
    const [resourceDisplay, setResourceDisplay] = useState<any>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const getData = async () => {
            setError(null);
            const items: any = await fetchAllPipelines();
            if (items !== false && Array.isArray(items)) {
                setReload(false);
                const currentItem: any = items.find(
                    ({ pipelineId }: any) => pipelineId === pipelineName
                );
                setDatabaseId(currentItem?.databaseId);
                setPipelineDescription(currentItem?.description);
                setAssetType(currentItem?.assetType);
                setOutputType(currentItem?.outputType);
                setPipelineType(currentItem?.pipelineType);
                setPipelineExecutionType(currentItem?.pipelineExecutionType);

                // Parse userProvidedResource for display
                if (currentItem?.userProvidedResource) {
                    try {
                        const resource = JSON.parse(currentItem.userProvidedResource);
                        setResourceDisplay(resource);
                    } catch (e) {
                        console.log("Failed to parse userProvidedResource", e);
                        setResourceDisplay(null);
                    }
                }
            } else {
                setReload(false);
                // The service layer returns the API error message string on failure.
                setError(
                    typeof items === "string" && items.trim() !== ""
                        ? items
                        : "Failed to load pipeline. Please try refreshing."
                );
            }
        };
        if (reload) {
            getData();
        }
    }, [pipelineName, reload]);

    return (
        <>
            <Grid {...({ padding: { top: "s", horizontal: "l" } } as any)}>
                <SpaceBetween direction="vertical" size="xs">
                    <BreadcrumbGroup
                        items={[
                            { text: Synonyms.Databases, href: "#/databases/" },
                            { text: databaseId, href: "#/databases/" + databaseId },
                            { text: "Pipelines", href: "#/pipelines/" },
                            { text: pipelineName as string, href: "#/pipelines/" + pipelineName },
                        ]}
                        ariaLabel="Breadcrumbs"
                    />
                    <h1>{pipelineName}</h1>
                    {error && (
                        <Alert
                            type="error"
                            dismissible
                            onDismiss={() => setError(null)}
                            action={<Button onClick={() => setReload(true)}>Retry</Button>}
                        >
                            {error}
                        </Alert>
                    )}
                    <Grid gridDefinition={[{ colspan: 4 }, { colspan: 8 }]}>
                        <TextContent>Pipeline Name</TextContent>
                        <Input
                            placeholder="Pipeline Name"
                            name="pipelineId"
                            value={pipelineName as string}
                            disabled
                        />
                        <TextContent>{Synonyms.Database} Name</TextContent>
                        <Input
                            placeholder={`${Synonyms.Database} Name`}
                            name="databaseId"
                            value={databaseId}
                            disabled
                        />
                        <TextContent>Pipeline Type</TextContent>
                        <Input name="pipelineType" value={pipelineType} disabled />
                        <TextContent>Pipeline Execution Type</TextContent>
                        <Input
                            name="pipelineExecutionType"
                            value={pipelineExecutionType}
                            disabled
                        />
                        {resourceDisplay && resourceDisplay.resourceType === "Lambda" && (
                            <>
                                <TextContent>Lambda Function</TextContent>
                                <Input value={resourceDisplay.resourceId || ""} disabled />
                            </>
                        )}
                        {resourceDisplay && resourceDisplay.resourceType === "SQS" && (
                            <>
                                <TextContent>SQS Queue URL</TextContent>
                                <Input value={resourceDisplay.resourceId || ""} disabled />
                            </>
                        )}
                        {resourceDisplay && resourceDisplay.resourceType === "EventBridge" && (
                            <>
                                <TextContent>EventBridge Bus</TextContent>
                                <Input value={resourceDisplay.resourceId || "default"} disabled />
                                <TextContent>EventBridge Source</TextContent>
                                <Input
                                    value={resourceDisplay.eventSource || "vams.pipeline"}
                                    disabled
                                />
                                <TextContent>EventBridge Detail Type</TextContent>
                                <Input
                                    value={resourceDisplay.eventDetailType || pipelineName}
                                    disabled
                                />
                            </>
                        )}
                        <TextContent>Description</TextContent>
                        <Textarea
                            placeholder="Description"
                            name="pipelineDescription"
                            rows={4}
                            value={pipelineDescription}
                            onChange={({ detail }) => setPipelineDescription(detail.value)}
                        />
                        <TextContent>{Synonyms.Asset} Type</TextContent>
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
            <div style={{ paddingBottom: "20px" }} />
        </>
    );
}

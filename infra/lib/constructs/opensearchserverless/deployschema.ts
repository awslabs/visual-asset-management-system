/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { Handler, Context, Callback } from "aws-lambda";
import * as aws4 from "aws4";
import {
    BatchGetCollectionCommand,
    OpenSearchServerlessClient,
} from "@aws-sdk/client-opensearchserverless";
import { Client, Connection } from "@opensearch-project/opensearch";
import { AwsSigv4Signer } from "@opensearch-project/opensearch/aws";
import { defaultProvider } from "@aws-sdk/credential-provider-node";

import { SSMClient, PutParameterCommand } from "@aws-sdk/client-ssm"; // ES Modules import

const setIndexNameSSM = async (stackName: string, indexName: string) => {
    const client = new SSMClient({});
    const command = new PutParameterCommand({
        // PutParameterRequest
        Name: "/" + [stackName, "indexName"].join("/"),
        Description: "The indexName in OpenSearch Service for " + stackName,
        Value: indexName, // required
        Type: "String",
        Overwrite: true,
    });
    const response = await client.send(command);
    console.log("indexName SSM response", response);
};

const setCollectionEndpointSSM = async (
    stackName: string,
    collectionName: string,
    value: string | undefined
) => {
    if (!value) {
        return;
    }
    const client = new SSMClient({});
    const command = new PutParameterCommand({
        // PutParameterRequest
        Name: "/" + [stackName, collectionName, "endpoint"].join("/"),
        Description: "The endpoint of the OpenSearch Service in " + stackName,
        Value: value, // required
        Type: "String",
        Overwrite: true,
    });
    const response = await client.send(command);
    console.log("opensearch endpoint SSM response", response);
};

export const handler: Handler = async function (event: any) {
    console.log("the event", event);

    if (event.RequestType === "Delete") {
        return {
            StackId: event.StackId,
            RequestId: event.RequestId,
            LogicalResourceId: event.LogicalResourceId,
            PhysicalResourceId: event.PhysicalResourceId,
            Status: "SUCCESS",
            Reason: "Delete request not supported",
        };
    }

    const collectionName = event?.ResourceProperties?.collectionName;
    console.log("Collection Name", collectionName);

    const cmd = new BatchGetCollectionCommand({
        names: [collectionName],
    });

    const aossClient = new OpenSearchServerlessClient({});
    const response = await aossClient.send(cmd);

    console.log("the response", response);

    if (response.collectionDetails?.length !== 1) {
        console.log("Collection not found or more than one collection with this name", response);
        return {
            StackId: event.StackId,
            RequestId: event.RequestId,
            LogicalResourceId: event.LogicalResourceId,
            PhysicalResourceId: event.PhysicalResourceId,
            Status: "FAILED",
            Reason: "Expected 1 but found multiple collections with the name " + collectionName,
        };
    }

    const client = new Client({
        ...AwsSigv4Signer({
            region: process.env["AWS_REGION"] ?? "us-east-1",
            service: "aoss",
            getCredentials: () => {
                const credentialsProvider = defaultProvider();
                return credentialsProvider();
            },
        }),
        node: response.collectionDetails![0].collectionEndpoint,
    });

    setCollectionEndpointSSM(
        event?.ResourceProperties?.stackName,
        collectionName,
        response.collectionDetails![0].collectionEndpoint
    );
    setIndexNameSSM(event?.ResourceProperties?.stackName, event?.ResourceProperties?.indexName);

    const exists_resp = await client.indices.exists({
        index: event?.ResourceProperties?.indexName,
    });
    console.log("exists_resp", exists_resp);
    if (exists_resp.body) {
        return {
            StackId: event.StackId,
            RequestId: event.RequestId,
            LogicalResourceId: event.LogicalResourceId,
            PhysicalResourceId: event.PhysicalResourceId,
            Status: "SUCCESS",
            Reason: "Index already exists",
        };
    }

    const index_resp = await client.indices.create({
        index: event?.ResourceProperties?.indexName,
        body: {
            mappings: {
                dynamic_templates: [
                    {
                        strings_as_text_and_keyword: {
                            match_mapping_type: "string",
                            match: "str_*",
                            mapping: {
                                type: "text",
                                fields: {
                                    raw: {
                                        type: "keyword",
                                    },
                                },
                            },
                        },
                    },
                    {
                        numeric: {
                            match: "num_*",
                            mapping: {
                                type: "long",
                            },
                        },
                    },
                    {
                        boolean: {
                            match: "bool_*",
                            mapping: {
                                type: "boolean",
                            },
                        },
                    },
                    {
                        location_as_geo_point: {
                            match: "gp_*",
                            mapping: {
                                type: "geo_point",
                            },
                        },
                    },
                    {
                        location_as_geo_collection: {
                            match: "gs_*",
                            mapping: {
                                type: "geo_shape",
                            },
                        },
                    },
                    {
                        date_fields: {
                            match: "dt_*",
                            mapping: {
                                type: "date",
                                format: "yyyy-MM-dd||epoch_millis",
                            },
                        },
                    },
                ],
            },
        },
    });

    console.log("index_resp", index_resp);

    return {
        StackId: event.StackId,
        RequestId: event.RequestId,
        LogicalResourceId: event.LogicalResourceId,
        PhysicalResourceId: event.PhysicalResourceId,
        Status: "SUCCESS",
        Reason: "Index created successfully",
    };
};

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { Handler } from "aws-lambda";
import { Client } from "@opensearch-project/opensearch";
import { AwsSigv4Signer } from "@opensearch-project/opensearch/aws";
import { defaultProvider } from "@aws-sdk/credential-provider-node";

import { SSMClient, PutParameterCommand } from "@aws-sdk/client-ssm"; // ES Modules import

const setIndexNameSSM = async (paramName: string, indexName: string) => {
    const client = new SSMClient({});
    const command = new PutParameterCommand({
        // PutParameterRequest
        Name: paramName,
        Description: "The indexName in OpenSearch Service for " + paramName,
        Value: indexName, // required
        Type: "String",
        Overwrite: true,
    });
    const response = await client.send(command);
    console.log("indexName SSM response", response);
};

const setDomainEndpointSSM = async (paramName: string, value: string | undefined) => {
    if (!value) {
        return;
    }
    const client = new SSMClient({});
    const command = new PutParameterCommand({
        // PutParameterRequest
        Name: paramName,
        Description: "The endpoint of the OpenSearch Service in " + paramName,
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

    const domainEndpoint = event?.ResourceProperties?.domainEndpoint;
    const indexName = event?.ResourceProperties?.indexName;

    const endpointSSMParam = event?.ResourceProperties?.endpointSSMParam;
    const indexNameSSMParam = event?.ResourceProperties?.indexNameSSMParam;
    console.log("EndpointSSMParam", endpointSSMParam);
    console.log("IndexNameSSMParam", indexNameSSMParam);

    setDomainEndpointSSM(endpointSSMParam, domainEndpoint);

    setIndexNameSSM(indexNameSSMParam, indexName);

    const client = new Client({
        ...AwsSigv4Signer({
            region: process.env["AWS_REGION"] ?? "us-east-1",
            service: "es",
            getCredentials: () => {
                const credentialsProvider = defaultProvider();
                return credentialsProvider();
            },
        }),
        node: domainEndpoint,
    });

    console.log("established opensearch client connection");

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

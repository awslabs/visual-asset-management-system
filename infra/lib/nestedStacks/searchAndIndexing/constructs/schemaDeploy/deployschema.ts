/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

const setEndpointSSM = async (paramName: string, value: string | undefined) => {
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
    console.log("endpoint SSM response", response);
};

/**
 * Get the OpenSearch index mapping schema for dual-index system with flat object fields
 * This schema uses flat_object type for metadata (MD_) and attributes (AB_) to prevent field explosion
 */
const getDualIndexMappingSchema = (indexType: "asset" | "file") => {
    const baseMapping = {
        mappings: {
            dynamic_templates: [
                {
                    core_strings: {
                        match_mapping_type: "string",
                        match: "str_*",
                        mapping: {
                            type: "text",
                            fields: {
                                keyword: {
                                    type: "keyword",
                                },
                            },
                        },
                    },
                },
                {
                    core_numeric: {
                        match: "num_*",
                        mapping: {
                            type: "long",
                        },
                    },
                },
                {
                    core_boolean: {
                        match: "bool_*",
                        mapping: {
                            type: "boolean",
                        },
                    },
                },
                {
                    core_dates: {
                        match: "date_*",
                        mapping: {
                            type: "date",
                            format: "yyyy-MM-dd'T'HH:mm:ss||yyyy-MM-dd||epoch_millis",
                        },
                    },
                },
                {
                    core_lists: {
                        match: "list_*",
                        mapping: {
                            type: "text",
                            fields: {
                                keyword: {
                                    type: "keyword",
                                },
                            },
                        },
                    },
                },
            ],
            properties: {
                _rectype: {
                    type: "keyword",
                },
                // Flat object for all metadata fields - prevents field explosion
                MD_: {
                    type: "flat_object",
                },
            } as any,
        },
        settings: {
            number_of_shards: 1,
            number_of_replicas: 0,
            analysis: {
                analyzer: {
                    default: {
                        type: "standard",
                    },
                },
            },
        },
    };

    // Add index-specific properties - using multi-field mapping for maximum flexibility
    if (indexType === "asset") {
        (baseMapping.mappings.properties as any) = {
            ...baseMapping.mappings.properties,
            str_databaseid: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_assetid: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_assetname: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_assettype: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_description: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_bucketid: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_bucketname: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_bucketprefix: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_asset_version_id: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_asset_version_comment: { type: "text", fields: { keyword: { type: "keyword" } } },
            bool_isdistributable: { type: "boolean" },
            list_tags: { type: "text", fields: { keyword: { type: "keyword" } } },
            date_asset_version_createdate: { type: "date" },
            bool_has_asset_children: { type: "boolean" },
            bool_has_asset_parents: { type: "boolean" },
            bool_has_assets_related: { type: "boolean" },
            bool_archived: { type: "boolean" },
        };
    } else if (indexType === "file") {
        (baseMapping.mappings.properties as any) = {
            ...baseMapping.mappings.properties,
            str_key: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_databaseid: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_assetid: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_assetname: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_bucketid: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_bucketname: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_bucketprefix: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_fileext: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_etag: { type: "text", fields: { keyword: { type: "keyword" } } },
            str_s3_version_id: { type: "text", fields: { keyword: { type: "keyword" } } },
            date_lastmodified: { type: "date" },
            num_filesize: { type: "long" },
            bool_archived: { type: "boolean" },
            list_tags: { type: "text", fields: { keyword: { type: "keyword" } } },
            // Flat object for all attribute fields (file index only) - prevents field explosion
            AB_: { type: "flat_object" },
        };
    }

    return baseMapping;
};

export const handler: Handler = async function (event: any) {
    console.log("the event ", event);

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

    // Extract properties for dual index deployment
    const assetIndexName = event?.ResourceProperties?.assetIndexName;
    const fileIndexName = event?.ResourceProperties?.fileIndexName;
    const endpointSSMParam = event?.ResourceProperties?.endpointSSMParam;
    const assetIndexNameSSMParam = event?.ResourceProperties?.assetIndexNameSSMParam;
    const fileIndexNameSSMParam = event?.ResourceProperties?.fileIndexNameSSMParam;

    // Determine deployment type and extract endpoint
    const isServerless = event?.ResourceProperties?.collectionEndpoint !== undefined;
    const isProvisioned = event?.ResourceProperties?.domainEndpoint !== undefined;

    let endpoint: string | undefined;
    let service: "aoss" | "es";

    if (isServerless) {
        endpoint = event?.ResourceProperties?.collectionEndpoint;
        service = "aoss";
        console.log("Detected serverless deployment");
        console.log("Collection Name", event?.ResourceProperties?.collectionName);
        console.log("Collection Endpoint", endpoint);
    } else if (isProvisioned) {
        endpoint = event?.ResourceProperties?.domainEndpoint;
        service = "es";
        console.log("Detected provisioned deployment");
        console.log("Domain Endpoint", endpoint);
    } else {
        throw new Error("Neither collectionEndpoint nor domainEndpoint provided");
    }

    console.log("Asset Index Name", assetIndexName);
    console.log("File Index Name", fileIndexName);
    console.log("EndpointSSMParam", endpointSSMParam);

    // Set SSM parameters
    setEndpointSSM(endpointSSMParam, endpoint);

    // Set index name parameters
    if (assetIndexNameSSMParam && assetIndexName) {
        setIndexNameSSM(assetIndexNameSSMParam, assetIndexName);
    }
    if (fileIndexNameSSMParam && fileIndexName) {
        setIndexNameSSM(fileIndexNameSSMParam, fileIndexName);
    }

    // Initialize OpenSearch client with appropriate service type
    const client = new Client({
        ...AwsSigv4Signer({
            region: process.env["AWS_REGION"] ?? "us-east-1",
            service: service,
            getCredentials: () => {
                const credentialsProvider = defaultProvider();
                return credentialsProvider();
            },
        }),
        node: endpoint,
    });

    console.log("established opensearch client connection");

    const results: any[] = [];

    // Create dual indexes
    const indexesToCreate = [];

    if (assetIndexName) {
        indexesToCreate.push({ name: assetIndexName, type: "asset" });
    }
    if (fileIndexName) {
        indexesToCreate.push({ name: fileIndexName, type: "file" });
    }

    for (const indexInfo of indexesToCreate) {
        try {
            // Check if index already exists
            const exists_resp = await client.indices.exists({
                index: indexInfo.name,
            });
            console.log(`${indexInfo.name} exists_resp`, exists_resp);

            if (exists_resp.body) {
                console.log(`Index ${indexInfo.name} already exists, skipping creation`);
                results.push({
                    index: indexInfo.name,
                    type: indexInfo.type,
                    status: "EXISTS",
                    message: "Index already exists",
                });
                continue;
            }

            // Create index with appropriate schema
            const indexSchema = getDualIndexMappingSchema(indexInfo.type as "asset" | "file");

            console.log(
                `Creating ${indexInfo.type} index ${indexInfo.name} with schema:`,
                JSON.stringify(indexSchema, null, 2)
            );

            const index_resp = await client.indices.create({
                index: indexInfo.name,
                body: indexSchema as any,
            });

            console.log(`${indexInfo.name} index_resp`, index_resp);

            results.push({
                index: indexInfo.name,
                type: indexInfo.type,
                status: "CREATED",
                message: "Index created successfully",
            });
        } catch (error) {
            console.error(`Error creating index ${indexInfo.name}:`, error);
            results.push({
                index: indexInfo.name,
                type: indexInfo.type,
                status: "ERROR",
                message: `Error creating index: ${error}`,
            });
        }
    }

    return {
        StackId: event.StackId,
        RequestId: event.RequestId,
        LogicalResourceId: event.LogicalResourceId,
        PhysicalResourceId: event.PhysicalResourceId,
        Status: "SUCCESS",
        Reason: `Processed ${results.length} indexes`,
        Data: {
            Results: results,
        },
    };
};

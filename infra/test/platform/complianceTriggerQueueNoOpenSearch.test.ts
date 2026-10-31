/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Compliance does not depend on search: in a deployment with neither OpenSearch flavour the
 * compliance trigger still has its event source (the trigger queue, subscribed to both indexer
 * topics), and the stream relays that publish the asset, asset-file-metadata, asset-link,
 * asset-link-metadata and file-attribute table records to those topics are still built. Without
 * them a NOOPENSEARCH deployment would bind schemas that never re-evaluate on a change.
 *
 * Also pins the stream view of the tables whose REMOVE records the trigger maps to assets.
 *
 * Full-app synth (~1 min, several GB): run this suite on its own.
 */

import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

jest.setTimeout(900_000);

const TRIGGER_HANDLER = "handlers.compliance.complianceTrigger.lambda_handler";
const RELAY_HANDLER = "handlers.indexing.snsQueuing.lambda_handler";

const noOpenSearch = (): SynthResult =>
    synthTemplate("commercial", {
        mutate: (c) => {
            c.app.openSearch.useServerless.enabled = false;
            c.app.openSearch.useProvisioned.enabled = false;
        },
        mutateKey: "no-opensearch",
    });

const inStack = (r: Resource, stack: string) => r.stack === stack;

const getAtt = (value: any): string | undefined =>
    value && Array.isArray(value["Fn::GetAtt"]) ? String(value["Fn::GetAtt"][0]) : undefined;

describe("compliance trigger without OpenSearch", () => {
    let synth: SynthResult;
    beforeAll(() => {
        synth = noOpenSearch();
    });

    test("the configuration really builds no OpenSearch", () => {
        // The control: an OpenSearch resource here would mean the mutation did not apply and every
        // assertion below proves nothing about a NOOPENSEARCH deployment.
        expect(synth.countOfType("AWS::OpenSearchServerless::Collection")).toBe(0);
        expect(synth.countOfType("AWS::OpenSearchService::Domain")).toBe(0);
        expect(synth.countOfType("AWS::Lambda::Function")).toBeGreaterThan(0);
    });

    // The trigger's queue, as the storage stack builds it (the source queue, not its DLQ).
    const triggerQueue = () => {
        const queues = synth.where(
            "AWS::SQS::Queue",
            (r) => r.logicalId.startsWith("ComplianceTriggerQueue") && !!r.properties.RedrivePolicy
        );
        expect(queues).toHaveLength(1);
        return queues[0];
    };

    test("the trigger has an SQS event source on its own queue", () => {
        const trigger = synth.where(
            "AWS::Lambda::Function",
            (r) => r.properties.Handler === TRIGGER_HANDLER
        );
        expect(trigger).toHaveLength(1);
        const mappings = synth.where(
            "AWS::Lambda::EventSourceMapping",
            (r) =>
                inStack(r, trigger[0].stack) &&
                r.properties.FunctionName?.Ref === trigger[0].logicalId
        );
        expect(mappings).toHaveLength(1);
        const queue = triggerQueue();
        // The queue is in the storage stack, so the mapping reads its ARN through the nested-stack
        // parameter CDK names after it.
        const source = mappings[0].properties.EventSourceArn;
        expect(typeof source?.Ref).toBe("string");
        expect(source.Ref).toMatch(
            /StorageResourcesBuilder.*ComplianceTriggerQueue[0-9A-F]{8}Arn$/
        );
        expect(mappings[0].properties.FunctionResponseTypes).toEqual(["ReportBatchItemFailures"]);

        // Both indexer topics deliver into that queue.
        const subscriptions = synth.where(
            "AWS::SNS::Subscription",
            (r) => inStack(r, queue.stack) && getAtt(r.properties.Endpoint) === queue.logicalId
        );
        expect(subscriptions).toHaveLength(2);
        const topics = subscriptions.map((s) => JSON.stringify(s.properties.TopicArn)).sort();
        expect(topics[0]).not.toEqual(topics[1]);
        expect(topics.some((t) => /AssetIndexerSnsTopic/i.test(t))).toBe(true);
        expect(topics.some((t) => /FileIndexerSnsTopic/i.test(t))).toBe(true);
    });

    test("the queue and its subscriptions are in place before the request Lambdas change", () => {
        // An upgrade from a build whose metadata and link Lambdas recorded each change themselves
        // updates those Lambdas (ApiBuilder) before the trigger starts reading its queue
        // (ApiBuilder2). The queue and its two subscriptions are built in the storage stack, which
        // both API stacks depend on, so CloudFormation creates them first and the messages of
        // changes made while the API stacks update wait in the queue.
        const queue = triggerQueue();
        const topics = synth.where("AWS::SNS::Topic", (r) =>
            /AssetIndexerSnsTopic/.test(r.logicalId)
        );
        expect(topics).toHaveLength(1);
        expect(queue.stack).toBe(topics[0].stack);
        const subscriptions = synth.where(
            "AWS::SNS::Subscription",
            (r) => getAtt(r.properties.Endpoint) === queue.logicalId
        );
        expect(subscriptions.map((s) => s.stack)).toEqual([queue.stack, queue.stack]);

        const nestedStacks = synth.where("AWS::CloudFormation::Stack", () => true);
        const nestedStack = (construct: string) => {
            const found = nestedStacks.filter((r) =>
                new RegExp(`^${construct}NestedStack${construct}NestedStackResource`).test(
                    r.logicalId
                )
            );
            expect(found).toHaveLength(1);
            return found[0];
        };
        const storage = nestedStack("StorageResourcesBuilder");
        const dependsOn = (r: Resource) => [r.raw.DependsOn ?? []].flat();
        for (const construct of ["ApiBuilder", "ApiBuilder2"]) {
            expect({ construct, dependsOn: dependsOn(nestedStack(construct)) }).toEqual({
                construct,
                dependsOn: expect.arrayContaining([storage.logicalId]),
            });
        }
        // The control: the request Lambdas whose stamp moved to the trigger are in ApiBuilder, not
        // in the storage stack.
        const metadataService = synth.where(
            "AWS::Lambda::Function",
            (r) => r.properties.Handler === "handlers.metadata.metadataService.lambda_handler"
        );
        expect(metadataService).toHaveLength(1);
        expect(metadataService[0].stack).not.toBe(queue.stack);
    });

    test("the tables whose REMOVE the trigger maps to assets stream the old image", () => {
        // A REMOVE record carries only the keys unless the stream holds the old image, and the keys of
        // an asset-link row do not name its assets; for a metadata row the old image also says whether
        // a workflow execution deleted it.
        const viewType = (prefix: string) => {
            const tables = synth.where("AWS::DynamoDB::Table", (r) =>
                r.logicalId.startsWith(prefix)
            );
            expect(tables).toHaveLength(1);
            return tables[0].properties.StreamSpecification?.StreamViewType;
        };
        for (const prefix of [
            "AssetLinksStorageTableV22",
            "AssetLinksMetadataStorageTable",
            "AssetFileMetadataStorageTableV2",
            "FileAttributeStorageTableV2",
        ]) {
            expect(viewType(prefix)).toBe("NEW_AND_OLD_IMAGES");
        }
        // The control: a table the change does not touch keeps its new-image stream.
        expect(viewType("AssetStorageTable")).toBe("NEW_IMAGE");
    });

    test("the stream relays that feed both topics are built", () => {
        const relays = synth.where(
            "AWS::Lambda::Function",
            (r) => r.properties.Handler === RELAY_HANDLER
        );
        const topicOf = (r: Resource) =>
            JSON.stringify(r.properties.Environment?.Variables?.SNS_TOPIC_ARN ?? "");
        const assetRelay = relays.filter((r) => /AssetIndexerSnsTopic/i.test(topicOf(r)));
        const fileRelay = relays.filter((r) => /FileIndexerSnsTopic/i.test(topicOf(r)));
        expect(assetRelay).toHaveLength(1);
        expect(fileRelay).toHaveLength(1);

        const streamSources = (relay: Resource) =>
            synth.where(
                "AWS::Lambda::EventSourceMapping",
                (r) => inStack(r, relay.stack) && r.properties.FunctionName?.Ref === relay.logicalId
            );
        // Asset, asset-file-metadata, asset-link and asset-link-metadata tables.
        expect(streamSources(assetRelay[0])).toHaveLength(4);
        // Asset-file-metadata and file-attribute tables.
        expect(streamSources(fileRelay[0])).toHaveLength(2);
    });
});

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
        const queueId = getAtt(mappings[0].properties.EventSourceArn);
        const queue = synth.where(
            "AWS::SQS::Queue",
            (r) => inStack(r, trigger[0].stack) && r.logicalId === queueId
        );
        expect(queue).toHaveLength(1);
        expect(queue[0].logicalId.startsWith("ComplianceTriggerQueue")).toBe(true);
        expect(mappings[0].properties.FunctionResponseTypes).toEqual(["ReportBatchItemFailures"]);

        // Both indexer topics deliver into that queue.
        const subscriptions = synth.where(
            "AWS::SNS::Subscription",
            (r) => inStack(r, queue[0].stack) && getAtt(r.properties.Endpoint) === queueId
        );
        expect(subscriptions).toHaveLength(2);
        const topics = subscriptions.map((s) => JSON.stringify(s.properties.TopicArn)).sort();
        expect(topics[0]).not.toEqual(topics[1]);
        expect(topics.some((t) => /AssetIndexerSnsTopic/i.test(t))).toBe(true);
        expect(topics.some((t) => /FileIndexerSnsTopic/i.test(t))).toBe(true);
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

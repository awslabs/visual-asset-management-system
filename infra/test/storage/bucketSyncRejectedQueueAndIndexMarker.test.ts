/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Bucket sync's rejected-record queue and the deploy-time readiness marker of the asset location index.
 *
 * The queue holds S3 notifications that bucket sync cannot attribute by rule (two live assets hold the
 * key's location). It is encrypted like the bucket-sync dead-letter queues, keeps messages 14 days,
 * refuses non-TLS access, raises an alarm on visible messages, and only the created-events functions
 * may send to it.
 *
 * The marker custom resource writes one SSM parameter on a new deployment. Its role may describe and
 * scan the asset table and put that one parameter, with partition-aware ARNs.
 */

import {
    Resource,
    SynthResult,
    TemplateName,
    actionsOf,
    handlerOf,
    resourcesOf,
    statementsOf,
    synthTemplate,
} from "../support/templateSynth";

const CREATED_HANDLER = "handlers.indexing.sqsBucketSync.lambda_handler_created";
const DELETED_HANDLER = "handlers.indexing.sqsBucketSync.lambda_handler_deleted";
const MARKER_HANDLER = "handlers.indexing.crAssetLocationIndexMarker.lambda_handler";
const PARTITION: Record<TemplateName, string> = {
    commercial: "aws",
    govcloud: "aws-us-gov",
    eusovereign: "aws-eusc",
};

function rejectedQueue(s: SynthResult): Resource {
    const queues = s.where("AWS::SQS::Queue", (r) =>
        r.logicalId.startsWith("bucketSyncRejectedQueue")
    );
    expect(queues).toHaveLength(1);
    return queues[0];
}

const refersTo = (value: any, logicalId: string): boolean =>
    JSON.stringify(value).includes(logicalId);

describe.each([
    ["CMK", true],
    ["SQS-managed", false],
])("bucket-sync rejected-record queue (%s encryption)", (_label, cmk) => {
    let synth: SynthResult;
    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: (c) => {
                c.app.useKmsCmkEncryption.enabled = cmk;
            },
            mutateKey: `rejectedQueue-cmk-${cmk}`,
        });
    });

    test("is encrypted with the deployment's key setup and keeps messages 14 days", () => {
        const queue = rejectedQueue(synth);
        expect(queue.properties.MessageRetentionPeriod).toBe(14 * 24 * 3600);
        if (cmk) {
            expect(queue.properties.KmsMasterKeyId).toBeDefined();
        } else {
            expect(queue.properties.SqsManagedSseEnabled).toBe(true);
            expect(queue.properties.KmsMasterKeyId).toBeUndefined();
        }
    });

    test("refuses access without TLS", () => {
        const queue = rejectedQueue(synth);
        const policies = synth.where("AWS::SQS::QueuePolicy", (r) =>
            refersTo(r.properties.Queues, queue.logicalId)
        );
        const statements = policies.flatMap((p) => p.properties.PolicyDocument.Statement as any[]);
        expect(
            statements.some(
                (st) =>
                    st.Effect === "Deny" && st.Condition?.Bool?.["aws:SecureTransport"] === "false"
            )
        ).toBe(true);
    });

    test("raises an alarm on visible messages", () => {
        const queue = rejectedQueue(synth);
        const alarms = synth.where(
            "AWS::CloudWatch::Alarm",
            (r) =>
                /-bucketSyncRejected$/.test(SynthResult.flatten(r.properties.AlarmName)) &&
                refersTo(r.properties.Dimensions, queue.logicalId)
        );
        expect(alarms).toHaveLength(1);
        expect(alarms[0].properties.MetricName).toBe("ApproximateNumberOfMessagesVisible");
        expect(alarms[0].properties.Threshold).toBe(0);
    });

    test("only the created-events bucket-sync functions may send to it, and they know its URL", () => {
        const queue = rejectedQueue(synth);
        const senders = synth
            .ofType("AWS::Lambda::Function")
            .filter((fn) =>
                statementsOf(synth, fn).some(
                    (st) =>
                        actionsOf(st).includes("sqs:SendMessage") &&
                        refersTo(st.Resource, queue.logicalId)
                )
            );
        const created = synth.where(
            "AWS::Lambda::Function",
            (r) => handlerOf(r) === CREATED_HANDLER
        );
        expect(created.length).toBeGreaterThan(0);
        expect(senders.map(handlerOf).sort()).toEqual(created.map(handlerOf).sort());
        for (const fn of created) {
            expect(
                refersTo(
                    fn.properties.Environment.Variables.BUCKET_SYNC_REJECTED_QUEUE_URL,
                    queue.logicalId
                )
            ).toBe(true);
        }
        for (const fn of synth.where(
            "AWS::Lambda::Function",
            (r) => handlerOf(r) === DELETED_HANDLER
        )) {
            expect(
                fn.properties.Environment.Variables.BUCKET_SYNC_REJECTED_QUEUE_URL
            ).toBeUndefined();
        }
    });
});

describe.each(["commercial", "govcloud", "eusovereign"] as TemplateName[])(
    "asset location index readiness marker (%s)",
    (templateName) => {
        let synth: SynthResult;
        beforeAll(() => {
            synth = synthTemplate(templateName);
        });

        test("a custom resource passes the asset table and the marker parameter", () => {
            const resources = synth.where(
                "AWS::CloudFormation::CustomResource",
                (r) =>
                    r.logicalId.startsWith("AssetLocationIndexMarker") &&
                    "markerParameterName" in r.properties
            );
            expect(resources).toHaveLength(1);
            const props = resources[0].properties;
            expect(SynthResult.flatten(props.markerParameterName)).toMatch(
                /^\/.+\/resourceNames\/dataMigrations\/assetLocationIndex$/
            );
            expect(refersTo(props.assetTableName, "AssetStorageTable")).toBe(true);
            expect(props.newTableWindowSeconds).toBe("3600");
        });

        test("its role describes and scans the asset table and puts only the marker parameter", () => {
            const markers = synth.where(
                "AWS::Lambda::Function",
                (r) => handlerOf(r) === MARKER_HANDLER
            );
            expect(markers).toHaveLength(1);
            const statements = statementsOf(synth, markers[0]);

            // The shared security helpers add the authorization-table reads every VAMS function holds;
            // the asset table is reached by this one statement only.
            const tableReads = statements.filter((st) =>
                refersTo(st.Resource, "AssetStorageTable")
            );
            expect(tableReads).toHaveLength(1);
            expect(actionsOf(tableReads[0]).sort()).toEqual([
                "dynamodb:DescribeTable",
                "dynamodb:Scan",
            ]);
            expect(resourcesOf(tableReads[0])).toHaveLength(1);
            expect(resourcesOf(tableReads[0])[0]).toContain("AssetStorageTable");

            const puts = statements.filter((st) => actionsOf(st).includes("ssm:PutParameter"));
            expect(puts).toHaveLength(1);
            expect(actionsOf(puts[0])).toEqual(["ssm:PutParameter"]);
            const [parameterArn] = resourcesOf(puts[0]);
            expect(resourcesOf(puts[0])).toHaveLength(1);
            expect(parameterArn.startsWith(`arn:${PARTITION[templateName]}:ssm:`)).toBe(true);
            expect(parameterArn).toMatch(
                /:parameter\/.+\/resourceNames\/dataMigrations\/assetLocationIndex$/
            );
            expect(
                statements.some((st) => actionsOf(st).some((a) => a === "ssm:*" || a === "*"))
            ).toBe(false);
        });
    }
);

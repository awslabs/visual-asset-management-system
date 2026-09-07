/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * No SQS queue may authorize `sns.amazonaws.com` to send without a source condition.
 *
 * The queue policy is the ONLY gate on these queues. `sqsBucketSync.py` and the indexer handlers treat
 * whatever arrives as a genuine Amazon S3 or indexer event and resolve or create assets, write file
 * version history, and update metadata from it — no authentication tier is involved. An unconditioned
 * `Allow sqs:SendMessage` to the SNS service principal therefore lets any SNS topic in any AWS account
 * inject a fabricated event, and the queue ARN is fully derivable from the account id plus the
 * deployment's configuration and stack names.
 *
 * The defect came from `queue.grantSendMessages(Service("SNS").Principal)`, which CDK renders as an
 * unconditioned resource-policy statement. It was redundant as well as dangerous: `SqsSubscription.bind()`
 * adds the statement delivery actually needs, scoped with `ArnEquals aws:SourceArn` to the subscribing
 * topic. Both halves were confirmed on the live deployment before removal — the scoped statement was
 * present alongside the unconditioned one, and the KMS key policy grants `sns.amazonaws.com`
 * Decrypt/GenerateDataKey independently, so an encrypted queue's delivery never depended on the grant.
 *
 * This asserts across every emitted template rather than per construct, because the same one-line call
 * appeared in four different nested stacks and the next one would too.
 */

import { SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

/** Templates that between them enable the indexer and bucket-sync queues. */
const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];

/**
 * The Physna add-on's two sync queues, which the shipped templates do NOT emit.
 *
 * `usePhysnaSync.enabled` is false in all three, so an unmutated synth contains 10 queue policies and
 * zero Physna ones — measured. The suite below is therefore blind to the add-on unless the feature is
 * turned on, which is worth stating because two of the four `grantSendMessages(Service("SNS").Principal)`
 * call sites were the Physna queues: an assertion sweeping "every queue policy" reads like whole-repo
 * coverage while never loading the stack the finding was filed against.
 *
 * Values satisfy `getConfig()`'s Physna block (UUID tenantId, https endpoints with the required trailing
 * slash on the base, authType cognito, inline credentials) — they are not real and reach no network at
 * synth.
 */
function enablePhysna(c: any) {
    c.app.addons.usePhysnaSync.enabled = true;
    c.app.addons.usePhysnaSync.tenantId = "00000000-0000-4000-8000-000000000000";
    c.app.addons.usePhysnaSync.apiBaseEndpoint = "https://app-api.physna.com/v3/";
    c.app.addons.usePhysnaSync.authTokenEndpoint =
        "https://physna-app.auth.us-east-2.amazoncognito.com/oauth2/token";
    c.app.addons.usePhysnaSync.authType = "cognito";
    c.app.addons.usePhysnaSync.clientId = "synth-only-client-id";
    c.app.addons.usePhysnaSync.clientSecret = "synth-only-client-secret";
    c.app.addons.usePhysnaSync.credentialsSecretArn = "";
}

interface Statement {
    Effect?: string;
    Principal?: any;
    Action?: any;
    Condition?: any;
}

function asArray(value: any): any[] {
    if (value === undefined) return [];
    return Array.isArray(value) ? value : [value];
}

/** True when the statement's principal is the SNS service. */
function isSnsPrincipal(statement: Statement): boolean {
    const service = statement.Principal?.Service;
    return asArray(service).some((s) => String(SynthResult.flatten(s)) === "sns.amazonaws.com");
}

/** True when the statement allows any form of SendMessage. */
function allowsSend(statement: Statement): boolean {
    if (statement.Effect !== "Allow") return false;
    return asArray(statement.Action).some((a) => /^sqs:(SendMessage|\*)$/.test(String(a)));
}

describe.each(TEMPLATES)("%s: SNS-to-SQS queue policies", (templateName) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(templateName);
    });

    test("no queue policy allows sns.amazonaws.com to send without a source condition", () => {
        const offenders: string[] = [];
        for (const policy of synth.ofType("AWS::SQS::QueuePolicy")) {
            for (const statement of asArray(policy.properties.PolicyDocument?.Statement)) {
                if (!isSnsPrincipal(statement) || !allowsSend(statement)) continue;
                const condition = JSON.stringify(statement.Condition ?? {});
                // aws:SourceArn or aws:SourceAccount both bound the sender; either is acceptable.
                if (!/aws:SourceArn|aws:SourceAccount/.test(condition)) {
                    offenders.push(
                        `${policy.stack}/${policy.logicalId}: ${JSON.stringify(
                            statement.Action
                        )} ` + `with condition ${condition}`
                    );
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    test("the queues that receive SNS notifications still have a SCOPED send statement", () => {
        // The positive control, and the load-bearing half. Without it the test above is satisfied just as
        // well by removing SNS delivery altogether, which would silently stop indexing and bucket sync.
        const scoped = [];
        for (const policy of synth.ofType("AWS::SQS::QueuePolicy")) {
            for (const statement of asArray(policy.properties.PolicyDocument?.Statement)) {
                if (!isSnsPrincipal(statement) || !allowsSend(statement)) continue;
                if (
                    /aws:SourceArn|aws:SourceAccount/.test(
                        JSON.stringify(statement.Condition ?? {})
                    )
                ) {
                    scoped.push(`${policy.stack}/${policy.logicalId}`);
                }
            }
        }
        expect(scoped.length).toBeGreaterThan(0);
    });

    test("every SNS subscription to a queue is matched by a policy statement naming its topic", () => {
        // Ties the two halves together: a scoped statement existing somewhere is not the same as EVERY
        // subscribed queue having one. A subscription whose queue policy lacks the statement would leave
        // delivery permanently denied — a silent break, since SNS does not surface a delivery failure to
        // the publisher.
        const subscriptions = synth
            .ofType("AWS::SNS::Subscription")
            .filter((s) => String(s.properties.Protocol) === "sqs");
        expect(subscriptions.length).toBeGreaterThan(0);

        const scopedStatements = synth.ofType("AWS::SQS::QueuePolicy").flatMap((policy) =>
            asArray(policy.properties.PolicyDocument?.Statement)
                .filter((st: Statement) => isSnsPrincipal(st) && allowsSend(st))
                .filter((st: Statement) =>
                    /aws:SourceArn|aws:SourceAccount/.test(JSON.stringify(st.Condition ?? {}))
                )
                .map((st: Statement) => SynthResult.flatten(st.Condition))
        );

        // Each subscription's TopicArn must appear in some scoped statement's condition. Compared on the
        // flattened text because both sides are Fn::Join/Ref structures over the same tokens.
        for (const subscription of subscriptions) {
            const topic = SynthResult.flatten(subscription.properties.TopicArn);
            const matched = scopedStatements.some((condition) => condition.includes(topic));
            expect(matched).toBe(true);
        }
    });
});

describe("Physna add-on sync queues", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enablePhysna,
            mutateKey: "physna-sync-enabled",
        });
    });

    test("the two Physna sync queue policies ARE in this synth", () => {
        // The control. Every assertion below is satisfied by a synth that emitted no Physna queue, and
        // the shipped templates emit none — which is what made the sweep above vacuous for this add-on.
        const physnaPolicies = synth
            .ofType("AWS::SQS::QueuePolicy")
            .filter((p) => /physna/i.test(p.logicalId) || /physna/i.test(p.stack));
        expect(physnaPolicies.length).toBeGreaterThanOrEqual(2);
    });

    test("no Physna queue policy allows sns.amazonaws.com to send without a source condition", () => {
        const offenders: string[] = [];
        for (const policy of synth.ofType("AWS::SQS::QueuePolicy")) {
            if (!/physna/i.test(policy.logicalId) && !/physna/i.test(policy.stack)) continue;
            for (const statement of asArray(policy.properties.PolicyDocument?.Statement)) {
                if (!isSnsPrincipal(statement) || !allowsSend(statement)) continue;
                const condition = JSON.stringify(statement.Condition ?? {});
                if (!/aws:SourceArn|aws:SourceAccount/.test(condition)) {
                    offenders.push(
                        `${policy.stack}/${policy.logicalId}: ${JSON.stringify(
                            statement.Action
                        )} with condition ${condition}`
                    );
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    test("both Physna queues still carry a SCOPED send statement, so sync delivery survives", () => {
        // The other half: the fix removed a grant, so it must not have removed delivery. The Physna
        // handlers act on whatever arrives, and SNS does not report a delivery denial to the publisher,
        // so a queue left with no statement at all fails silently.
        const scoped = new Set<string>();
        for (const policy of synth.ofType("AWS::SQS::QueuePolicy")) {
            if (!/physna/i.test(policy.logicalId) && !/physna/i.test(policy.stack)) continue;
            for (const statement of asArray(policy.properties.PolicyDocument?.Statement)) {
                if (!isSnsPrincipal(statement) || !allowsSend(statement)) continue;
                if (
                    /aws:SourceArn|aws:SourceAccount/.test(
                        JSON.stringify(statement.Condition ?? {})
                    )
                ) {
                    scoped.add(`${policy.stack}/${policy.logicalId}`);
                }
            }
        }
        expect(scoped.size).toBeGreaterThanOrEqual(2);
    });
});

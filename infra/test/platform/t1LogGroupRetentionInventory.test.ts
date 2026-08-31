/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: the CloudWatch log-group inventory each shipped config template emits, and the retention on
 * every one of them.
 *
 * **FIX-093** — the VPC flow-log group declared `RetentionDays.TEN_YEARS` while `LogRetentionAspect`
 * overwrote it with `ONE_YEAR`, so the deployment never had the ten-year period the construct implied.
 * The owner's decision is that one year applies to every log group and overrides any construct-level
 * declaration, so the check that matters is the EMITTED value across all three partitions rather than the
 * declaration.
 *
 * Two things this file adds over asserting "the values are all 365":
 *
 * - **The inventory is pinned per template.** A single-value assertion is satisfied by a template that
 *   emitted one log group as readily as by one that emitted forty, so the count each partition emits is
 *   asserted as a floor and printed. A conditional group that stops being created shows up as a failure
 *   here instead of silently shrinking what the retention assertion covers.
 * - **The flow-log group is asserted by name** in the partitions that create it. It is the subject of the
 *   fix, and it exists only when a VPC is created — the commercial template ships `useGlobalVpc` disabled,
 *   which is why an assertion written against commercial alone would find nothing and pass.
 *
 * 365 is asserted as a literal rather than read back from the aspect's call site. The value is the owner's
 * decision and the documentation states it (`architecture/networking.md`, `architecture/aws-resources.md`,
 * `developer/audit-logging.md`), so a change to the call site must fail here and send the next reader to
 * those pages. `docsSchemaOwnershipAndSubscriptions.test.ts` asserts the call site itself.
 */

import {
    ALL_TEMPLATES,
    RESTRICTED_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    synthTemplate,
} from "../support/templateSynth";

// A full-app synth is ~20 s and this file needs one per shipped template.
jest.setTimeout(600_000);

const synth = (name: TemplateName): SynthResult => synthTemplate(name);

/** The retention every VAMS log group is expected to carry, in days (ONE_YEAR). */
const EXPECTED_DAYS = 365;

/**
 * Floor for the number of log groups each template emits.
 *
 * Pinned from the observed inventory. These are floors rather than exact counts so that adding a log group
 * does not fail the suite, while removing one — which would narrow the retention assertion below without
 * changing its result — does.
 */
const MIN_LOG_GROUPS: Record<TemplateName, number> = {
    // 13: nine audit groups, the orchestration-bus audit group, the shared workflow group, the AWS
    // CloudTrail group, and the REST API access logs. No VPC (so no flow-log group) and no Amazon
    // OpenSearch Service domain groups, because the commercial template ships Serverless.
    commercial: 13,
    // 17: the commercial set plus the VPC flow-log group and the three provisioned OpenSearch domain
    // groups, both of which the restricted templates enable.
    govcloud: 17,
    eusovereign: 17,
};

/** The VPC flow-log group, which is FIX-093's subject. Created only when VAMS builds a VPC. */
const FLOW_LOG_PREFIX = "/aws/vendedlogs/VAMSCloudWatchVPCLogs";

const logGroups = (s: SynthResult) => s.ofType("AWS::Logs::LogGroup");

const nameOf = (g: Resource): string => SynthResult.flatten(g.properties.LogGroupName);

describe("FIX-093: every emitted log group carries the one-year retention", () => {
    test.each(ALL_TEMPLATES)("%s emits its expected log-group inventory", (name) => {
        const groups = logGroups(synth(name));
        // eslint-disable-next-line no-console
        console.log(
            `[T1] ${name}: ${groups.length} log group(s) — ` +
                groups
                    .map((g) => `${nameOf(g) || g.logicalId}=${g.properties.RetentionInDays}`)
                    .join(", ")
        );
        expect(groups.length).toBeGreaterThanOrEqual(MIN_LOG_GROUPS[name]);
    });

    test.each(ALL_TEMPLATES)("%s sets 365 days on every log group", (name) => {
        const groups = logGroups(synth(name));
        // Control: the count assertion above is a separate test, so repeat the non-empty check here —
        // an empty list satisfies `every()` vacuously.
        expect(groups.length).toBeGreaterThan(0);
        const offenders = groups
            .filter((g) => g.properties.RetentionInDays !== EXPECTED_DAYS)
            .map(
                (g) => `${g.stack}/${g.logicalId} (${nameOf(g)}) = ${g.properties.RetentionInDays}`
            );
        expect(offenders).toEqual([]);
    });

    test.each(ALL_TEMPLATES)("%s leaves no group at the CDK default of 731 days", (name) => {
        // A CDK LogGroup with no declaration emits TWO_YEARS (731), not an absent value, so 731 is what a
        // group the aspect missed would look like.
        const stale = logGroups(synth(name)).filter((g) => g.properties.RetentionInDays === 731);
        expect(stale.map((g) => `${g.stack}/${g.logicalId}`)).toEqual([]);
    });

    test.each(RESTRICTED_TEMPLATES)("%s emits the VPC flow-log group at 365 days", (name) => {
        const flow = logGroups(synth(name)).filter((g) => nameOf(g).startsWith(FLOW_LOG_PREFIX));
        // Positive control for the assertion that follows: asserting a property of a resource that was
        // never emitted is the specific way a verify-only check goes vacuous.
        expect(flow.length).toBe(1);
        expect(flow[0].properties.RetentionInDays).toBe(EXPECTED_DAYS);
    });

    test("the commercial template creates no VPC, and so no flow-log group", () => {
        // Explains the restricted-only scope above rather than leaving it looking arbitrary.
        const s = synth("commercial");
        const flow = logGroups(s).filter((g) => nameOf(g).startsWith(FLOW_LOG_PREFIX));
        expect(logGroups(s).length).toBeGreaterThan(0); // control: it emits log groups at all
        expect(s.countOfType("AWS::EC2::VPC")).toBe(0);
        expect(flow).toEqual([]);
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: assertions against the CloudFormation that each shipped config template actually emits.
 *
 * This is the only validation GovCloud and the EU Sovereign Cloud get. No environment exists for either,
 * so a partition defect otherwise ships and surfaces as a `CREATE_FAILED` partway through creating the
 * core stack — a ~30 minute rollback, discovered by a customer.
 *
 * Every negative assertion here is paired with a positive control. Asserting "no Cognito interface
 * endpoint is emitted for GovCloud" is satisfied just as well by a template that emitted nothing, and the
 * control is what distinguishes the two. `expectAbsent()` takes the control as a required argument for
 * that reason.
 *
 * Covers, from docs/review/FINAL-FIX-PLAN.md: the partition-portability class as a whole, plus
 * specifically the log-retention decision (S1-INFRA-057 / S1-INFRA-109) and the Cognito VPC endpoint
 * placeholder decision (S1-INFRA-110, which a customer hit on a mid-release commit).
 */

import * as fsNode from "fs";
import * as pathNode from "path";
import {
    ALL_TEMPLATES,
    RESTRICTED_TEMPLATES,
    SynthResult,
    TemplateName,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";

// A full-app synth is ~20 s per template and three are needed.
jest.setTimeout(600_000);

const cached: Partial<Record<TemplateName, SynthResult>> = {};
const synth = (name: TemplateName): SynthResult => (cached[name] ??= synthTemplate(name));

describe("T1 harness produces a usable assembly", () => {
    test.each(ALL_TEMPLATES)("%s synthesizes a non-trivial assembly", (name) => {
        const s = synth(name);
        // The floor that makes every other assertion in this file meaningful. Without it, an assembly
        // that silently emitted nothing would satisfy all the negatives below.
        expect(Object.keys(s.templates).length).toBeGreaterThan(1);
        expect(s.resources.length).toBeGreaterThan(100);
        expect(s.countOfType("AWS::Lambda::Function")).toBeGreaterThan(20);
        expect(s.countOfType("AWS::DynamoDB::Table")).toBeGreaterThan(20);
    });

    test("the restricted templates resolve to their own partitions", () => {
        expect(synth("govcloud").partition).toBe("aws-us-gov");
        expect(synth("eusovereign").partition).toBe("aws-eusc");
        expect(synth("commercial").partition).toBe("aws");
    });
});

describe("EventSourceMapping Tags are stripped in restricted partitions", () => {
    /**
     * GovCloud and EU Sovereign Lambda reject `Tags` on `AWS::Lambda::EventSourceMapping` outright, and
     * CDK's `addEventSource()` stamps the stack tags onto it. This is the single most expensive partition
     * defect in the codebase: it fails mid-deploy and rolls back the whole core stack. No aspect can fix
     * it, because the L1 is created lazily inside `addEventSource()` after aspects have finished visiting.
     */
    const mappings = (s: SynthResult) => s.ofType("AWS::Lambda::EventSourceMapping");

    test("commercial emits mappings WITH Tags — the control that makes the strip assertion real", () => {
        const found = mappings(synth("commercial"));
        expect(found.length).toBeGreaterThan(0);
        const tagged = found.filter((m) => "Tags" in m.properties);
        // If commercial emitted no tagged mapping, the restricted assertions below would pass whether or
        // not the strip works.
        expect(tagged.length).toBeGreaterThan(0);
    });

    test.each(RESTRICTED_TEMPLATES)("%s emits no mapping carrying Tags", (name) => {
        const s = synth(name);
        const withTags = mappings(s).filter((m) => "Tags" in m.properties);
        expectAbsent(
            `EventSourceMapping with Tags in ${name}`,
            withTags.map((m) => `${m.stack}/${m.logicalId}`),
            {
                description: `${name} emits event source mappings at all`,
                count: mappings(s).length,
            }
        );
    });
});

describe("Cognito interface endpoints are not created in any partition", () => {
    /**
     * Amazon Cognito PrivateLink is unavailable in several partitions and AZ support varies per account,
     * so the block that would create these endpoints is a live conditional with a commented-out body
     * (`S1-INFRA-110`, owner decision: keep as a placeholder). A customer hit the failure on a commit
     * inside the window where the endpoints WERE created. This pins the placeholder so re-enabling it
     * becomes a visible, deliberate decision rather than an accident.
     */
    const cognitoEndpoints = (s: SynthResult) =>
        s.where("AWS::EC2::VPCEndpoint", (r) =>
            /cognito/i.test(SynthResult.flatten(r.properties.ServiceName))
        );

    test.each(ALL_TEMPLATES)("%s emits no cognito-idp/cognito-identity endpoint", (name) => {
        const s = synth(name);
        const all = s.ofType("AWS::EC2::VPCEndpoint");
        if (all.length === 0) {
            // The commercial template ships with useGlobalVpc disabled, so it legitimately has no
            // endpoints. Saying so beats an assertion that passes for the wrong reason.
            expect(name).toBe("commercial");
            return;
        }
        expectAbsent(
            `cognito VPC endpoint in ${name}`,
            cognitoEndpoints(s).map((r) => `${r.stack}/${r.logicalId}`),
            { description: `${name} emits VPC endpoints at all`, count: all.length }
        );
    });
});

describe("CloudWatch log retention matches the documented one year", () => {
    /**
     * `LogRetentionAspect` is the single authority and overwrites whatever a construct declared. The
     * owner reaffirmed ONE_YEAR in round 2, superseding an earlier ten-year answer. Asserting the
     * emitted value in every partition is what stops a construct-level declaration appearing to win.
     */
    const DOCUMENTED_DAYS = 365;

    test.each(ALL_TEMPLATES)("%s sets %i days on every log group", (name) => {
        const s = synth(name);
        const groups = s.ofType("AWS::Logs::LogGroup");
        expect(groups.length).toBeGreaterThan(0);
        const values = Array.from(new Set(groups.map((g) => g.properties.RetentionInDays)));
        expect(values).toEqual([DOCUMENTED_DAYS]);
    });

    test("no log group is left at the CDK default of 731 days", () => {
        // The negative control for the assertion above: a bare CDK LogGroup emits TWO_YEARS (731), not
        // an absent value, so 731 is what a missed group would look like.
        for (const name of ALL_TEMPLATES) {
            const stale = synth(name)
                .ofType("AWS::Logs::LogGroup")
                .filter((g) => g.properties.RetentionInDays === 731);
            expect(stale.map((g) => `${name}:${g.stack}/${g.logicalId}`)).toEqual([]);
        }
    });
});

describe("no hardcoded commercial partition ARNs reach a restricted template", () => {
    /**
     * A literal `arn:aws:` in a GovCloud or EU Sovereign template is a hardcoded partition — the defect
     * class `service-helper.ts` exists to prevent. This is reported rather than asserted at zero on the
     * first run: the count is pinned so it cannot grow silently, and the current value is printed so the
     * remaining sites are visible. Ratcheting a known count downward is honest; asserting zero on a
     * codebase that has some would just be a failing test nobody runs.
     */
    const BASELINE = new Map<TemplateName, number>();

    test.each(RESTRICTED_TEMPLATES)("%s: literal arn:aws: occurrences are pinned", (name) => {
        const s = synth(name);
        const hits = s.grep(/"arn:aws:[a-z0-9-]+:/);
        BASELINE.set(name, hits.length);
        // eslint-disable-next-line no-console
        console.log(
            `[T1] ${name}: ${hits.length} resource(s) contain a literal commercial ARN` +
                (hits.length
                    ? ` — e.g. ${hits
                          .slice(0, 3)
                          .map((h) => h.type)
                          .join(", ")}`
                    : "")
        );
        // Control: the grep machinery works. The commercial template must have many such literals,
        // otherwise a zero here would mean the search is broken rather than the code being clean.
        expect(synth("commercial").grep(/"arn:aws:[a-z0-9-]+:/).length).toBeGreaterThan(0);
        // Ratchet: whatever the count is today, it must not grow. Lower it as sites are fixed.
        expect(hits.length).toBeLessThanOrEqual(400);
    });
});

describe("each shipped config template's ARN placeholders match its own partition", () => {
    /**
     * The reverse of the check above, and a different failure mode. The one above looks for a commercial
     * ARN emitted into a restricted TEMPLATE by construct code. This one looks at the shipped
     * `config.template.*.json` files themselves: an operator copies a placeholder verbatim, so a
     * placeholder written in the wrong partition produces an ARN that does not resolve in the deployment
     * it was copied into.
     *
     * Asserted at zero rather than ratcheted, because these are a handful of hand-written literals in
     * three files rather than construct output.
     */
    // Declared here rather than read from the template: the templates set no `env.partition`, so the
    // expected partition is not derivable from the file being checked.
    const EXPECTED_PARTITION: Record<string, string> = {
        commercial: "aws",
        govcloud: "aws-us-gov",
        eusovereign: "aws-eusc",
    };

    const ARN = /"arn:(aws[a-z-]*):/g;

    test.each(Object.keys(EXPECTED_PARTITION))("%s", (name) => {
        const templatePath = pathNode.join(
            __dirname,
            "..",
            "..",
            "config",
            `config.template.${name}.json`
        );
        const text = fsNode.readFileSync(templatePath, "utf-8") as string;

        const found = [...text.matchAll(ARN)].map((m) => m[1]);
        // Control: the pattern finds the ARNs that are there. A template with no ARN placeholder at all
        // would otherwise pass while asserting nothing.
        expect(found.length).toBeGreaterThan(0);

        const wrong = found.filter((p) => p !== EXPECTED_PARTITION[name]);
        expect(wrong).toEqual([]);
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The web CSP carries the partition's AWS service-host wildcard (`https://*.<suffix>`) in
 * connect-src, img-src, media-src and frame-src, so a presigned URL for a bucket in any Region of the
 * deployment's partition is allowed. This holds for EVERY partition the S3 hostname table defines, which
 * is read from SERVICE_LOOKUP so a new partition is covered automatically.
 *
 * Only the commercial partition has a live proof; the other partitions are verified here by synth-time
 * policy assertions only.
 */

import { SERVICE_LOOKUP } from "../../lib/helper/const";
import { Service, SetConfig, deriveServiceDnsSuffix } from "../../lib/helper/service-helper";
import { generateContentSecurityPolicy } from "../../lib/helper/security";
import { buildTemplateConfig } from "../support/templateSynth";

const REGION: { [partition: string]: string } = {
    aws: "us-east-1",
    "aws-cn": "cn-north-1",
    "aws-us-gov": "us-gov-west-1",
    "aws-iso": "us-iso-east-1",
    "aws-iso-b": "us-isob-east-1",
    "aws-iso-e": "eu-isoe-west-1",
    "aws-iso-f": "us-isof-south-1",
    "aws-eusc": "eusc-de-east-1",
};
// A second Region of the same partition.
const OTHER_REGION: { [partition: string]: string } = {
    aws: "us-west-2",
    "aws-cn": "cn-northwest-1",
    "aws-us-gov": "us-gov-east-1",
    "aws-iso": "us-iso-west-1",
    "aws-iso-b": "us-isob-west-1",
    "aws-iso-e": "eu-isoe-west-2",
    "aws-iso-f": "us-isof-east-1",
    "aws-eusc": "eusc-de-west-1",
};
// The ALB attribute cap applies to every partition but commercial CloudFront (1783).
const CAP = (partition: string) => (partition === "aws" ? 1783 : 1024);
const API_HOST = "abcdefghij.execute-api.us-gov-west-1.amazonaws.com"; // longest plausible, 49 chars
const WILDCARD_DIRECTIVES = ["connect-src", "img-src", "media-src", "frame-src"];

const partitions = Object.keys(SERVICE_LOOKUP["s3"]);

function policy(partition: string, region = REGION[partition]) {
    const config = buildTemplateConfig("govcloud", (c: any) => {
        c.env.region = region;
        // Cognito and Location Service have no hostname in the ISO and China partitions; the
        // wildcard under test does not depend on either.
        c.app.authProvider.useCognito.enabled = false;
        c.app.useLocationService.enabled = false;
    });
    SetConfig(config);
    return generateContentSecurityPolicy({} as any, "", API_HOST, config);
}

function directive(csp: string, name: string): string[] {
    const found = csp
        .split(";")
        .map((d) => d.trim())
        .find((d) => d.startsWith(`${name} `));
    return found ? found.slice(name.length).trim().split(/\s+/) : [];
}

/** CSP host-source grammar: `https://*.sfx` matches any host with one or more labels before `.sfx`. */
function hostSourceMatches(source: string, host: string): boolean {
    const m = /^https:\/\/\*\.(.+)$/.exec(source);
    return !!m && host.endsWith(`.${m[1]}`) && host.length > m[1].length + 1;
}

/** The pre-fix policy's S3 entries: the regional S3 endpoint in each of the four directives. */
function oldS3Bytes(partition: string): number {
    SetConfig(buildTemplateConfig("govcloud", (c: any) => (c.env.region = REGION[partition])));
    return `https://${Service("S3", false).Endpoint}/`.length * WILDCARD_DIRECTIVES.length;
}

describe("CSP AWS service-host wildcard, every partition", () => {
    test("every partition has a Region in this test", () => {
        expect(partitions.sort()).toEqual(Object.keys(REGION).sort());
        expect(partitions.sort()).toEqual(Object.keys(OTHER_REGION).sort());
    });

    test.each(partitions)("%s: emitted policy", (partition) => {
        const info = SERVICE_LOOKUP["s3"][partition];
        const suffix = deriveServiceDnsSuffix(partition, "S3", info.hostname);
        const csp = policy(partition);

        // The four directives carry the wildcard, and no other directive does.
        for (const d of WILDCARD_DIRECTIVES) {
            expect(directive(csp, d)).toContain(`https://*.${suffix}`);
        }
        const carriers = csp
            .split(";")
            .map((d) => d.trim())
            .filter((d) => d.includes("https://*."))
            .map((d) => d.split(" ")[0]);
        expect(carriers).toEqual(["connect-src", "img-src", "media-src", "frame-src"]);

        // No other partition's suffix, no regional S3 host, no FIPS host.
        const wildcardSources = new Set(
            csp.split(/[\s;]+/).filter((t) => t.startsWith("https://*."))
        );
        expect([...wildcardSources]).toEqual([`https://*.${suffix}`]);
        expect(csp).not.toMatch(/https:\/\/s3[.-]/);
        expect(csp).not.toContain(`s3.${REGION[partition]}`);
        expect(csp).not.toMatch(/-fips\./);
    });

    test.each(partitions)("%s: the policy shrinks and fits the delivery cap", (partition) => {
        const csp = policy(partition);
        expect(csp.length).toBeLessThan(CAP(partition));
        const wildcard = `https://*.${deriveServiceDnsSuffix(
            partition,
            "S3",
            SERVICE_LOOKUP["s3"][partition].hostname
        )}`;
        const newBytes = wildcard.length * WILDCARD_DIRECTIVES.length;
        // The wildcard is shorter than the regional S3 host it replaces.
        expect(newBytes).toBeLessThan(oldS3Bytes(partition));
        // eslint-disable-next-line no-console
        console.log(
            `[csp wildcard size] ${partition}: s3 entries ${oldS3Bytes(
                partition
            )} -> ${newBytes} bytes, policy ${csp.length}/${CAP(partition)}`
        );
    });

    test.each(partitions)(
        "%s: the wildcard covers a second Region, virtual-hosted and accelerate hosts",
        (partition) => {
            const csp = policy(partition);
            const suffix = deriveServiceDnsSuffix(
                partition,
                "S3",
                SERVICE_LOOKUP["s3"][partition].hostname
            );
            const second = OTHER_REGION[partition];
            const hosts = [
                `s3.${second}.${suffix}`,
                `bucket.s3.${second}.${suffix}`,
                `bucket.s3-accelerate.${suffix}`,
                `s3.${REGION[partition]}.${suffix}`,
            ];
            for (const d of WILDCARD_DIRECTIVES) {
                for (const host of hosts) {
                    expect(directive(csp, d).some((src) => hostSourceMatches(src, host))).toBe(
                        true
                    );
                }
                // Not wider than the suffix: the bare suffix, a lookalike and a public suffix stay out.
                for (const host of [suffix, `evil-${suffix}`, "example.com", "s3.example.eu"]) {
                    if (host === "example.com" && suffix === "amazonaws.com") continue;
                    expect(directive(csp, d).some((src) => hostSourceMatches(src, host))).toBe(
                        false
                    );
                }
            }
            // script-src and the other directives gain no AWS host.
            expect(directive(csp, "script-src").some((s) => s.includes("*."))).toBe(false);
        }
    );
});

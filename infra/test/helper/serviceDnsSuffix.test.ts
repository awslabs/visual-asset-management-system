/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `Service(...).DnsSuffix` is the partition's service DNS suffix, derived from the hostname table
 * rather than from a hardcoded partition string. The web CSP builds its AWS service-host wildcard
 * (`https://*.<suffix>`) from it, so every partition the S3 table defines must derive a suffix that
 * the S3 hostname actually ends with.
 */

import { Config } from "../../config/config";
import { SERVICE_LOOKUP } from "../../lib/helper/const";
import { Service, SetConfig, deriveServiceDnsSuffix } from "../../lib/helper/service-helper";

// One region per partition the S3 table defines.
const REGION_FOR_PARTITION: { [partition: string]: string } = {
    aws: "us-east-1",
    "aws-cn": "cn-north-1",
    "aws-us-gov": "us-gov-west-1",
    "aws-iso": "us-iso-east-1",
    "aws-iso-b": "us-isob-east-1",
    "aws-iso-e": "eu-isoe-west-1",
    "aws-iso-f": "us-isof-south-1",
    "aws-eusc": "eusc-de-east-1",
};

const EXPECTED_SUFFIX: { [partition: string]: string } = {
    aws: "amazonaws.com",
    "aws-cn": "amazonaws.com.cn",
    "aws-us-gov": "amazonaws.com",
    "aws-iso": "c2s.ic.gov",
    "aws-iso-b": "sc2s.sgov.gov",
    "aws-iso-e": "cloud.adc-e.uk",
    "aws-iso-f": "csp.hci.ic.gov",
    "aws-eusc": "amazonaws.eu",
};

const useRegion = (region: string) =>
    SetConfig({ env: { region, account: "111111111111" }, app: { useFips: false } } as Config);

describe("Service().DnsSuffix", () => {
    const partitions = Object.keys(SERVICE_LOOKUP["s3"]);

    test("the test covers every partition the S3 table defines", () => {
        expect(partitions.sort()).toEqual(Object.keys(REGION_FOR_PARTITION).sort());
        expect(partitions.sort()).toEqual(Object.keys(EXPECTED_SUFFIX).sort());
    });

    test.each(Object.keys(REGION_FOR_PARTITION))(
        "%s: the suffix is what the S3 hostname ends with",
        (partition) => {
            const region = REGION_FOR_PARTITION[partition];
            useRegion(region);
            const suffix = Service("S3", false).DnsSuffix;
            expect(suffix).toBe(EXPECTED_SUFFIX[partition]);
            expect(Service("S3", false).Endpoint).toBe(`s3.${region}.${suffix}`);
        }
    );

    test("the suffix does not depend on the FIPS setting", () => {
        useRegion("us-gov-west-1");
        expect(Service("S3", true).DnsSuffix).toBe(Service("S3", false).DnsSuffix);
    });
});

describe("deriveServiceDnsSuffix", () => {
    test.each([
        ["no region label", "s3.amazonaws.com"],
        ["nothing before the region label", "{region}.amazonaws.com"],
        ["a bare public suffix", "s3.{region}.com"],
        ["a one-label suffix", "s3.{region}.eu"],
        ["a bare two-label public suffix", "s3.{region}.co.uk"],
        ["a bare registry suffix", "s3.{region}.ic.gov"],
        ["a wildcard in the suffix", "s3.{region}.*.amazonaws.com"],
        ["a second placeholder in the suffix", "s3.{region}.{account-id}.amazonaws.com"],
        ["an empty label", "s3.{region}..amazonaws.com"],
        ["an empty suffix", "s3.{region}."],
    ])("throws, naming the partition, for %s", (_why, hostname) => {
        expect(() => deriveServiceDnsSuffix("aws-test", "S3", hostname)).toThrow(
            /Partition "aws-test" service S3/
        );
    });

    test("derives the suffix of a well-formed template", () => {
        expect(deriveServiceDnsSuffix("aws", "S3", "s3.{region}.amazonaws.com")).toBe(
            "amazonaws.com"
        );
        expect(deriveServiceDnsSuffix("aws", "ECR", "api.ecr.{region}.amazonaws.com")).toBe(
            "amazonaws.com"
        );
    });
});

describe("every partition the S3 table defines", () => {
    const rows = Object.entries(SERVICE_LOOKUP["s3"]);

    test.each(rows.map(([partition, info]) => [partition, info.hostname]))(
        "%s (%s): suffix has at least two labels and is not a bare public suffix",
        (partition, hostname) => {
            const suffix = deriveServiceDnsSuffix(partition, "S3", hostname);
            expect(suffix.split(".").length).toBeGreaterThanOrEqual(2);
            expect(suffix).toBe(EXPECTED_SUFFIX[partition]);
            expect(["com", "eu", "uk", "gov", "cn"]).not.toContain(suffix);
            expect(hostname.endsWith(`.${suffix}`)).toBe(true);
        }
    );
});

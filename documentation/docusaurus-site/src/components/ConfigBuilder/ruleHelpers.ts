/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Helpers the core rules in `validation.ts` and the add-on rule modules under `addon/` share. They
 * live apart from `validation.ts` so an add-on module can use them without importing the file that
 * imports it.
 */

import type { ConfigShape } from "./types";
import { getByPath } from "./pathUtils";

/**
 * Region-name prefix to partition name and DNS suffix, matching what
 * `region_info.RegionInfo.get(region)` resolves at synth time (config.ts: "garnetIngestionQueueSqsUrl
 * must be a valid SQS URL" builds the pattern from that suffix). A region with no listed prefix is
 * commercial. Verified against every Region
 * aws-cdk-lib knows. Ordered longest-prefix-first so `us-isob-` is not shadowed by `us-iso-`.
 */
export const PARTITION_DNS_SUFFIXES: { prefix: string; partition: string; suffix: string }[] = [
    { prefix: "us-gov-", partition: "aws-us-gov", suffix: "amazonaws.com" },
    { prefix: "cn-", partition: "aws-cn", suffix: "amazonaws.com.cn" },
    { prefix: "eusc-", partition: "aws-eusc", suffix: "amazonaws.eu" },
    { prefix: "us-isob-", partition: "aws-iso-b", suffix: "sc2s.sgov.gov" },
    { prefix: "us-isof-", partition: "aws-iso-f", suffix: "csp.hci.ic.gov" },
    { prefix: "us-iso-", partition: "aws-iso", suffix: "c2s.ic.gov" },
    { prefix: "eu-isoe-", partition: "aws-iso-e", suffix: "cloud.adc-e.uk" },
];

export const SECRETSMANAGER_ARN_PATTERN = /^arn:aws[a-z-]*:secretsmanager:/;

/** One enabled feature that requires a VPC, as `getConfig()` collects them. */
export interface VpcRequiringFeature {
    id: string;
    label: string;
    fieldPaths: string[];
    appliesWhen: (cfg: ConfigShape) => boolean;
}

/** config.ts treats null / "" / "UNDEFINED" (and missing) all as unset. */
export function isUnset(value: unknown): boolean {
    return value == null || value === "" || value === "UNDEFINED";
}

/**
 * True for the values `getConfig()`'s `== undefined` backfills treat as absent — null and undefined,
 * and nothing else. A field that is absent from `config.json` is filled with its default before the
 * matching check runs, so a rule guarded by this stays silent rather than reporting a value the
 * deployment would supply for itself. `""` is deliberately NOT absent: `getConfig()` does not backfill
 * it, so an empty string reaches the check and is rejected there.
 */
export function isAbsent(value: unknown): boolean {
    return value == null;
}

/** HuggingFace token check also rejects whitespace-only (config.ts: the `huggingFaceToken.trim() === ""` checks). */
export function isBlank(value: unknown): boolean {
    return isUnset(value) || (typeof value === "string" && value.trim() === "");
}

export function g(cfg: ConfigShape, path: string): any {
    return getByPath(cfg, path);
}

/**
 * The partition implied by `env.region`, or undefined when the region is unset — the deploy-time
 * region can still come from CDK context or the environment, which the browser cannot read, so the
 * partition is unknowable and partition-specific rules stay silent.
 */
export function partitionForRegionName(region: unknown): string | undefined {
    if (isUnset(region) || typeof region !== "string") return undefined;
    const match = PARTITION_DNS_SUFFIXES.find((entry) => region.startsWith(entry.prefix));
    return match ? match.partition : "aws";
}

/** True when a value looks like a valid URL (config.ts uses `new URL(...)`). */
export function isValidUrl(value: unknown): boolean {
    if (typeof value !== "string" || value === "") return false;
    try {
        // eslint-disable-next-line no-new
        new URL(value);
        return true;
    } catch {
        return false;
    }
}

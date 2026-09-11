/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Entry formats accepted by `validatePresignedUrlRestrictions` in
 * infra/config/config.ts, for `app.assetBuckets.presignedUrlNetworkRestrictions`.
 * Transcribed from that function so the builder marks exactly the entries a
 * `cdk synth` would reject. Pure TypeScript with no React import, so both the
 * field editor and `validation.ts` can use it.
 */

const IPV4_CIDR = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/;
const IPV6_CIDR = /^([0-9a-fA-F:]+)\/(\d{1,3})$/;
const VPCE_ID = /^vpce-[0-9a-f]{8,}$/;

/** True for an IPv4 or IPv6 CIDR — an address plus a prefix length in range. */
export function isCidr(entry: string): boolean {
    const v4 = entry.match(IPV4_CIDR);
    if (v4) {
        return v4.slice(1, 5).every((octet) => parseInt(octet) <= 255) && parseInt(v4[5]) <= 32;
    }
    const v6 = entry.match(IPV6_CIDR);
    return !!v6 && entry.includes(":") && parseInt(v6[2]) <= 128;
}

/** True for a VPC endpoint ID — `vpce-` followed by at least eight hex digits. */
export function isVpceId(entry: string): boolean {
    return VPCE_ID.test(entry);
}

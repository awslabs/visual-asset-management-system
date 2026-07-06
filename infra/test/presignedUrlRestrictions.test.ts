/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    validatePresignedUrlRestrictions,
    ConfigPresignedUrlNetworkRestrictions,
} from "../config/config";

const restrictions = (
    overrides: Partial<ConfigPresignedUrlNetworkRestrictions>
): ConfigPresignedUrlNetworkRestrictions => ({
    allowedIpRanges: [],
    allowedVpceIds: [],
    ...overrides,
});

describe("validatePresignedUrlRestrictions", () => {
    test("accepts undefined (no restrictions configured)", () => {
        expect(() => validatePresignedUrlRestrictions(undefined, "test")).not.toThrow();
    });

    test("accepts empty lists", () => {
        expect(() => validatePresignedUrlRestrictions(restrictions({}), "test")).not.toThrow();
    });

    test("accepts valid IPv4 CIDRs", () => {
        expect(() =>
            validatePresignedUrlRestrictions(
                restrictions({ allowedIpRanges: ["203.0.113.0/24", "10.0.0.1/32"] }),
                "test"
            )
        ).not.toThrow();
    });

    test("accepts valid IPv6 CIDRs", () => {
        expect(() =>
            validatePresignedUrlRestrictions(
                restrictions({ allowedIpRanges: ["2001:db8::/32", "::1/128"] }),
                "test"
            )
        ).not.toThrow();
    });

    test("accepts valid VPC endpoint IDs", () => {
        expect(() =>
            validatePresignedUrlRestrictions(
                restrictions({ allowedVpceIds: ["vpce-0123456789abcdef0"] }),
                "test"
            )
        ).not.toThrow();
    });

    test("rejects IP ranges and VPCE IDs configured together", () => {
        expect(() =>
            validatePresignedUrlRestrictions(
                restrictions({
                    allowedIpRanges: ["203.0.113.0/24"],
                    allowedVpceIds: ["vpce-0123456789abcdef0"],
                }),
                "test"
            )
        ).toThrow(/Configuration Error.*not both/);
    });

    test("rejects an IPv4 address without a prefix length", () => {
        expect(() =>
            validatePresignedUrlRestrictions(
                restrictions({ allowedIpRanges: ["203.0.113.5"] }),
                "test"
            )
        ).toThrow(/Configuration Error/);
    });

    test("rejects a malformed CIDR", () => {
        expect(() =>
            validatePresignedUrlRestrictions(
                restrictions({ allowedIpRanges: ["not-a-cidr/24"] }),
                "test"
            )
        ).toThrow(/Configuration Error/);
    });

    test("rejects an IPv4 prefix length over 32", () => {
        expect(() =>
            validatePresignedUrlRestrictions(
                restrictions({ allowedIpRanges: ["203.0.113.0/33"] }),
                "test"
            )
        ).toThrow(/Configuration Error/);
    });

    test("rejects a malformed VPC endpoint ID", () => {
        expect(() =>
            validatePresignedUrlRestrictions(
                restrictions({ allowedVpceIds: ["vpc-0123456789abcdef0"] }),
                "test"
            )
        ).toThrow(/Configuration Error/);
    });
});

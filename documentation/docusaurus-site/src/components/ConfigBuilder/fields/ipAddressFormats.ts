/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Address-family detection for the authorizer IP allow-list, mirroring `infra/config/config.ts`.
 *
 * A hand-ported mirror rather than a shared import: the ConfigBuilder runs in the browser and cannot
 * reach the CDK sources. `validation.ts` mirrors `getConfig()`'s validation rules, and the drift check
 * (`infra/test/config/configBuilderSync.test.ts`) covers only `schema.ts` and `defaults.ts` — so a rule
 * that drifts here is silent, and worse than absent, because the operator has been told a configuration
 * is valid.
 *
 * This file exists because that drift happened. `getConfig()` accepts an IPv6 allow-list entry, while
 * `validation.ts` still tested both endpoints against an IPv4-only pattern — so the builder rejected a
 * configuration the deployment accepts.
 *
 * Keep in step with `ipAddressFamily` / `isIpv4Literal` / `isIpv6Literal` in `infra/config/config.ts`.
 */

/**
 * The address family of an IP literal, or `undefined` when the value is not one.
 *
 * A zone index (`%eth0`) and a prefix length (`/64`) are rejected: an allow-list entry is a single
 * endpoint, not a network or a scoped address.
 */
export function ipAddressFamily(value: unknown): 4 | 6 | undefined {
    if (typeof value !== "string") {
        return undefined;
    }
    const address = value.trim();
    if (address === "" || address.includes("%") || address.includes("/")) {
        return undefined;
    }
    if (isIpv4Literal(address)) {
        return 4;
    }
    return isIpv6Literal(address) ? 6 : undefined;
}

function isIpv4Literal(address: string): boolean {
    return /^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/.test(
        address
    );
}

/**
 * Structural IPv6 check: at most one `::`, hextets of 1-4 hex digits, an optional dotted-quad
 * tail counting as two hextets, and a total of exactly 8 hextets without `::` or fewer than 8
 * with it. A single regex for this is unreadable and historically gets the `::` cases wrong.
 */
function isIpv6Literal(address: string): boolean {
    if (!address.includes(":")) {
        return false;
    }
    const compressedParts = address.split("::");
    if (compressedParts.length > 2) {
        return false;
    }
    const compressed = compressedParts.length === 2;
    // A leading or trailing `::` leaves an empty side, which contributes no hextets.
    const sides = compressedParts.map((side) => (side === "" ? [] : side.split(":")));
    if (sides.some((side) => side.some((hextet) => hextet === ""))) {
        return false;
    }

    let hextetCount = 0;
    for (let sideIndex = 0; sideIndex < sides.length; sideIndex++) {
        const side = sides[sideIndex];
        for (let partIndex = 0; partIndex < side.length; partIndex++) {
            const part = side[partIndex];
            const isLastPartOfAddress =
                sideIndex === sides.length - 1 && partIndex === side.length - 1;
            if (isLastPartOfAddress && part.includes(".")) {
                if (!isIpv4Literal(part)) {
                    return false;
                }
                hextetCount += 2;
                continue;
            }
            if (!/^[0-9a-fA-F]{1,4}$/.test(part)) {
                return false;
            }
            hextetCount += 1;
        }
    }

    return compressed ? hextetCount < 8 : hextetCount === 8;
}

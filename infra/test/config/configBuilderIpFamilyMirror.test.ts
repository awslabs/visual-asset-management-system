/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The ConfigBuilder's address-family helper agrees with the one `getConfig()` validates against.
 *
 * `documentation/docusaurus-site/src/components/ConfigBuilder/validation.ts` mirrors `getConfig()`'s
 * validation rules by hand, and `configBuilderSync.test.ts` covers only `schema.ts` and `defaults.ts` —
 * so a drifted validation rule is silent, and worse than an absent one, because the operator has been
 * told a configuration is valid.
 *
 * MEASURED drift, which is why this exists. `getConfig()` was changed to accept an IPv6 allow-list entry
 * (the authorizer compares numerically with Python's `ipaddress`), and `validation.ts` went on testing
 * both endpoints against an IPv4-only regex. The builder therefore rejected a configuration the
 * deployment accepts — the opposite of the usual drift and just as wrong.
 *
 * This test lives in `infra/test` on purpose. The ConfigBuilder's `.tsx` files are unreachable by any
 * runner in this repository (infra's jest sets `roots: ["test"]` with `testEnvironment: "node"`), but a
 * PURE-TypeScript module in the component is importable from here, which is why the ported helper was
 * given its own file rather than being inlined into `validation.ts`.
 */

import { ipAddressFamily as cdkFamily } from "../../config/config";
import { ipAddressFamily as builderFamily } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/fields/ipAddressFormats";

/** Cases chosen for the `::` and boundary behaviour a single regex historically gets wrong. */
const CASES: Array<[unknown, 4 | 6 | undefined, string]> = [
    ["192.168.1.1", 4, "ordinary IPv4"],
    ["0.0.0.0", 4, "IPv4 lower bound"],
    ["255.255.255.255", 4, "IPv4 upper bound"],
    ["256.1.1.1", undefined, "octet out of range"],
    ["192.168.1", undefined, "three octets"],
    ["2001:db8::", 6, "trailing compression"],
    ["2001:db8::ffff", 6, "compression in the middle"],
    ["2001:0db8:0000:0000:0000:0000:0000:0001", 6, "fully expanded, 8 hextets"],
    ["::1", 6, "leading compression"],
    ["::", 6, "all zeroes"],
    ["::ffff:192.168.1.1", 6, "dotted-quad tail counts as two hextets"],
    ["2001:db8:::1", undefined, "two compressions"],
    ["2001:db8::1%eth0", undefined, "zone index is not an endpoint"],
    ["2001:db8::/64", undefined, "a prefix length is a network, not an endpoint"],
    ["1:2:3:4:5:6:7", undefined, "seven hextets with no compression"],
    ["1:2:3:4:5:6:7:8:9", undefined, "nine hextets"],
    ["12345::1", undefined, "hextet longer than four hex digits"],
    ["not-an-address", undefined, "not an address"],
    ["", undefined, "empty string"],
    [42, undefined, "not a string"],
    [null, undefined, "null"],
    [undefined, undefined, "undefined"],
];

describe("ConfigBuilder ip-address mirror", () => {
    it("[control] both helpers are real functions, not undefined imports", () => {
        // An import that resolved to undefined would make every assertion below throw rather than
        // pass — but a mistyped `.some()` guard could still swallow it, so this is asserted first.
        expect(typeof cdkFamily).toBe("function");
        expect(typeof builderFamily).toBe("function");
    });

    it.each(CASES)("%s -> %s (%s)", (value, expected) => {
        expect(cdkFamily(value)).toBe(expected);
        expect(builderFamily(value)).toBe(expected);
    });

    it("the two helpers agree on every case", () => {
        const disagreements = CASES.filter(([v]) => cdkFamily(v) !== builderFamily(v)).map(
            ([v, , why]) =>
                `${JSON.stringify(v)} (${why}): cdk=${cdkFamily(v)} builder=${builderFamily(v)}`
        );
        expect(disagreements).toEqual([]);
    });

    it("[control] the comparison can detect a disagreement", () => {
        // Without this, "they agree" is satisfied by two helpers that both return undefined for
        // everything — which is exactly what a broken port looks like.
        const distinct = new Set(CASES.map(([v]) => String(builderFamily(v))));
        expect(distinct).toContain("4");
        expect(distinct).toContain("6");
        expect(distinct).toContain("undefined");
    });

    it("an IPv6 range is accepted where an IPv4-only pattern would refuse it", () => {
        // The specific drift this file was written for, stated as the property rather than as a diff.
        const ipv4Only =
            /^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/;
        for (const address of ["2001:db8::", "2001:db8::ffff"]) {
            expect(ipv4Only.test(address)).toBe(false); // the old rule refused it
            expect(builderFamily(address)).toBe(6); // the mirror accepts it
            expect(cdkFamily(address)).toBe(6); // and so does the deployment
        }
    });
});

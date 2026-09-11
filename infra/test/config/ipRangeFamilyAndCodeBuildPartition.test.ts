/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Two `getConfig()` rules that were missing.
 *
 * **IPv6 in the authorizer allow-list.** `authorizerOptions.allowedIpRanges` accepted only IPv4
 * dotted quads, so no IPv6 range was expressible — while the authorizer's `_strip_port` parses the
 * bracketed `[2001:db8::1]:50314` CloudFront viewer-address form and a unit test asserts it. The
 * matcher now compares with Python's `ipaddress`, so both families work; the validator has to admit
 * an IPv6 literal for a range to be configurable at all. Both endpoints of one entry must be the
 * same family: a mixed pair has no ordering, and the authorizer skips such an entry rather than
 * allowing it, so a throw here is what surfaces the mistake instead of silently narrowing access.
 *
 * **`useCodeBuild` outside the supported partitions.** Decision E-6 is "warn only" — inform rather
 * than prevent, because partition support changes and a hard guard would block legitimate
 * evaluation. So the assertions come in pairs: the warning must fire, and `getConfig()` must NOT
 * throw. A test asserting only the warning would pass on an implementation that also rejected the
 * config, which is the outcome the ruling excluded.
 *
 * `getConfig()` reads `config/config.json` from disk, so `fs.readFileSync` is mocked to serve a
 * chosen template. Only the config filename is intercepted; the S3 and WAF policy JSON reads
 * `getConfig()` also performs fall through to the real implementation.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** Serve `configJson` for config.json; delegate every other path to the real fs. */
const serveConfig = (configJson: unknown) => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (path: string, ...rest: unknown[]) => {
            if (typeof path === "string" && path.endsWith("config.json")) {
                return JSON.stringify(configJson);
            }
            return realReadFileSync(path, ...rest);
        }
    );
};

const templateFor = (base: unknown, region: string) => {
    const config = JSON.parse(JSON.stringify(base));
    config.env.region = region;
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    }
    return config;
};

// No jest.resetModules() here: it re-runs the fs mock factory, so getConfig() would bind to a
// second mock instance while serveConfig() configures the first, and every "does not throw"
// assertion would pass while reading the real on-disk config.json.
const loadConfig = (base: unknown, region: string, mutate?: (c: any) => void) => {
    const config = templateFor(base, region);
    mutate?.(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

// ---------------------------------------------------------------------------------------------
// ipAddressFamily
// ---------------------------------------------------------------------------------------------

describe("ipAddressFamily", () => {
    test.each([
        ["203.0.113.7", 4],
        ["0.0.0.0", 4],
        ["255.255.255.255", 4],
        ["2001:db8::1", 6],
        ["2001:db8::", 6],
        ["::1", 6],
        ["::", 6],
        ["2001:0db8:0000:0000:0000:0000:0000:0001", 6],
        ["fe80::0202:b3ff:fe1e:8329", 6],
        ["::ffff:192.0.2.1", 6],
        ["2001:db8::ffff:192.0.2.1", 6],
    ])("classifies %s as IPv%s", (address, family) => {
        expect(Config.ipAddressFamily(address)).toBe(family);
    });

    test.each([
        ["256.0.0.1", "octet out of range"],
        ["203.0.113", "too few octets"],
        ["203.0.113.7.8", "too many octets"],
        ["2001:db8::1::2", "two :: compressions"],
        ["2001:db8:0:0:0:0:0:0:1", "nine hextets"],
        ["2001:db8:0:0:0:0:0", "seven hextets, uncompressed"],
        ["2001:db8::12345", "hextet longer than four digits"],
        ["2001:db8::g", "non-hex digit"],
        ["2001:db8::/64", "a network, not an address"],
        ["fe80::1%eth0", "zone index"],
        ["", "empty"],
        ["   ", "whitespace only"],
        ["localhost", "a hostname"],
    ])("rejects %s (%s)", (address) => {
        expect(Config.ipAddressFamily(address)).toBeUndefined();
    });

    test("rejects a non-string", () => {
        expect(Config.ipAddressFamily(undefined)).toBeUndefined();
        expect(Config.ipAddressFamily(null)).toBeUndefined();
        expect(Config.ipAddressFamily(3232235777)).toBeUndefined();
        expect(Config.ipAddressFamily(["203.0.113.7"])).toBeUndefined();
    });

    test("tolerates surrounding whitespace, as the authorizer does", () => {
        expect(Config.ipAddressFamily(" 203.0.113.7 ")).toBe(4);
        expect(Config.ipAddressFamily(" 2001:db8::1 ")).toBe(6);
    });
});

// ---------------------------------------------------------------------------------------------
// getConfig() allowedIpRanges validation
// ---------------------------------------------------------------------------------------------

const INVALID_FORMAT = /Invalid IP address format in range at index/;
const MIXED_FAMILY = /mixes IPv4 and IPv6 endpoints|mixes IPv6 and IPv4 endpoints/;
const NOT_A_PAIR = /must be an array of exactly 2 IP addresses/;

const withRanges = (ranges: unknown) => (c: any) => {
    c.app.authProvider.authorizerOptions.allowedIpRanges = ranges;
};

describe("authorizerOptions.allowedIpRanges accepts both address families", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("accepts an IPv6 range", () => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            withRanges([["2001:db8::", "2001:db8::ffff"]])
        );
        expect(run).not.toThrow(INVALID_FORMAT);
    });

    test("accepts IPv4 and IPv6 ranges side by side", () => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            withRanges([
                ["203.0.113.0", "203.0.113.255"],
                ["2001:db8::", "2001:db8::ffff"],
            ])
        );
        expect(run).not.toThrow(INVALID_FORMAT);
    });

    test("still accepts an IPv4-only range", () => {
        // Control: the relaxation must not have replaced the IPv4 rule with a permissive one.
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            withRanges([["203.0.113.0", "203.0.113.255"]])
        );
        expect(run).not.toThrow();
    });

    test("still rejects a malformed address", () => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            withRanges([["203.0.113.0", "not-an-ip"]])
        );
        expect(run).toThrow(INVALID_FORMAT);
    });

    test("still rejects an out-of-range IPv4 octet", () => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            withRanges([["203.0.113.0", "203.0.113.256"]])
        );
        expect(run).toThrow(INVALID_FORMAT);
    });

    test("rejects a CIDR, which is a network rather than an endpoint", () => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            withRanges([["2001:db8::/32", "2001:db8::ffff"]])
        );
        expect(run).toThrow(INVALID_FORMAT);
    });

    test("rejects a range whose endpoints are different families", () => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            withRanges([["203.0.113.0", "2001:db8::ffff"]])
        );
        expect(run).toThrow(MIXED_FAMILY);
    });

    test("still rejects an entry that is not a two-element array", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", withRanges([["2001:db8::"]]));
        expect(run).toThrow(NOT_A_PAIR);
    });
});

// ---------------------------------------------------------------------------------------------
// useCodeBuild partition posture (decision E-6: warn only)
// ---------------------------------------------------------------------------------------------

const CODEBUILD_WARNING = /useCodeBuild is true for .* while deploying to the '[^']+' partition/;

const CODEBUILD_PIPELINES = [
    "useConversionCoordinateTransform",
    "useSplatToolbox",
    "useIsaacLabTraining",
    "useNvidiaCosmos",
    "useNvidiaCosmos3",
    "useNvidiaGr00t",
];

const warningsFrom = (run: () => unknown): string[] => {
    const captured: string[] = [];
    const spy = jest.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
        captured.push(args.map(String).join(" "));
    });
    try {
        run();
    } finally {
        spy.mockRestore();
    }
    return captured.filter((message) => CODEBUILD_WARNING.test(message));
};

describe("useCodeBuild outside the supported partitions warns and does not reject", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("every pipeline that exposes useCodeBuild is covered by the check", () => {
        // The check iterates a hardcoded list of config paths, so a seventh CodeBuild pipeline added
        // to the interface without a list entry would be silently uncovered. Derived from the
        // shipped template rather than restated, so the two cannot drift apart.
        const pipelines = (commercialTemplate as any).app.pipelines;
        const exposed = Object.keys(pipelines).filter(
            (name) =>
                pipelines[name] &&
                typeof pipelines[name] === "object" &&
                "useCodeBuild" in pipelines[name]
        );
        expect(exposed.sort()).toEqual([...CODEBUILD_PIPELINES].sort());
    });

    test.each(CODEBUILD_PIPELINES)("warns for %s in the EU Sovereign Cloud", (pipeline) => {
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1", (c) => {
            c.app.pipelines[pipeline].useCodeBuild = true;
        });
        const warnings = warningsFrom(run);
        expect(warnings.join(" ")).toContain(`app.pipelines.${pipeline}`);
    });

    test("does NOT throw — decision E-6 is warn only, not reject", () => {
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1", (c) => {
            c.app.pipelines.useSplatToolbox.useCodeBuild = true;
        });
        const spy = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            expect(run).not.toThrow();
        } finally {
            spy.mockRestore();
        }
    });

    test("names every offending pipeline in one warning", () => {
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1", (c) => {
            for (const pipeline of CODEBUILD_PIPELINES) {
                c.app.pipelines[pipeline].useCodeBuild = true;
            }
        });
        const warnings = warningsFrom(run);
        expect(warnings).toHaveLength(1);
        for (const pipeline of CODEBUILD_PIPELINES) {
            expect(warnings[0]).toContain(`app.pipelines.${pipeline}`);
        }
    });

    test("does not warn in the commercial partition", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.pipelines.useSplatToolbox.useCodeBuild = true;
        });
        expect(warningsFrom(run)).toEqual([]);
    });

    test("does not warn in GovCloud", () => {
        const run = loadConfig(govcloudTemplate, "us-gov-west-1", (c) => {
            c.app.pipelines.useSplatToolbox.useCodeBuild = true;
        });
        expect(warningsFrom(run)).toEqual([]);
    });

    test("does not warn on the shipped EU Sovereign config, which sets useCodeBuild false", () => {
        // Control for the six warning cases above: proves the trigger is the flag, not the partition.
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1");
        expect(warningsFrom(run)).toEqual([]);
    });
});

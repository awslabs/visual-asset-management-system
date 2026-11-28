/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` validation of `app.api.apiGatewayRest.customDomain`, and the ConfigBuilder rules that
 * mirror it.
 *
 * The custom domain is a Regional API Gateway domain name, so each rule fails at synth with the field
 * named instead of at deploy:
 *   * `domainHost` and `certificateArn` are required when the domain is enabled;
 *   * the host is a lowercase name with no scheme, port or path (API Gateway rejects uppercase);
 *   * the host differs, in any letter case, from the web application's host: the CloudFront custom
 *     domain's or the ALB's `domainHost`, each only while that front is enabled;
 *   * `optionalHostedZoneId`, when set, is a Route 53 hosted zone ID without the `/hostedzone/` prefix;
 *   * a `PRIVATE` endpoint cannot take it — a private API is reached through its VPC endpoint;
 *   * the partition must be one where VAMS creates the domain (commercial and AWS GovCloud (US));
 *   * the certificate must be an ACM certificate in the deployment Region and partition, because a
 *     Regional domain name takes a certificate from its own Region.
 * Each rule is exercised in both directions, and the disabled default is the control.
 *
 * `configBuilderSync.test.ts` covers only `schema.ts` and `defaults.ts`, so the hand-ported
 * `validation.ts` rules are asserted here against the same cases.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import { newTestApp } from "../support/testApp";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

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

const HOST = "api.vams.example.com";
const certArn = (partition: string, region: string) =>
    `arn:${partition}:acm:${region}:123456789012:certificate/11111111-2222-3333-4444-555555555555`;

/** A deployable config derived from a template, with the placeholders getConfig() requires filled. */
const templateFor = (base: unknown, region: string) => {
    const config = JSON.parse(JSON.stringify(base));
    config.env.region = region;
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn = certArn("aws", "us-east-1");
    }
    return config;
};

// config.ts is imported once at module scope and getConfig() re-reads config.json on every call, so
// jest.resetModules() is not used (it would bind config.ts to a second fs mock instance).
const loadConfig = (base: unknown, region: string, mutate?: (c: any) => void) => {
    const config = templateFor(base, region);
    mutate?.(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

/** Enable the custom domain with a valid host and certificate for the given partition and Region. */
const enable =
    (partition: string, region: string, overrides: Record<string, unknown> = {}) =>
    (c: any) => {
        c.app.api.apiGatewayRest.customDomain = {
            enabled: true,
            domainHost: HOST,
            certificateArn: certArn(partition, region),
            optionalHostedZoneId: "",
            ...overrides,
        };
    };

const ANY_CUSTOM_DOMAIN_ERROR = /app\.api\.apiGatewayRest\.customDomain/;

describe("app.api.apiGatewayRest.customDomain validation", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("the shipped templates leave it disabled and pass (control)", () => {
        for (const [base, region] of [
            [commercialTemplate, "us-east-1"],
            [govcloudTemplate, "us-gov-west-1"],
            [eusovereignTemplate, "eusc-de-east-1"],
        ] as const) {
            expect((base as any).app.api.apiGatewayRest.customDomain.enabled).toBe(false);
            expect(loadConfig(base, region)).not.toThrow(ANY_CUSTOM_DOMAIN_ERROR);
        }
    });

    test("a config.json without the block is backfilled to disabled", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            delete c.app.api.apiGatewayRest.customDomain;
        });
        const config = run();
        expect(config.app.api.apiGatewayRest.customDomain).toEqual({
            enabled: false,
            domainHost: "",
            certificateArn: "",
            optionalHostedZoneId: "",
        });
        expect(Config.apiCustomDomainHost(config)).toBeUndefined();
    });

    test("accepts a valid host and certificate in the commercial partition", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", enable("aws", "us-east-1"));
        const config = run();
        expect(Config.apiCustomDomainHost(config)).toBe(HOST);
    });

    test("accepts a valid host and certificate in AWS GovCloud (US)", () => {
        const run = loadConfig(
            govcloudTemplate,
            "us-gov-west-1",
            enable("aws-us-gov", "us-gov-west-1")
        );
        expect(run).not.toThrow();
    });

    test.each([
        ["domainHost", { domainHost: "" }],
        ["certificateArn", { certificateArn: "" }],
        ["domainHost UNDEFINED", { domainHost: "UNDEFINED" }],
    ])("rejects a missing %s", (_label, overrides) => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            enable("aws", "us-east-1", overrides)
        );
        expect(run).toThrow(/customDomain\.enabled requires .*domainHost and .*certificateArn/);
    });

    test("does not require the fields while disabled", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            enable("aws", "us-east-1", { domainHost: "", certificateArn: "" })(c);
            c.app.api.apiGatewayRest.customDomain.enabled = false;
        });
        expect(run).not.toThrow(ANY_CUSTOM_DOMAIN_ERROR);
    });

    test.each([
        ["a scheme", `https://${HOST}`],
        ["a path", `${HOST}/api`],
        ["a port", `${HOST}:443`],
        ["uppercase letters", "API.vams.example.com"],
        ["a single label", "localhost"],
    ])("rejects a domainHost with %s", (_label, domainHost) => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            enable("aws", "us-east-1", { domainHost })
        );
        expect(run).toThrow(/customDomain\.domainHost must be a lowercase host name/);
    });

    test("rejects a PRIVATE endpoint", () => {
        const run = loadConfig(govcloudTemplate, "us-gov-west-1", (c) => {
            enable("aws-us-gov", "us-gov-west-1")(c);
            c.app.api.apiGatewayRest.endpointType = "PRIVATE";
            c.app.useAlb.usePublicSubnet = false;
            c.app.useGlobalVpc.addVpcEndpoints = true;
        });
        expect(run).toThrow(/customDomain\.enabled is not supported with .*endpointType 'PRIVATE'/);
    });

    test("PRIVATE without the custom domain is not rejected by this rule (control)", () => {
        const run = loadConfig(govcloudTemplate, "us-gov-west-1", (c) => {
            c.app.api.apiGatewayRest.endpointType = "PRIVATE";
            c.app.useAlb.usePublicSubnet = false;
            c.app.useGlobalVpc.addVpcEndpoints = true;
        });
        expect(run).not.toThrow(ANY_CUSTOM_DOMAIN_ERROR);
    });

    test("rejects the EU Sovereign Cloud partition, naming the field", () => {
        const run = loadConfig(
            eusovereignTemplate,
            "eusc-de-east-1",
            enable("aws-eusc", "eusc-de-east-1")
        );
        expect(run).toThrow(
            /customDomain\.enabled is not supported in the 'aws-eusc' partition.*customDomain\.enabled to false/
        );
    });

    test.each([
        ["another Region", certArn("aws", "us-west-2")],
        ["another partition", certArn("aws-us-gov", "us-east-1")],
        ["a non-ACM ARN", "arn:aws:iam::123456789012:server-certificate/vams"],
        ["a malformed value", "certificate-1234"],
    ])("rejects a certificate ARN from %s", (_label, certificateArn) => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            enable("aws", "us-east-1", { certificateArn })
        );
        expect(run).toThrow(
            /customDomain\.certificateArn must be an ACM certificate ARN in the deployment Region \(us-east-1\)/
        );
    });

    test("accepts a certificate in a non-us-east-1 deployment Region", () => {
        // A Regional domain name takes its own Region's certificate, unlike the CloudFront one.
        const run = loadConfig(commercialTemplate, "eu-west-1", enable("aws", "eu-west-1"));
        expect(run).not.toThrow(ANY_CUSTOM_DOMAIN_ERROR);
    });

    test("rejects the CloudFront custom domain's host, compared case-insensitively", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            enable("aws", "us-east-1")(c);
            c.app.useCloudFront.customDomain = {
                enabled: true,
                domainHost: "API.Vams.Example.com",
                certificateArn: certArn("aws", "us-east-1"),
                optionalHostedZoneId: "",
            };
        });
        expect(run).toThrow(
            /customDomain\.domainHost must differ from app\.useCloudFront\.customDomain\.domainHost/
        );
    });

    test("ignores the CloudFront custom domain's host while that domain is disabled (control)", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            enable("aws", "us-east-1")(c);
            c.app.useCloudFront.customDomain = {
                enabled: false,
                domainHost: HOST,
                certificateArn: "",
                optionalHostedZoneId: "",
            };
        });
        expect(run).not.toThrow(ANY_CUSTOM_DOMAIN_ERROR);
    });

    test("rejects the ALB's host", () => {
        const run = loadConfig(
            govcloudTemplate,
            "us-gov-west-1",
            enable("aws-us-gov", "us-gov-west-1", { domainHost: "vams.example.com" })
        );
        expect(run).toThrow(/customDomain\.domainHost must differ from app\.useAlb\.domainHost/);
    });

    test("ignores the ALB host while the ALB is disabled (control)", () => {
        // The commercial template carries a placeholder ALB host with the ALB off.
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            enable("aws", "us-east-1", { domainHost: c.app.useAlb.domainHost })(c);
        });
        expect(commercialTemplate.app.useAlb.enabled).toBe(false);
        expect(run).not.toThrow(ANY_CUSTOM_DOMAIN_ERROR);
    });

    test.each([
        ["a typical ID", "Z0123456789ABCDEFGHIJ"],
        ["a short ID", "Z1D633PJN98FT9"],
        ["the longest ID", "Z" + "A".repeat(31)],
        ["an empty value", ""],
        ["UNDEFINED", "UNDEFINED"],
    ])("accepts optionalHostedZoneId with %s", (_label, optionalHostedZoneId) => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            enable("aws", "us-east-1", { optionalHostedZoneId })
        );
        expect(run).not.toThrow(ANY_CUSTOM_DOMAIN_ERROR);
    });

    test.each([
        // pragma: allowlist nextline secret
        ["the /hostedzone/ prefix", "/hostedzone/Z0123456789ABCDEFGHIJ"],
        ["lowercase letters", "z0123456789abcdefghij"],
        ["no Z prefix", "A0123456789ABCDEFGHIJ"],
        ["only the prefix", "Z"],
        ["more than 32 characters", "Z" + "A".repeat(32)],
        ["a domain name", "example.com"],
    ])("rejects optionalHostedZoneId with %s", (_label, optionalHostedZoneId) => {
        const run = loadConfig(
            commercialTemplate,
            "us-east-1",
            enable("aws", "us-east-1", { optionalHostedZoneId })
        );
        expect(run).toThrow(
            /customDomain\.optionalHostedZoneId must be an Amazon Route 53 hosted zone ID/
        );
    });
});

describe("the ConfigBuilder mirrors the customDomain rules", () => {
    const CUSTOM_DOMAIN_RULES = [
        "api-custom-domain-fields",
        "api-custom-domain-host-format",
        "api-custom-domain-host-conflict",
        "api-custom-domain-hosted-zone-id-format",
        "api-custom-domain-private-endpoint",
        "api-custom-domain-partition",
        "api-custom-domain-cert-region",
    ];
    const fired = (cfg: ReturnType<typeof makeDefaultConfig>) =>
        evaluateRules(cfg)
            .map((rule) => rule.id)
            .filter((id) => CUSTOM_DOMAIN_RULES.includes(id));

    const builderConfig = (
        profile: "commercial" | "govcloud" | "eusovereign",
        region: string,
        partition: string,
        overrides: Record<string, unknown> = {}
    ) => {
        const cfg = makeDefaultConfig(profile);
        cfg.env.region = region;
        enable(partition, region, overrides)(cfg);
        return cfg;
    };

    test("is silent on every preset (control)", () => {
        for (const profile of ["commercial", "govcloud", "eusovereign"] as const) {
            expect(fired(makeDefaultConfig(profile))).toEqual([]);
        }
    });

    test("is silent on a valid commercial and GovCloud domain", () => {
        expect(fired(builderConfig("commercial", "us-east-1", "aws"))).toEqual([]);
        expect(fired(builderConfig("govcloud", "us-gov-west-1", "aws-us-gov"))).toEqual([]);
    });

    test("flags the missing fields", () => {
        expect(fired(builderConfig("commercial", "us-east-1", "aws", { domainHost: "" }))).toEqual([
            "api-custom-domain-fields",
        ]);
    });

    test("flags a host with a scheme", () => {
        expect(
            fired(
                builderConfig("commercial", "us-east-1", "aws", { domainHost: `https://${HOST}` })
            )
        ).toEqual(["api-custom-domain-host-format"]);
    });

    test("flags a PRIVATE endpoint", () => {
        const cfg = builderConfig("govcloud", "us-gov-west-1", "aws-us-gov");
        cfg.app.api.apiGatewayRest.endpointType = "PRIVATE";
        expect(fired(cfg)).toEqual(["api-custom-domain-private-endpoint"]);
    });

    test("flags the EU Sovereign Cloud partition", () => {
        expect(fired(builderConfig("eusovereign", "eusc-de-east-1", "aws-eusc"))).toEqual([
            "api-custom-domain-partition",
        ]);
    });

    test("flags a certificate from another Region", () => {
        expect(
            fired(
                builderConfig("commercial", "us-east-1", "aws", {
                    certificateArn: certArn("aws", "us-west-2"),
                })
            )
        ).toEqual(["api-custom-domain-cert-region"]);
    });

    test("checks only the certificate format while env.region is unset", () => {
        const cfg = builderConfig("commercial", "us-east-1", "aws", {
            certificateArn: certArn("aws", "us-west-2"),
        });
        cfg.env.region = "";
        expect(fired(cfg)).toEqual([]);
        cfg.app.api.apiGatewayRest.customDomain.certificateArn = "certificate-1234";
        expect(fired(cfg)).toEqual(["api-custom-domain-cert-region"]);
    });

    test("flags the CloudFront custom domain's host, compared case-insensitively", () => {
        const cfg = builderConfig("commercial", "us-east-1", "aws");
        cfg.app.useCloudFront.customDomain.enabled = true;
        cfg.app.useCloudFront.customDomain.domainHost = "API.Vams.Example.com";
        cfg.app.useCloudFront.customDomain.certificateArn = certArn("aws", "us-east-1");
        expect(fired(cfg)).toEqual(["api-custom-domain-host-conflict"]);
        cfg.app.useCloudFront.customDomain.enabled = false;
        expect(fired(cfg)).toEqual([]);
    });

    test("flags the ALB's host only while the ALB is enabled", () => {
        const cfg = builderConfig("govcloud", "us-gov-west-1", "aws-us-gov");
        cfg.app.useAlb.domainHost = HOST;
        expect(cfg.app.useAlb.enabled).toBe(true);
        expect(fired(cfg)).toEqual(["api-custom-domain-host-conflict"]);
        cfg.app.useAlb.enabled = false;
        expect(fired(cfg)).not.toContain("api-custom-domain-host-conflict");
    });

    test("flags a malformed hosted zone ID and accepts a valid or empty one", () => {
        expect(
            fired(
                builderConfig("commercial", "us-east-1", "aws", {
                    // pragma: allowlist nextline secret
                    optionalHostedZoneId: "/hostedzone/Z0123456789ABCDEFGHIJ",
                })
            )
        ).toEqual(["api-custom-domain-hosted-zone-id-format"]);
        expect(
            fired(
                builderConfig("commercial", "us-east-1", "aws", {
                    optionalHostedZoneId: "Z0123456789ABCDEFGHIJ",
                })
            )
        ).toEqual([]);
        expect(
            fired(builderConfig("commercial", "us-east-1", "aws", { optionalHostedZoneId: "" }))
        ).toEqual([]);
    });
});

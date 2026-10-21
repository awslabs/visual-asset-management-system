/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The `getConfig()` rules for `app.addons.useEdhWorkstations`, and the ConfigBuilder's mirror of them.
 *
 * Every case asserts on the MESSAGE, not merely that something threw: enabling the add-on also trips the
 * VPC rule on the commercial template, so "it throws" would pass while the rule under test did nothing.
 * The shipped templates passing unchanged is the control for every added `throw`.
 *
 * `getConfig()` reads `config/config.json` from disk, so `fs.readFileSync` is mocked to serve a chosen
 * template; every other read (policy JSON, `existsSync` for Dockerfile directories, the synthesized
 * assembly) falls through to the real implementation.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../../config/config";
import * as EdhConfig from "../../../config/addon/edhWorkstationsConfig";
import {
    validateEdhAmiRecipes,
    loadEdhAmiRecipe,
    resolveEdhAmiRecipes,
    EDH_SHIPPED_AMI_RECIPE_DIRS,
} from "../../../config/addon/edhWorkstationsConfig";
import commercialTemplate from "../../../config/config.template.commercial.json";
import eusovereignTemplate from "../../../config/config.template.eusovereign.json";
import govcloudTemplate from "../../../config/config.template.govcloud.json";
import { newTestApp } from "../../support/testApp";
import { expectAbsent, SynthResult, synthTemplate } from "../../support/templateSynth";
import { makeDefaultConfig } from "../../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { applyDerived } from "../../../../documentation/docusaurus-site/src/components/ConfigBuilder/derived";
import { evaluateRules } from "../../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const CONFIG_DIR = path.join(__dirname, "..", "..", "..", "config");
const SHIPPED_TEMPLATES = [
    "config.template.commercial.json",
    "config.template.govcloud.json",
    "config.template.eusovereign.json",
];

/** The lease policy the shipped JSONs carry and `getConfig()` fills in, field by field. */
const LEASE_DEFAULTS = { enabled: false, leaseHours: 8, forced: false, graceHours: 24 };

/** The block every shipped JSON carries and the whole-block default `getConfig()` fills in. */
const DISABLED_BLOCK = {
    enabled: false,
    edhApiEndpoint: "",
    edhAdminApiKeySecretArn: "",
    edhApiCaBundleSecretArn: "",
    edhApiTlsServerName: "",
    edhClusterId: "",
    autoProvisionUsers: true,
    edhAllowedInstanceTypes: [],
    vdi: { defaultProject: "default", virtualDesktopProfileId: 0, lease: LEASE_DEFAULTS },
    sessionBootstrap: {
        enabled: false,
        sessionKeyTtlMinutes: 480,
        secretNamePrefix: "vams/edh-session/",
        workstationInstanceRoleArns: [],
        launchManifest: false,
    },
    amiBuilds: {
        enabled: false,
        includeShippedRecipes: true,
        buildSubnetId: "",
        buildTimeoutMinutes: 180,
        keepVersionsDefault: 3,
    },
    predefinedImages: { vdi: [], simulationAmis: [], amiRecipes: [], registrationRevision: 1 },
    containers: {
        enabled: false,
        buildAndPushImages: [],
        existingEcrImages: [],
        jobDefinitions: [],
        eksEnabled: false,
        imagePullRoleArns: [],
    },
};

const VDI_IMAGE = {
    amiId: "ami-0123456789abcdef0",
    stackName: "vams-linux-desktop",
    baseOs: "amazonlinux2023",
    rootSize: 100,
    virtualDesktopProfileId: 1,
};
const SIMULATION_AMI = {
    amiId: "ami-0fedcba9876543210",
    stackName: "vams-isaac-sim",
    rootSize: 200,
    targetNodeProfileId: 2,
};
const BUILD_IMAGE = { name: "sim-runner", dockerfileDir: "infra/config" };
const ECR_IMAGE = {
    imageUri: "123456789012.dkr.ecr.us-east-1.amazonaws.com/vendor/runner:1.0.0",
    displayName: "Vendor runner",
};
const JOB_DEFINITION = { name: "edh-train", imageRef: "sim-runner", vcpus: 4, memory: 16384 };

const REPO = path.join(__dirname, "..", "..", "..", "..");

/** Dot paths of every leaf under `value`, sorted; arrays count as leaves. */
const leafPaths = (value: unknown, prefix = ""): string[] => {
    if (value === null || typeof value !== "object" || Array.isArray(value)) return [prefix];
    return Object.keys(value as Record<string, unknown>)
        .sort()
        .flatMap((key) =>
            leafPaths((value as Record<string, unknown>)[key], prefix ? `${prefix}.${key}` : key)
        );
};

/** Serve `configJson` for config.json; delegate every other path to the real fs. */
const serveConfig = (configJson: unknown) => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json"))
                return JSON.stringify(configJson);
            return realReadFileSync(p, ...rest);
        }
    );
};

/** A deployable config derived from a template, with the placeholders getConfig() requires filled. */
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

// config.ts is imported once at module scope and getConfig() re-reads config.json on every call, so no
// jest.resetModules() is needed — and it must NOT be used: it would bind a second fs mock instance.
const loadConfig = (base: unknown, region: string, mutate?: (c: any) => void) => {
    const config = templateFor(base, region);
    mutate?.(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

afterEach(() => {
    // Restored to delegating rather than cleared, or a synth in a later test reads nothing.
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("the shipped configuration still validates", () => {
    test("the commercial template passes getConfig() unchanged", () => {
        expect(loadConfig(commercialTemplate, "us-east-1")).not.toThrow();
    });

    test("the govcloud template passes getConfig() unchanged", () => {
        expect(loadConfig(govcloudTemplate, "us-gov-west-1")).not.toThrow();
    });

    test("the eusovereign template passes getConfig() unchanged", () => {
        expect(loadConfig(eusovereignTemplate, "eusc-de-east-1")).not.toThrow();
    });
});

describe("configuration shape", () => {
    test.each(SHIPPED_TEMPLATES)(
        "%s carries the disabled block with every leaf present",
        (file) => {
            const json = JSON.parse(realReadFileSync(path.join(CONFIG_DIR, file), "utf8"));
            expect(json.app.addons.useEdhWorkstations).toEqual(DISABLED_BLOCK);
        }
    );

    test("config.json carries every leaf of the block", () => {
        // config.json is the deployment's working copy and is switched on for a smoke test now and
        // then, so its key shape is asserted rather than the disabled values.
        const json = JSON.parse(realReadFileSync(path.join(CONFIG_DIR, "config.json"), "utf8"));
        expect(leafPaths(json.app.addons.useEdhWorkstations)).toEqual(leafPaths(DISABLED_BLOCK));
    });

    test("a config.json written before the block existed resolves the disabled defaults", () => {
        const config = loadConfig(commercialTemplate, "us-east-1", (c) => {
            delete c.app.addons.useEdhWorkstations;
        })();
        expect(config.app.addons.useEdhWorkstations).toEqual(DISABLED_BLOCK);
    });

    test("a block missing later-added leaves is backfilled field by field", () => {
        // Sub-objects present but partial: every absent member is filled by its own guard.
        const partial = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.addons.useEdhWorkstations = {
                enabled: false,
                edhApiEndpoint: "",
                edhClusterId: "",
                vdi: { defaultProject: "default" },
                sessionBootstrap: { enabled: false },
                predefinedImages: { registrationRevision: 1 },
                containers: { enabled: false },
            };
        })();
        expect(partial.app.addons.useEdhWorkstations).toEqual(DISABLED_BLOCK);

        // Sub-objects absent altogether: each is filled whole.
        const bare = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.addons.useEdhWorkstations = {
                enabled: false,
                edhApiEndpoint: "",
                edhClusterId: "",
            };
        })();
        expect(bare.app.addons.useEdhWorkstations).toEqual(DISABLED_BLOCK);
    });
});

/** A complete Secrets Manager ARN in the given partition, suffix included. */
const secretArn = (partition: string, name = "vams/edh/root-key") =>
    `arn:${partition}:secretsmanager:us-east-1:123456789012:secret:${name}-AbCdEf`;

/**
 * Enables the add-on with everything else it requires already valid. The commercial template ships
 * without a VPC, and the add-on requires one, so the VPC is switched on here too.
 */
const enableEdhConfig = (c: any, partition = "aws") => {
    c.app.useGlobalVpc.enabled = true;
    const edh = c.app.addons.useEdhWorkstations;
    edh.enabled = true;
    edh.edhApiEndpoint = "https://internal-edh-123.us-east-1.elb.amazonaws.com";
    edh.edhAdminApiKeySecretArn = secretArn(partition);
    edh.edhClusterId = "edh-dev";
    edh.vdi.virtualDesktopProfileId = 1;
    return edh;
};

/** Commercial template, add-on enabled and valid, then `mutate` applied to the EDH block. */
const withEdh = (mutate?: (edh: any, c: any) => void) =>
    loadConfig(commercialTemplate, "us-east-1", (c) => {
        const edh = enableEdhConfig(c);
        mutate?.(edh, c);
    });

describe("enabling the add-on", () => {
    test("a fully configured commercial deployment is accepted", () => {
        expect(withEdh()).not.toThrow();
    });

    test("a fully configured GovCloud deployment is accepted", () => {
        expect(
            loadConfig(govcloudTemplate, "us-gov-west-1", (c) => enableEdhConfig(c, "aws-us-gov"))
        ).not.toThrow();
    });

    test("the EU Sovereign Cloud partition is rejected by name", () => {
        expect(
            loadConfig(eusovereignTemplate, "eusc-de-east-1", (c) => enableEdhConfig(c, "aws-eusc"))
        ).toThrow(/EDH Workstations is not available in the 'aws-eusc' partition/);
    });

    test("without a VPC the add-on is named in the VPC error", () => {
        expect(
            withEdh((edh, c) => {
                c.app.useGlobalVpc.enabled = false;
            })
        ).toThrow(/require a VPC: addons.useEdhWorkstations\./);
    });
});

describe("edhApiEndpoint", () => {
    test("is required", () => {
        expect(withEdh((edh) => (edh.edhApiEndpoint = ""))).toThrow(
            /requires edhApiEndpoint when enabled/
        );
    });

    test("must parse as a URL", () => {
        expect(withEdh((edh) => (edh.edhApiEndpoint = "edh-alb.corp.internal"))).toThrow(
            /edhApiEndpoint must be a valid URL/
        );
    });

    test("a plain http endpoint is rejected, naming the root key", () => {
        expect(withEdh((edh) => (edh.edhApiEndpoint = "http://edh.corp.internal"))).toThrow(
            /edhApiEndpoint must use https, or the EDH root key/
        );
    });

    test.each([
        ["https://169.254.169.254"],
        ["https://127.0.0.1"],
        ["https://localhost"],
        ["https://edh.localhost"],
        ["https://[::1]"],
    ])("a loopback or link-local host is rejected: %s", (endpoint) => {
        expect(withEdh((edh) => (edh.edhApiEndpoint = endpoint))).toThrow(
            /edhApiEndpoint resolves to a loopback or link-local address/
        );
    });

    test.each([
        ["https://edh.corp.internal"],
        ["https://edh.corp.local"],
        ["https://10.0.0.5"],
        ["https://192.168.20.7:8443"],
        ["https://edh-alb.internal.example.com:8443"],
        ["https://172.16.4.4"],
        ["https://internal-edh-123.us-gov-west-1.elb.amazonaws.com"],
    ])("an in-network host is accepted: %s", (endpoint) => {
        // The outbound validator rejects every one of these; this endpoint is the internal EDH load
        // balancer, so private space is where it lives.
        expect(withEdh((edh) => (edh.edhApiEndpoint = endpoint))).not.toThrow();
    });
});

describe("secret ARNs", () => {
    test("the root key secret ARN is required", () => {
        expect(withEdh((edh) => (edh.edhAdminApiKeySecretArn = ""))).toThrow(
            /requires edhAdminApiKeySecretArn when enabled/
        );
    });

    test("a non-Secrets-Manager ARN is rejected", () => {
        expect(
            withEdh(
                (edh) =>
                    (edh.edhAdminApiKeySecretArn =
                        "arn:aws:kms:us-east-1:123456789012:key/11111111-2222-3333-4444-555555555555")
            )
        ).toThrow(/edhAdminApiKeySecretArn must be a valid AWS Secrets Manager secret ARN/);
    });

    test("a partition other than the deployment's is rejected", () => {
        expect(withEdh((edh) => (edh.edhAdminApiKeySecretArn = secretArn("aws-us-gov")))).toThrow(
            /edhAdminApiKeySecretArn uses partition 'aws-us-gov' which does not match the deployment partition 'aws'/
        );
    });

    test("a partial ARN without the 6-character suffix is rejected", () => {
        expect(
            withEdh(
                (edh) =>
                    (edh.edhAdminApiKeySecretArn =
                        "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams/edh/root-key")
            )
        ).toThrow(/edhAdminApiKeySecretArn must be the complete secret ARN/);
    });

    test("the CA bundle ARN is optional and, when set, held to the same rule", () => {
        // A name whose last hyphenated segment is itself six alphanumerics (`ca-bundle`) is
        // indistinguishable from a suffixed name, for this validator and for
        // `Secret.fromSecretCompleteArn` alike, so the fixture name ends in a shorter segment.
        expect(withEdh((edh) => (edh.edhApiCaBundleSecretArn = ""))).not.toThrow();
        expect(
            withEdh(
                (edh) => (edh.edhApiCaBundleSecretArn = secretArn("aws", "vams/edh/ca-bundle-pem"))
            )
        ).not.toThrow();
        expect(
            withEdh(
                (edh) =>
                    (edh.edhApiCaBundleSecretArn =
                        "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams/edh/ca-bundle-pem")
            )
        ).toThrow(/edhApiCaBundleSecretArn must be the complete secret ARN/);
    });

    test("the certificate server name is optional, needs the CA bundle and is a DNS name", () => {
        expect(withEdh((edh) => (edh.edhApiTlsServerName = ""))).not.toThrow();
        expect(
            withEdh((edh) => {
                edh.edhApiCaBundleSecretArn = secretArn("aws", "vams/edh/ca-bundle-pem");
                edh.edhApiTlsServerName = "EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE";
            })
        ).not.toThrow();
        // Against the public trust store the name would accept any public certificate issued for it.
        expect(
            withEdh((edh) => {
                edh.edhApiCaBundleSecretArn = "";
                edh.edhApiTlsServerName = "EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE";
            })
        ).toThrow(/edhApiTlsServerName requires edhApiCaBundleSecretArn/);
        expect(
            withEdh((edh) => {
                edh.edhApiCaBundleSecretArn = secretArn("aws", "vams/edh/ca-bundle-pem");
                edh.edhApiTlsServerName = "edh alb";
            })
        ).toThrow(/edhApiTlsServerName must be a DNS name/);
    });
});

describe("cluster id, instance types, VDI and registration revision", () => {
    test("edhClusterId is required and may not be blank", () => {
        expect(withEdh((edh) => (edh.edhClusterId = "   "))).toThrow(
            /requires edhClusterId when enabled/
        );
    });

    test("edhClusterId fits the resource tag key it becomes the suffix of", () => {
        expect(withEdh((edh) => (edh.edhClusterId = "edh*1"))).toThrow(
            /edhClusterId is the suffix of the edh:visibility:<edhClusterId> resource tag key/
        );
        expect(withEdh((edh) => (edh.edhClusterId = "c".repeat(114)))).toThrow(
            /edhClusterId is the suffix of the edh:visibility:<edhClusterId> resource tag key/
        );
        expect(withEdh((edh) => (edh.edhClusterId = "c".repeat(113)))).not.toThrow();
        expect(withEdh((edh) => (edh.edhClusterId = "edh-t1_prod.v2:eu/a=b+c@d"))).not.toThrow();
    });

    test("an instance type entry must be family.size", () => {
        expect(
            withEdh((edh) => (edh.edhAllowedInstanceTypes = ["g5.2xlarge", "G5 xlarge"]))
        ).toThrow(/edhAllowedInstanceTypes\[1\] is not an EC2 instance type/);
    });

    test("hyphenated families, hyphenated sizes and an empty list are accepted", () => {
        expect(
            withEdh(
                (edh) =>
                    (edh.edhAllowedInstanceTypes = [
                        "g4dn.xlarge",
                        "p4d.24xlarge",
                        "u-6tb1.56xlarge",
                        "c7i.metal-24xl",
                    ])
            )
        ).not.toThrow();
        expect(withEdh((edh) => (edh.edhAllowedInstanceTypes = []))).not.toThrow();
    });

    test("the serialized list is capped at 2048 bytes", () => {
        const many = Array.from({ length: 200 }, (_, i) => `g5.${i}xlarge`);
        expect(JSON.stringify(many).length).toBeGreaterThan(2048); // the case is a real overflow
        expect(withEdh((edh) => (edh.edhAllowedInstanceTypes = many))).toThrow(
            /edhAllowedInstanceTypes serializes to more than 2048 bytes/
        );
    });

    test("vdi.defaultProject and vdi.virtualDesktopProfileId are checked whenever the add-on is enabled", () => {
        expect(withEdh((edh) => (edh.vdi.defaultProject = ""))).toThrow(
            /vdi.defaultProject must be a non-empty string when the EDH Workstations add-on is enabled/
        );
        expect(withEdh((edh) => (edh.vdi.virtualDesktopProfileId = 0))).toThrow(
            /vdi.virtualDesktopProfileId must be a positive integer when the EDH Workstations add-on is enabled/
        );
        expect(withEdh((edh) => (edh.vdi.virtualDesktopProfileId = 2.5))).toThrow(
            /vdi.virtualDesktopProfileId must be a positive integer/
        );
        // The templates ship virtualDesktopProfileId 0: accepted while the add-on is off, so enabling
        // it without choosing a profile fails here rather than at the first launch.
        expect(loadConfig(commercialTemplate, "us-east-1")).not.toThrow();
    });

    test("a vdi.enabled key is refused by name, add-on on or off", () => {
        expect(withEdh((edh) => (edh.vdi.enabled = true))).toThrow(
            /vdi\.enabled is not a configuration field/
        );
        expect(
            loadConfig(commercialTemplate, "us-east-1", (c) => {
                c.app.addons.useEdhWorkstations.vdi.enabled = false;
            })
        ).toThrow(/vdi\.enabled is not a configuration field/);
    });

    test("predefinedImages.registrationRevision must be a non-negative integer", () => {
        expect(withEdh((edh) => (edh.predefinedImages.registrationRevision = -1))).toThrow(
            /registrationRevision must be a non-negative integer/
        );
        expect(withEdh((edh) => (edh.predefinedImages.registrationRevision = 0))).not.toThrow();
    });
});

describe("vdi.lease", () => {
    test("leaseHours holds to 1-720 whole hours", () => {
        expect(withEdh((edh) => (edh.vdi.lease.leaseHours = 1))).not.toThrow();
        expect(withEdh((edh) => (edh.vdi.lease.leaseHours = 720))).not.toThrow();
        expect(withEdh((edh) => (edh.vdi.lease.leaseHours = 0))).toThrow(/leaseHours/);
        expect(withEdh((edh) => (edh.vdi.lease.leaseHours = 721))).toThrow(/leaseHours/);
        expect(withEdh((edh) => (edh.vdi.lease.leaseHours = 2.5))).toThrow(/leaseHours/);
    });

    test("graceHours holds to 0-168 whole hours", () => {
        expect(withEdh((edh) => (edh.vdi.lease.graceHours = 0))).not.toThrow();
        expect(withEdh((edh) => (edh.vdi.lease.graceHours = 168))).not.toThrow();
        expect(withEdh((edh) => (edh.vdi.lease.graceHours = -1))).toThrow(/graceHours/);
        expect(withEdh((edh) => (edh.vdi.lease.graceHours = 999))).toThrow(/graceHours/);
        expect(withEdh((edh) => (edh.vdi.lease.graceHours = 0.5))).toThrow(/graceHours/);
    });

    test("a configuration without the lease block resolves the defaults", () => {
        const absent = withEdh((edh) => delete edh.vdi.lease)();
        expect(absent.app.addons.useEdhWorkstations.vdi.lease).toEqual(LEASE_DEFAULTS);

        // A block carrying one field keeps it and takes the default for the other three.
        const partial = withEdh((edh) => (edh.vdi.lease = { leaseHours: 12 }))();
        expect(partial.app.addons.useEdhWorkstations.vdi.lease).toEqual({
            ...LEASE_DEFAULTS,
            leaseHours: 12,
        });
    });

    test("lease.enabled must be a boolean", () => {
        expect(withEdh((edh) => (edh.vdi.lease.enabled = "false"))).toThrow(
            /lease\.enabled must be a boolean/
        );
    });

    test("lease.forced must be a boolean", () => {
        expect(withEdh((edh) => (edh.vdi.lease.forced = "true"))).toThrow(
            /lease\.forced must be a boolean/
        );
    });

    test("is not validated while the add-on is disabled", () => {
        expect(
            loadConfig(commercialTemplate, "us-east-1", (c) => {
                c.app.addons.useEdhWorkstations.vdi.lease.leaseHours = 0;
                c.app.addons.useEdhWorkstations.vdi.lease.graceHours = 999;
            })
        ).not.toThrow();
    });
});

describe("containers.enabled while the add-on is disabled", () => {
    test("is normalized to false with a warning", () => {
        const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            const config = loadConfig(commercialTemplate, "us-east-1", (c) => {
                c.app.addons.useEdhWorkstations.containers.enabled = true;
            })();
            expect(config.app.addons.useEdhWorkstations.containers.enabled).toBe(false);
            expect(warn).toHaveBeenCalledWith(
                expect.stringMatching(
                    /useEdhWorkstations.containers.enabled is ignored because useEdhWorkstations.enabled is false/
                )
            );
        } finally {
            warn.mockRestore();
        }
    });

    test("is left true when the add-on is enabled", () => {
        const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            const config = withEdh((edh) => (edh.containers.enabled = true))();
            expect(config.app.addons.useEdhWorkstations.containers.enabled).toBe(true);
            expect(warn).not.toHaveBeenCalledWith(
                expect.stringMatching(/containers.enabled is ignored/)
            );
        } finally {
            warn.mockRestore();
        }
    });
});

describe("validateInternalHttpsEndpoint", () => {
    test("is exported and differs from the outbound validator on private space", () => {
        expect(typeof EdhConfig.validateInternalHttpsEndpoint).toBe("function");
        expect(() =>
            EdhConfig.validateInternalHttpsEndpoint("https://10.0.0.5", "x.endpoint")
        ).not.toThrow();
        expect(() =>
            EdhConfig.validateInternalHttpsEndpoint("https://169.254.169.254", "x.endpoint")
        ).toThrow(/x.endpoint resolves to a loopback or link-local address \(169.254.169.254\)/);
    });
});

describe("validateEdhSecretCompleteArn", () => {
    test("names the config path in every message", () => {
        expect(() =>
            EdhConfig.validateEdhSecretCompleteArn("arn:aws:s3:::bucket", "x.arn", "aws")
        ).toThrow(/^Configuration Error: x.arn must be a valid AWS Secrets Manager secret ARN/);
        expect(() =>
            EdhConfig.validateEdhSecretCompleteArn(secretArn("aws-us-gov"), "x.arn", "aws")
        ).toThrow(/x.arn uses partition 'aws-us-gov'/);
        expect(() =>
            EdhConfig.validateEdhSecretCompleteArn(secretArn("aws"), "x.arn", "aws")
        ).not.toThrow();
    });
});

describe("predefinedImages entries", () => {
    test("valid VDI images and simulation AMIs are accepted", () => {
        expect(
            withEdh((edh) => {
                edh.predefinedImages.vdi = [
                    VDI_IMAGE,
                    {
                        ...VDI_IMAGE,
                        stackName: "vams-windows",
                        launchTenancy: "default",
                        description: "Windows",
                    },
                ];
                edh.predefinedImages.simulationAmis = [
                    SIMULATION_AMI,
                    { ...SIMULATION_AMI, stackName: "vams-isaac-sim-2", targetNodeUserDataId: 2 },
                ];
            })
        ).not.toThrow();
    });

    test("an AMI id is checked with the index in the message", () => {
        expect(
            withEdh(
                (edh) =>
                    (edh.predefinedImages.vdi = [
                        VDI_IMAGE,
                        { ...VDI_IMAGE, stackName: "b", amiId: "ami-XYZ" },
                    ])
            )
        ).toThrow(/predefinedImages\.vdi\[1\]\.amiId must be an AMI id/);
        expect(
            withEdh(
                (edh) => (edh.predefinedImages.simulationAmis = [{ ...SIMULATION_AMI, amiId: "" }])
            )
        ).toThrow(/predefinedImages\.simulationAmis\[0\]\.amiId must be an AMI id/);
    });

    test("stackName, baseOs and the numeric fields are checked", () => {
        expect(
            withEdh((edh) => (edh.predefinedImages.vdi = [{ ...VDI_IMAGE, stackName: "" }]))
        ).toThrow(/predefinedImages\.vdi\[0\]\.stackName must be a non-empty string/);
        expect(
            withEdh((edh) => (edh.predefinedImages.vdi = [{ ...VDI_IMAGE, baseOs: "" }]))
        ).toThrow(/predefinedImages\.vdi\[0\]\.baseOs must be a non-empty string/);
        expect(
            withEdh((edh) => (edh.predefinedImages.vdi = [{ ...VDI_IMAGE, rootSize: 0 }]))
        ).toThrow(/predefinedImages\.vdi\[0\]\.rootSize must be a positive integer/);
        expect(
            withEdh(
                (edh) =>
                    (edh.predefinedImages.vdi = [{ ...VDI_IMAGE, virtualDesktopProfileId: "1" }])
            )
        ).toThrow(/predefinedImages\.vdi\[0\]\.virtualDesktopProfileId must be a positive integer/);
        expect(
            withEdh(
                (edh) =>
                    (edh.predefinedImages.simulationAmis = [
                        { ...SIMULATION_AMI, targetNodeProfileId: -2 },
                    ])
            )
        ).toThrow(
            /predefinedImages\.simulationAmis\[0\]\.targetNodeProfileId must be a positive integer/
        );
        expect(
            withEdh(
                (edh) =>
                    (edh.predefinedImages.simulationAmis = [
                        { ...SIMULATION_AMI, targetNodeUserDataId: 0 },
                    ])
            )
        ).toThrow(
            /predefinedImages\.simulationAmis\[0\]\.targetNodeUserDataId must be a positive integer/
        );
    });

    test("a stackName declared twice is rejected", () => {
        expect(
            withEdh((edh) => (edh.predefinedImages.vdi = [VDI_IMAGE, { ...VDI_IMAGE }]))
        ).toThrow(
            /predefinedImages\.vdi\[1\]\.stackName 'vams-linux-desktop' is declared more than once/
        );
    });
});

describe("containers entries", () => {
    const withContainers = (mutate: (containers: any) => void) =>
        withEdh((edh) => {
            edh.containers.enabled = true;
            edh.containers.buildAndPushImages = [BUILD_IMAGE];
            edh.containers.existingEcrImages = [ECR_IMAGE];
            edh.containers.jobDefinitions = [
                JOB_DEFINITION,
                {
                    ...JOB_DEFINITION,
                    name: "edh-infer",
                    imageRef: ECR_IMAGE.imageUri,
                    gpu: 1,
                    platform: "EC2",
                },
            ];
            mutate(edh.containers);
        });

    test("a valid container configuration is accepted", () => {
        expect(withContainers(() => undefined)).not.toThrow();
    });

    test("a build image name is a lowercase repository-safe token, declared once", () => {
        expect(
            withContainers((c) => (c.buildAndPushImages = [{ ...BUILD_IMAGE, name: "Sim Runner" }]))
        ).toThrow(
            /containers\.buildAndPushImages\[0\]\.name must be lowercase letters, digits and hyphens/
        );
        expect(withContainers((c) => (c.buildAndPushImages = [BUILD_IMAGE, BUILD_IMAGE]))).toThrow(
            /containers\.buildAndPushImages\[1\]\.name 'sim-runner' is declared more than once/
        );
    });

    test("dockerfileDir must be repository-relative, without '..', and exist as a directory", () => {
        expect(
            withContainers(
                (c) => (c.buildAndPushImages = [{ ...BUILD_IMAGE, dockerfileDir: "/tmp/build" }])
            )
        ).toThrow(
            /containers\.buildAndPushImages\[0\]\.dockerfileDir must be relative to the repository root, not absolute/
        );
        expect(
            withContainers(
                (c) => (c.buildAndPushImages = [{ ...BUILD_IMAGE, dockerfileDir: "C:\\build" }])
            )
        ).toThrow(/dockerfileDir must be relative to the repository root, not absolute/);
        expect(
            withContainers(
                (c) =>
                    (c.buildAndPushImages = [{ ...BUILD_IMAGE, dockerfileDir: "infra/../backend" }])
            )
        ).toThrow(/dockerfileDir must not contain '\.\.' path segments/);
        expect(
            withContainers(
                (c) =>
                    (c.buildAndPushImages = [
                        { ...BUILD_IMAGE, dockerfileDir: "infra/config/does-not-exist" },
                    ])
            )
        ).toThrow(
            /dockerfileDir 'infra\/config\/does-not-exist' is not a directory under the repository root/
        );
        expect(
            withContainers(
                (c) =>
                    (c.buildAndPushImages = [
                        { ...BUILD_IMAGE, dockerfileDir: "infra/config/config.ts" },
                    ])
            )
        ).toThrow(
            /dockerfileDir 'infra\/config\/config.ts' is not a directory under the repository root/
        );
    });

    test("directory existence is checked only while containers are enabled", () => {
        // The lists are still shape-checked when disabled; a missing directory is a build-time
        // concern that does not exist until the build resources do.
        expect(
            withEdh((edh) => {
                edh.containers.enabled = false;
                edh.containers.buildAndPushImages = [
                    { ...BUILD_IMAGE, dockerfileDir: "infra/config/does-not-exist" },
                ];
            })
        ).not.toThrow();
        expect(
            withEdh((edh) => {
                edh.containers.enabled = false;
                edh.containers.buildAndPushImages = [{ ...BUILD_IMAGE, dockerfileDir: "/abs" }];
            })
        ).toThrow(/dockerfileDir must be relative to the repository root, not absolute/);
    });

    test("a build platform outside the supported set is rejected", () => {
        expect(
            withContainers(
                (c) => (c.buildAndPushImages = [{ ...BUILD_IMAGE, platform: "windows/amd64" }])
            )
        ).toThrow(
            /containers\.buildAndPushImages\[0\]\.platform must be one of linux\/amd64, linux\/arm64/
        );
    });

    test("an existing ECR image needs a real, placeholder-free ECR URI and a display name", () => {
        expect(
            withContainers(
                (c) =>
                    (c.existingEcrImages = [
                        {
                            ...ECR_IMAGE,
                            imageUri:
                                "<ACCOUNTID>.dkr.ecr.<REGION>.amazonaws.com/<ECR-REPOSITORY>:<IMAGE-TAG>",
                        },
                    ])
            )
        ).toThrow(
            /containers\.existingEcrImages\[0\]\.imageUri still contains the template placeholder\(s\) <ACCOUNTID>, <REGION>, <ECR-REPOSITORY>, <IMAGE-TAG>/
        );
        expect(
            withContainers(
                (c) =>
                    (c.existingEcrImages = [
                        { ...ECR_IMAGE, imageUri: "docker.io/library/python:3.12" },
                    ])
            )
        ).toThrow(/containers\.existingEcrImages\[0\]\.imageUri must be an Amazon ECR image URI/);
        expect(
            withContainers((c) => (c.existingEcrImages = [{ ...ECR_IMAGE, displayName: "" }]))
        ).toThrow(/containers\.existingEcrImages\[0\]\.displayName must be a non-empty string/);
        expect(withContainers((c) => (c.existingEcrImages = [ECR_IMAGE, ECR_IMAGE]))).toThrow(
            /containers\.existingEcrImages\[1\]\.imageUri '.*' is declared more than once/
        );
    });

    test("a job definition imageRef must name a build image or an existing image", () => {
        expect(
            withContainers((c) => (c.jobDefinitions = [{ ...JOB_DEFINITION, imageRef: "missing" }]))
        ).toThrow(
            /containers\.jobDefinitions\[0\]\.imageRef 'missing' does not name a containers\.buildAndPushImages\[\]\.name or a containers\.existingEcrImages\[\]\.imageUri/
        );
    });

    test("a job definition name begins with edh-", () => {
        for (const name of ["train", "EDH-train", "sim-edh-train"]) {
            expect(
                withContainers((c) => (c.jobDefinitions = [{ ...JOB_DEFINITION, name }]))
            ).toThrow(
                new RegExp(
                    `containers\\.jobDefinitions\\[0\\]\\.name '${name}' must begin with 'edh-'\\. ` +
                        "EDH submits container jobs under its controller role"
                )
            );
        }
    });

    test("job definition names are unique and the numeric fields are checked", () => {
        expect(
            withContainers((c) => (c.jobDefinitions = [JOB_DEFINITION, JOB_DEFINITION]))
        ).toThrow(/containers\.jobDefinitions\[1\]\.name 'edh-train' is declared more than once/);
        expect(
            withContainers((c) => (c.jobDefinitions = [{ ...JOB_DEFINITION, vcpus: 0 }]))
        ).toThrow(/containers\.jobDefinitions\[0\]\.vcpus must be a positive integer/);
        expect(
            withContainers((c) => (c.jobDefinitions = [{ ...JOB_DEFINITION, memory: 1.5 }]))
        ).toThrow(/containers\.jobDefinitions\[0\]\.memory must be a positive integer/);
        expect(
            withContainers((c) => (c.jobDefinitions = [{ ...JOB_DEFINITION, gpu: -1 }]))
        ).toThrow(/containers\.jobDefinitions\[0\]\.gpu must be a non-negative integer/);
        expect(
            withContainers((c) => (c.jobDefinitions = [{ ...JOB_DEFINITION, platform: "LAMBDA" }]))
        ).toThrow(/containers\.jobDefinitions\[0\]\.platform must be one of EC2, FARGATE/);
    });
});

describe("exported per-entry validators", () => {
    test("validateEdhJobDefinitions resolves imageRef against both lists", () => {
        expect(() =>
            EdhConfig.validateEdhJobDefinitions(
                [JOB_DEFINITION],
                "x.jobDefinitions",
                [BUILD_IMAGE],
                []
            )
        ).not.toThrow();
        expect(() =>
            EdhConfig.validateEdhJobDefinitions(
                [{ ...JOB_DEFINITION, imageRef: ECR_IMAGE.imageUri }],
                "x.jobDefinitions",
                [],
                [ECR_IMAGE]
            )
        ).not.toThrow();
        expect(() =>
            EdhConfig.validateEdhJobDefinitions([JOB_DEFINITION], "x.jobDefinitions", [], [])
        ).toThrow(/x\.jobDefinitions\[0\]\.imageRef 'sim-runner' does not name/);
    });

    test("validateEdhBuildImages skips the directory check without a repository root", () => {
        expect(() =>
            EdhConfig.validateEdhBuildImages(
                [{ ...BUILD_IMAGE, dockerfileDir: "nope/nope" }],
                "x.build"
            )
        ).not.toThrow();
        expect(() =>
            EdhConfig.validateEdhBuildImages(
                [{ ...BUILD_IMAGE, dockerfileDir: "nope/nope" }],
                "x.build",
                path.join(__dirname, "..", "..", "..", "..")
            )
        ).toThrow(
            /x\.build\[0\]\.dockerfileDir 'nope\/nope' is not a directory under the repository root/
        );
    });

    test("every list validator accepts an empty list", () => {
        expect(() => EdhConfig.validateEdhPredefinedVdiImages([], "x")).not.toThrow();
        expect(() => EdhConfig.validateEdhSimulationAmis([], "x")).not.toThrow();
        expect(() => EdhConfig.validateEdhBuildImages([], "x")).not.toThrow();
        expect(() => EdhConfig.validateEdhExistingEcrImages([], "x")).not.toThrow();
        expect(() => EdhConfig.validateEdhJobDefinitions([], "x", [], [])).not.toThrow();
    });
});

describe("sessionBootstrap", () => {
    const ROLE_ARN = "arn:aws:iam::123456789012:role/edh-workstation-instance";
    const withBootstrap = (mutate?: (bootstrap: any) => void) =>
        withEdh((edh) => {
            edh.sessionBootstrap.enabled = true;
            edh.sessionBootstrap.workstationInstanceRoleArns = [ROLE_ARN];
            mutate?.(edh.sessionBootstrap);
        });

    test("a valid bootstrap configuration is accepted", () => {
        expect(withBootstrap()).not.toThrow();
    });

    test("only the secret name prefix is checked while bootstrap is disabled", () => {
        expect(
            withEdh((edh) => {
                edh.sessionBootstrap.enabled = false;
                edh.sessionBootstrap.sessionKeyTtlMinutes = 0;
                edh.sessionBootstrap.workstationInstanceRoleArns = [];
            })
        ).not.toThrow();
        // The prefix scopes the reconciler's PutSecretValue and DeleteSecret grant whatever the flag
        // says, so an empty or malformed prefix (which would grant them on every secret) is refused.
        for (const prefix of ["no-trailing-slash", ""]) {
            expect(
                withEdh((edh) => {
                    edh.sessionBootstrap.enabled = false;
                    edh.sessionBootstrap.secretNamePrefix = prefix;
                })
            ).toThrow(/sessionBootstrap.secretNamePrefix must be a Secrets Manager name prefix/);
        }
    });

    test("the secret name prefix must not cover the root key or CA bundle secret", () => {
        for (const enabled of [false, true]) {
            expect(
                withEdh((edh) => {
                    edh.sessionBootstrap.enabled = enabled;
                    edh.sessionBootstrap.workstationInstanceRoleArns = [
                        "arn:aws:iam::123456789012:role/EdhDesktopInstanceRole",
                    ];
                    edh.edhAdminApiKeySecretArn = secretArn("aws");
                    edh.sessionBootstrap.secretNamePrefix = "vams/";
                })
            ).toThrow(/must not cover the edhAdminApiKeySecretArn secret/);
        }
        expect(
            withEdh((edh) => {
                edh.edhAdminApiKeySecretArn = secretArn("aws");
                edh.sessionBootstrap.secretNamePrefix = "vams/edh-session/";
            })
        ).not.toThrow();
    });

    test("the key TTL is an integer of at least 1 minute", () => {
        expect(withBootstrap((b) => (b.sessionKeyTtlMinutes = 0))).toThrow(
            /sessionBootstrap.sessionKeyTtlMinutes must be an integer of at least 1/
        );
        expect(withBootstrap((b) => (b.sessionKeyTtlMinutes = 30.5))).toThrow(
            /sessionBootstrap.sessionKeyTtlMinutes must be an integer of at least 1/
        );
    });

    test("the secret name prefix ends with '/' and uses Secrets Manager name characters", () => {
        expect(withBootstrap((b) => (b.secretNamePrefix = "vams/edh-session"))).toThrow(
            /sessionBootstrap.secretNamePrefix must be a Secrets Manager name prefix ending in '\/'/
        );
        expect(withBootstrap((b) => (b.secretNamePrefix = "vams/edh session/"))).toThrow(
            /sessionBootstrap.secretNamePrefix must be a Secrets Manager name prefix/
        );
        expect(
            withBootstrap((b) => (b.secretNamePrefix = "team-a/vams_edh.sessions/"))
        ).not.toThrow();
    });

    test("the secret name prefix is at most 128 characters", () => {
        // The shipped Linux recipes' session bootstrap component resolves the prefix into a document
        // the 16,000-character component limit applies to.
        const longest = `${"a".repeat(127)}/`;
        expect(withBootstrap((b) => (b.secretNamePrefix = longest))).not.toThrow();
        expect(withBootstrap((b) => (b.secretNamePrefix = `a${longest}`))).toThrow(
            /sessionBootstrap.secretNamePrefix must be at most 128 characters/
        );
    });

    test("at least one workstation instance role ARN is required", () => {
        expect(withBootstrap((b) => (b.workstationInstanceRoleArns = []))).toThrow(
            /sessionBootstrap.workstationInstanceRoleArns must list at least one IAM role ARN/
        );
    });

    test("each role ARN is an IAM role in the deployment partition, indexed in the message", () => {
        expect(
            withBootstrap(
                (b) =>
                    (b.workstationInstanceRoleArns = [
                        ROLE_ARN,
                        "arn:aws:iam::123456789012:user/alice",
                    ])
            )
        ).toThrow(/sessionBootstrap.workstationInstanceRoleArns\[1\] must be an IAM role ARN/);
        expect(
            withBootstrap(
                (b) =>
                    (b.workstationInstanceRoleArns = [
                        "arn:aws-us-gov:iam::123456789012:role/edh-workstation-instance",
                    ])
            )
        ).toThrow(
            /sessionBootstrap.workstationInstanceRoleArns\[0\] uses partition 'aws-us-gov' which does not match the deployment partition 'aws'/
        );
    });

    test("a role name with a wildcard or whitespace is rejected; a path is accepted", () => {
        // IAM rejects a wildcard inside a policy Principal, so the CMK key policy would fail the deploy.
        expect(
            withBootstrap(
                (b) => (b.workstationInstanceRoleArns = ["arn:aws:iam::123456789012:role/*"])
            )
        ).toThrow(/sessionBootstrap.workstationInstanceRoleArns\[0\] must be an IAM role ARN/);
        expect(
            withBootstrap(
                (b) => (b.workstationInstanceRoleArns = ["arn:aws:iam::123456789012:role/edh ws"])
            )
        ).toThrow(
            /workstationInstanceRoleArns\[0\] must be an IAM role ARN .*the path and name use only letters, digits and _\+=,\.@\/- \./
        );
        expect(
            withBootstrap(
                (b) =>
                    (b.workstationInstanceRoleArns = [
                        "arn:aws:iam::123456789012:role/edh/desktops/instance-role+=,.@_-1",
                    ])
            )
        ).not.toThrow();
    });
});

describe("ConfigBuilder mirror", () => {
    /** The builder's commercial preset with the add-on enabled and valid, then `mutate`. */
    const builderConfig = (mutate?: (c: any) => void) => {
        const c = makeDefaultConfig("commercial");
        c.env.region = "us-east-1";
        enableEdhConfig(c);
        mutate?.(c);
        return c;
    };
    const ids = (c: any) => evaluateRules(c).map((r) => r.id);
    const edhIds = (c: any) =>
        ids(c).filter((id) => id.startsWith("edh-") || id === "vpc-required-edh-workstations");

    test("[control] a valid enabled configuration raises no EDH rule", () => {
        expect(edhIds(builderConfig())).toEqual([]);
    });

    test("the VPC requirement is mirrored with the config.ts label", () => {
        const rules = evaluateRules(builderConfig((c) => (c.app.useGlobalVpc.enabled = false)));
        const rule = rules.find((r) => r.id === "vpc-required-edh-workstations");
        expect(rule?.message).toMatch(/require a VPC: addons\.useEdhWorkstations\./);
    });

    test("the partition rule fires for an EU Sovereign region and stays silent without a region", () => {
        // The secret ARN is moved into the region's partition so only the partition rule is under test.
        const inPartition = (region: string, partition: string) => (c: any) => {
            c.env.region = region;
            c.app.addons.useEdhWorkstations.edhAdminApiKeySecretArn = secretArn(partition);
        };
        expect(edhIds(builderConfig(inPartition("eusc-de-east-1", "aws-eusc")))).toEqual([
            "edh-partition",
        ]);
        expect(edhIds(builderConfig(inPartition("us-gov-west-1", "aws-us-gov")))).toEqual([]);
        expect(edhIds(builderConfig((c) => (c.env.region = "")))).toEqual([]);
    });

    test("the cluster id rules mirror config.ts", () => {
        expect(
            edhIds(builderConfig((c) => (c.app.addons.useEdhWorkstations.edhClusterId = "")))
        ).toEqual(["edh-clusterid-required"]);
        expect(
            edhIds(builderConfig((c) => (c.app.addons.useEdhWorkstations.edhClusterId = "edh*1")))
        ).toEqual(["edh-clusterid-format"]);
        expect(
            edhIds(
                builderConfig(
                    (c) => (c.app.addons.useEdhWorkstations.edhClusterId = "c".repeat(114))
                )
            )
        ).toEqual(["edh-clusterid-format"]);
    });

    test("endpoint rules mirror the internal validator", () => {
        expect(
            edhIds(builderConfig((c) => (c.app.addons.useEdhWorkstations.edhApiEndpoint = "")))
        ).toEqual(["edh-apiendpoint-required"]);
        expect(
            edhIds(
                builderConfig(
                    (c) =>
                        (c.app.addons.useEdhWorkstations.edhApiEndpoint =
                            "http://edh.corp.internal")
                )
            )
        ).toEqual(["edh-apiendpoint-https"]);
        expect(
            edhIds(
                builderConfig(
                    (c) =>
                        (c.app.addons.useEdhWorkstations.edhApiEndpoint = "https://169.254.169.254")
                )
            )
        ).toEqual(["edh-apiendpoint-loopback"]);
        expect(
            edhIds(
                builderConfig(
                    (c) => (c.app.addons.useEdhWorkstations.edhApiEndpoint = "https://10.0.0.5")
                )
            )
        ).toEqual([]);
    });

    test("secret ARN rules mirror the complete-ARN and partition checks", () => {
        expect(
            edhIds(
                builderConfig(
                    (c) =>
                        (c.app.addons.useEdhWorkstations.edhAdminApiKeySecretArn =
                            "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams/edh/root-key")
                )
            )
        ).toEqual(["edh-adminkey-secretarn-format"]);
        expect(
            edhIds(
                builderConfig(
                    (c) =>
                        (c.app.addons.useEdhWorkstations.edhAdminApiKeySecretArn =
                            secretArn("aws-us-gov"))
                )
            )
        ).toEqual(["edh-adminkey-secretarn-partition"]);
    });

    test("list entry rules fire once per list", () => {
        expect(
            edhIds(
                builderConfig((c) => {
                    const edh = c.app.addons.useEdhWorkstations;
                    edh.predefinedImages.vdi = [{ ...VDI_IMAGE, amiId: "nope" }];
                    edh.containers.jobDefinitions = [{ ...JOB_DEFINITION, imageRef: "missing" }];
                })
            )
        ).toEqual(["edh-predefined-vdi-entries", "edh-jobdefinition-entries"]);
    });

    test("the job definition entry rule fires on a name without the edh- prefix", () => {
        const withJob = (name: string) =>
            builderConfig((c) => {
                const containers = c.app.addons.useEdhWorkstations.containers;
                containers.buildAndPushImages = [BUILD_IMAGE];
                containers.jobDefinitions = [{ ...JOB_DEFINITION, name }];
            });
        expect(edhIds(withJob("edh-train"))).toEqual([]);
        expect(edhIds(withJob("train"))).toEqual(["edh-jobdefinition-entries"]);
    });

    test("session bootstrap rules mirror config.ts", () => {
        expect(
            edhIds(
                builderConfig((c) => {
                    c.app.addons.useEdhWorkstations.sessionBootstrap.enabled = true;
                    c.app.addons.useEdhWorkstations.sessionBootstrap.sessionKeyTtlMinutes = 0;
                })
            )
        ).toEqual(["edh-sessionbootstrap-ttl", "edh-sessionbootstrap-rolearns-required"]);
        const withRoles = (roles: string[]) =>
            builderConfig((c) => {
                c.app.addons.useEdhWorkstations.sessionBootstrap.enabled = true;
                c.app.addons.useEdhWorkstations.sessionBootstrap.workstationInstanceRoleArns =
                    roles;
            });
        expect(edhIds(withRoles(["arn:aws:iam::123456789012:role/*"]))).toEqual([
            "edh-sessionbootstrap-rolearns-format",
        ]);
        expect(
            edhIds(withRoles(["arn:aws:iam::123456789012:role/edh/desktops/instance-role"]))
        ).toEqual([]);
    });

    // One firing case per remaining rule id. validation.ts is outside configBuilderSync's reach, so a
    // predicate that silently stops firing is caught only by a case that expects it to fire.
    const edh = (c: any) => c.app.addons.useEdhWorkstations;
    test.each<[string, (c: any) => void, string[]]>([
        [
            "edh-apiendpoint-url",
            (c) => (edh(c).edhApiEndpoint = "not a url"),
            ["edh-apiendpoint-url"],
        ],
        [
            "edh-adminkey-secretarn-required",
            (c) => (edh(c).edhAdminApiKeySecretArn = ""),
            ["edh-adminkey-secretarn-required"],
        ],
        [
            "edh-cabundle-secretarn-format",
            (c) =>
                (edh(c).edhApiCaBundleSecretArn =
                    "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams/edh/ca"),
            ["edh-cabundle-secretarn-format"],
        ],
        [
            "edh-cabundle-secretarn-partition",
            (c) => (edh(c).edhApiCaBundleSecretArn = secretArn("aws-us-gov", "vams/edh/ca")),
            ["edh-cabundle-secretarn-partition"],
        ],
        [
            "edh-instancetypes-pattern",
            (c) => (edh(c).edhAllowedInstanceTypes = ["g5.2xlarge", "G5 xlarge"]),
            ["edh-instancetypes-pattern"],
        ],
        [
            "edh-instancetypes-pattern accepts hyphenated families and sizes",
            (c) => (edh(c).edhAllowedInstanceTypes = ["u-6tb1.56xlarge", "c7i.metal-24xl"]),
            [],
        ],
        [
            "edh-instancetypes-size",
            (c) =>
                (edh(c).edhAllowedInstanceTypes = Array.from(
                    { length: 200 },
                    (_, i) => `g5.${i}xlarge`
                )),
            ["edh-instancetypes-size"],
        ],
        [
            "edh-vdi-defaultproject-required",
            (c) => (edh(c).vdi.defaultProject = ""),
            ["edh-vdi-defaultproject-required"],
        ],
        [
            "edh-vdi-profileid",
            (c) => (edh(c).vdi.virtualDesktopProfileId = 0),
            ["edh-vdi-profileid"],
        ],
        [
            "edh-lease-enabled-boolean",
            (c) => (edh(c).vdi.lease.enabled = "false"),
            ["edh-lease-enabled-boolean"],
        ],
        [
            "edh-lease-forced-boolean",
            (c) => (edh(c).vdi.lease.forced = "true"),
            ["edh-lease-forced-boolean"],
        ],
        [
            "edh-lease-hours-range",
            (c) => (edh(c).vdi.lease.leaseHours = 0),
            ["edh-lease-hours-range"],
        ],
        [
            "edh-lease-grace-range",
            (c) => (edh(c).vdi.lease.graceHours = 999),
            ["edh-lease-grace-range"],
        ],
        [
            "edh-registration-revision",
            (c) => (edh(c).predefinedImages.registrationRevision = -1),
            ["edh-registration-revision"],
        ],
        [
            "edh-sessionbootstrap-prefix",
            (c) => {
                edh(c).sessionBootstrap.enabled = true;
                edh(c).sessionBootstrap.secretNamePrefix = "vams/edh-session";
                edh(c).sessionBootstrap.workstationInstanceRoleArns = [
                    "arn:aws:iam::123456789012:role/edh-desktop-instance",
                ];
            },
            ["edh-sessionbootstrap-prefix"],
        ],
        [
            "edh-sessionbootstrap-prefix-length",
            (c) => {
                edh(c).sessionBootstrap.enabled = true;
                edh(c).sessionBootstrap.secretNamePrefix = `${"a".repeat(128)}/`;
                edh(c).sessionBootstrap.workstationInstanceRoleArns = [
                    "arn:aws:iam::123456789012:role/edh-desktop-instance",
                ];
            },
            ["edh-sessionbootstrap-prefix-length"],
        ],
        [
            "edh-sessionbootstrap-prefix-length accepts a prefix of 128 characters",
            (c) => {
                edh(c).sessionBootstrap.enabled = true;
                edh(c).sessionBootstrap.secretNamePrefix = `${"a".repeat(127)}/`;
                edh(c).sessionBootstrap.workstationInstanceRoleArns = [
                    "arn:aws:iam::123456789012:role/edh-desktop-instance",
                ];
            },
            [],
        ],
        [
            "edh-simulation-ami-entries",
            (c) =>
                (edh(c).predefinedImages.simulationAmis = [{ ...SIMULATION_AMI, amiId: "nope" }]),
            ["edh-simulation-ami-entries"],
        ],
        [
            "edh-build-image-entries",
            (c) =>
                (edh(c).containers.buildAndPushImages = [
                    { ...BUILD_IMAGE, dockerfileDir: "../outside/the/repository" },
                ]),
            ["edh-build-image-entries"],
        ],
        [
            "edh-existing-ecr-entries",
            (c) =>
                (edh(c).containers.existingEcrImages = [
                    { ...ECR_IMAGE, imageUri: "<ACCOUNTID>.dkr.ecr.us-east-1.amazonaws.com/x:1" },
                ]),
            ["edh-existing-ecr-entries"],
        ],
        [
            "edh-containers-codebuild-partition",
            (c) => {
                // The warning exists only outside the CodeBuild partitions, where the partition rule
                // fires as well; the secret ARN follows the Region so nothing else does.
                c.env.region = "eusc-de-east-1";
                edh(c).edhAdminApiKeySecretArn = secretArn("aws-eusc");
                edh(c).containers.enabled = true;
                edh(c).containers.buildAndPushImages = [BUILD_IMAGE];
            },
            ["edh-partition", "edh-containers-codebuild-partition"],
        ],
        [
            "edh-shipped-recipes-no-session-bootstrap-warn",
            (c) => (edh(c).amiBuilds.enabled = true),
            ["edh-shipped-recipes-no-session-bootstrap-warn"],
        ],
        [
            "edh-vdi-enabled-not-a-field",
            (c) => (edh(c).vdi.enabled = true),
            ["edh-vdi-enabled-not-a-field"],
        ],
        [
            "edh-vdi-enabled-not-a-field fires with the add-on disabled, for any value",
            (c) => {
                edh(c).enabled = false;
                edh(c).vdi.enabled = false;
            },
            ["edh-vdi-enabled-not-a-field"],
        ],
        [
            "edh-tlsservername-requires-cabundle",
            (c) => (edh(c).edhApiTlsServerName = "EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE"),
            ["edh-tlsservername-requires-cabundle"],
        ],
        [
            "edh-tlsservername-format",
            (c) => {
                edh(c).edhApiCaBundleSecretArn = secretArn("aws", "vams/edh/ca");
                edh(c).edhApiTlsServerName = "edh_alb.internal";
            },
            ["edh-tlsservername-format"],
        ],
        [
            "edh-amibuilds-requires-addon",
            (c) => {
                edh(c).enabled = false;
                edh(c).amiBuilds.enabled = true;
            },
            ["edh-amibuilds-requires-addon"],
        ],
        [
            "edh-amibuilds-enabled-boolean",
            (c) => {
                // Shipped recipes off, so the truthy string does not also raise the shipped-recipes warning.
                edh(c).amiBuilds.enabled = "true";
                edh(c).amiBuilds.includeShippedRecipes = false;
            },
            ["edh-amibuilds-enabled-boolean"],
        ],
        [
            "edh-amibuilds-shippedrecipes-boolean",
            (c) => (edh(c).amiBuilds.includeShippedRecipes = "true"),
            ["edh-amibuilds-shippedrecipes-boolean"],
        ],
        [
            "edh-amibuilds-timeout-range",
            (c) => (edh(c).amiBuilds.buildTimeoutMinutes = 29),
            ["edh-amibuilds-timeout-range"],
        ],
        [
            "edh-amibuilds-keepversions-range",
            (c) => (edh(c).amiBuilds.keepVersionsDefault = 21),
            ["edh-amibuilds-keepversions-range"],
        ],
        [
            "edh-amibuilds-subnet-format",
            (c) => (edh(c).amiBuilds.buildSubnetId = "vpc-0123456789abcdef0"),
            ["edh-amibuilds-subnet-format"],
        ],
        [
            "edh-amirecipes-entries accepts a relative directory it does not read",
            (c) =>
                (edh(c).predefinedImages.amiRecipes = [
                    { recipeDir: "backendAddons/edh/amiRecipes/isaacsim", buildOnDeploy: true },
                ]),
            [],
        ],
        [
            "edh-amirecipes-entries rejects an absolute recipeDir",
            (c) =>
                (edh(c).predefinedImages.amiRecipes = [
                    { recipeDir: "/opt/recipes/desktop", buildOnDeploy: false },
                ]),
            ["edh-amirecipes-entries"],
        ],
        [
            "edh-amirecipes-entries rejects a drive-letter recipeDir",
            (c) =>
                (edh(c).predefinedImages.amiRecipes = [
                    { recipeDir: "C:\\recipes\\desktop", buildOnDeploy: false },
                ]),
            ["edh-amirecipes-entries"],
        ],
        [
            "edh-amirecipes-entries rejects a '..' segment",
            (c) =>
                (edh(c).predefinedImages.amiRecipes = [
                    { recipeDir: "recipes/../outside", buildOnDeploy: false },
                ]),
            ["edh-amirecipes-entries"],
        ],
        [
            "edh-amirecipes-entries rejects a duplicate after separator and trailing-slash normalization",
            (c) =>
                (edh(c).predefinedImages.amiRecipes = [
                    { recipeDir: "recipes/desktop", buildOnDeploy: false },
                    { recipeDir: "recipes\\desktop\\", buildOnDeploy: true },
                ]),
            ["edh-amirecipes-entries"],
        ],
        [
            "edh-amirecipes-entries rejects an entry without buildOnDeploy",
            (c) => (edh(c).predefinedImages.amiRecipes = [{ recipeDir: "recipes/desktop" }]),
            ["edh-amirecipes-entries"],
        ],
        [
            "edh-amirecipes-entries rejects a list that is not an array",
            (c) =>
                (edh(c).predefinedImages.amiRecipes = {
                    recipeDir: "recipes/desktop",
                    buildOnDeploy: false,
                }),
            ["edh-amirecipes-entries"],
        ],
    ])("%s", (_, mutate, expected) => {
        expect(edhIds(builderConfig(mutate))).toEqual(expected);
    });

    test("applyDerived forces containers.enabled off while the add-on is disabled", () => {
        const c = makeDefaultConfig("commercial");
        c.app.addons.useEdhWorkstations.containers.enabled = true;
        const { config, changes } = applyDerived(c);
        expect(config.app.addons.useEdhWorkstations.containers.enabled).toBe(false);
        expect(changes).toEqual([
            expect.objectContaining({
                path: "app.addons.useEdhWorkstations.containers.enabled",
                to: false,
            }),
        ]);
        // The input is not mutated; the builder relies on structural sharing.
        expect(c.app.addons.useEdhWorkstations.containers.enabled).toBe(true);
    });

    test("applyDerived leaves an enabled add-on's containers alone", () => {
        const c = builderConfig(
            (cfg) => (cfg.app.addons.useEdhWorkstations.containers.enabled = true)
        );
        const { config, changes } = applyDerived(c);
        expect(config.app.addons.useEdhWorkstations.containers.enabled).toBe(true);
        expect(changes).toEqual([]);
    });
});

describe("feature flags pushed by the core stack", () => {
    /**
     * Flag-row custom resources whose putItem payload carries `flag` as the featureName value.
     * `Create` is the call JSON-encoded as a string and assembled through Fn::Join, so it is
     * flattened before matching; re-stringifying the resource would escape the payload's quotes.
     */
    const flagRows = (result: ReturnType<typeof synthTemplate>, flag: string) =>
        result.where("Custom::AWS", (r) =>
            SynthResult.flatten(r.properties.Create).includes(`"S":"${flag}"`)
        );

    // Set directly on the template: the synth harness builds a Config without getConfig().
    const edhOn = (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        c.app.addons.useEdhWorkstations.enabled = true;
        c.app.addons.useEdhWorkstations.edhApiEndpoint =
            "https://internal-edh-123.us-east-1.elb.amazonaws.com";
        c.app.addons.useEdhWorkstations.edhAdminApiKeySecretArn = secretArn("aws");
        c.app.addons.useEdhWorkstations.edhClusterId = "edh-dev";
        c.app.addons.useEdhWorkstations.vdi.virtualDesktopProfileId = 1;
    };
    const edhAndContainersOn = (c: any) => {
        edhOn(c);
        c.app.addons.useEdhWorkstations.containers.enabled = true;
    };

    test("the stock commercial template publishes neither EDH flag", () => {
        const stock = synthTemplate("commercial");
        // Control: a flag the commercial template does publish is found by the same query.
        expectAbsent("EDH flag rows", flagRows(stock, "EDH_WORKSTATIONS"), {
            description: "CLOUDFRONTDEPLOY flag row on the stock commercial template",
            count: flagRows(stock, "CLOUDFRONTDEPLOY").length,
        });
        expect(flagRows(stock, "EDH_CONTAINERS")).toEqual([]);
    });

    test("enabling the add-on publishes EDH_WORKSTATIONS and not EDH_CONTAINERS", () => {
        const result = synthTemplate("commercial", { mutate: edhOn, mutateKey: "edh-on" });
        expect(flagRows(result, "EDH_WORKSTATIONS")).toHaveLength(1);
        expect(flagRows(result, "EDH_CONTAINERS")).toEqual([]);
    });

    test("enabling containers as well publishes both flags", () => {
        const result = synthTemplate("commercial", {
            mutate: edhAndContainersOn,
            mutateKey: "edh-and-containers-on",
        });
        expect(flagRows(result, "EDH_WORKSTATIONS")).toHaveLength(1);
        expect(flagRows(result, "EDH_CONTAINERS")).toHaveLength(1);
    });
});

describe("containers.imagePullRoleArns", () => {
    const PULL_ROLE_ARN = "arn:aws:iam::123456789012:role/edh-batch-execution";
    const withPullRoles = (roles: unknown) =>
        withEdh((edh) => {
            edh.containers.enabled = true;
            edh.containers.imagePullRoleArns = roles;
        });

    test("an empty list is the default and is accepted", () => {
        const config = withEdh((edh) => {
            delete edh.containers.imagePullRoleArns;
        })();
        expect(config.app.addons.useEdhWorkstations.containers.imagePullRoleArns).toEqual([]);
        expect(withPullRoles([])).not.toThrow();
    });

    test("role ARNs in the deployment partition are accepted", () => {
        expect(
            withPullRoles([
                PULL_ROLE_ARN,
                "arn:aws:iam::123456789012:role/service-role/edh-ecs-task-execution",
            ])
        ).not.toThrow();
    });

    test("a value that is not a list is rejected", () => {
        expect(withPullRoles(PULL_ROLE_ARN)).toThrow(
            /containers.imagePullRoleArns must be a list of IAM role ARNs/
        );
    });

    test("each entry is an IAM role ARN in the deployment partition, indexed in the message", () => {
        expect(withPullRoles([PULL_ROLE_ARN, "arn:aws:iam::123456789012:user/alice"])).toThrow(
            /containers.imagePullRoleArns\[1\] must be an IAM role ARN/
        );
        expect(
            withPullRoles(["arn:aws-us-gov:iam::123456789012:role/edh-batch-execution"])
        ).toThrow(
            /containers.imagePullRoleArns\[0\] uses partition 'aws-us-gov' which does not match the deployment partition 'aws'/
        );
    });

    test("entries are checked while the containers feature is off", () => {
        // A malformed ARN is rejected on its own; enabling the feature later cannot make it valid.
        expect(
            withEdh((edh) => {
                edh.containers.enabled = false;
                edh.containers.imagePullRoleArns = ["not-an-arn"];
            })
        ).toThrow(/containers.imagePullRoleArns\[0\] must be an IAM role ARN/);
    });

    test("a role name with a wildcard is rejected", () => {
        // IAM rejects a wildcard inside a policy Principal, so the repository policy would fail at CREATE.
        expect(withPullRoles(["arn:aws:iam::123456789012:role/*"])).toThrow(
            /containers.imagePullRoleArns\[0\] must be an IAM role ARN/
        );
        expect(withPullRoles([PULL_ROLE_ARN, "arn:aws:iam::123456789012:role/edh-?"])).toThrow(
            /containers.imagePullRoleArns\[1\] must be an IAM role ARN/
        );
    });
});

describe("ConfigBuilder mirror: containers.imagePullRoleArns", () => {
    /** The builder's commercial preset with the add-on enabled and valid, then `mutate`. */
    const builderConfig = (mutate?: (c: any) => void) => {
        const c = makeDefaultConfig("commercial");
        c.env.region = "us-east-1";
        enableEdhConfig(c);
        mutate?.(c);
        return c;
    };
    const edhIds = (c: any) =>
        evaluateRules(c)
            .map((r) => r.id)
            .filter((id) => id.startsWith("edh-"));
    const setRoles = (roles: unknown) => (c: any) => {
        c.app.addons.useEdhWorkstations.containers.imagePullRoleArns = roles;
    };

    test("[control] a valid role list raises no EDH rule", () => {
        expect(
            edhIds(builderConfig(setRoles(["arn:aws:iam::123456789012:role/edh-batch-execution"])))
        ).toEqual([]);
    });

    test("a malformed or foreign-partition entry raises the format rule", () => {
        expect(edhIds(builderConfig(setRoles(["not-an-arn"])))).toEqual([
            "edh-containers-imagepullrolearns-format",
        ]);
        expect(
            edhIds(
                builderConfig(
                    setRoles(["arn:aws-us-gov:iam::123456789012:role/edh-batch-execution"])
                )
            )
        ).toEqual(["edh-containers-imagepullrolearns-format"]);
        expect(edhIds(builderConfig(setRoles(["arn:aws:iam::123456789012:role/*"])))).toEqual([
            "edh-containers-imagepullrolearns-format",
        ]);
    });
});

describe("amiBuilds and amiRecipes", () => {
    /** Build an enabled EDH config and run it through getConfig(). */
    const enabledEdhConfig = () =>
        loadConfig(commercialTemplate, "us-east-1", (c) => enableEdhConfig(c));
    /** Apply mutate to an enabled config and run getConfig(). */
    const getConfigFrom = (mutate: (c: any) => void) => {
        const getConf = loadConfig(commercialTemplate, "us-east-1", (c) => {
            enableEdhConfig(c);
            mutate(c);
        });
        return getConf();
    };

    test("a config written before the block existed resolves the disabled defaults", () => {
        const c = enabledEdhConfig();
        delete (c().app.addons.useEdhWorkstations as any).amiBuilds;
        delete (c().app.addons.useEdhWorkstations.predefinedImages as any).amiRecipes;
        const resolved = c();
        expect(resolved.app.addons.useEdhWorkstations.amiBuilds).toEqual({
            enabled: false,
            includeShippedRecipes: true,
            buildSubnetId: "",
            buildTimeoutMinutes: 180,
            keepVersionsDefault: 3,
        });
        expect(resolved.app.addons.useEdhWorkstations.predefinedImages.amiRecipes).toEqual([]);
    });

    test("buildTimeoutMinutes and keepVersionsDefault are bounded integers", () => {
        expect(() =>
            getConfigFrom((c) => {
                c.app.addons.useEdhWorkstations.amiBuilds.enabled = true;
                c.app.addons.useEdhWorkstations.amiBuilds.buildTimeoutMinutes = 5;
            })
        ).toThrow(/amiBuilds\.buildTimeoutMinutes must be an integer between 30 and 720/);
        expect(() =>
            getConfigFrom((c) => {
                c.app.addons.useEdhWorkstations.amiBuilds.enabled = true;
                c.app.addons.useEdhWorkstations.amiBuilds.buildTimeoutMinutes = 180;
                c.app.addons.useEdhWorkstations.amiBuilds.keepVersionsDefault = 0;
            })
        ).toThrow(/amiBuilds\.keepVersionsDefault must be an integer between 1 and 20/);
    });

    test("a build subnet id, when set, is a subnet id", () => {
        expect(() =>
            getConfigFrom((c) => {
                c.app.addons.useEdhWorkstations.amiBuilds.enabled = true;
                c.app.addons.useEdhWorkstations.amiBuilds.buildSubnetId = "vpc-0123";
            })
        ).toThrow(/amiBuilds\.buildSubnetId must be a subnet id/);
    });

    test("amiBuilds requires the add-on", () => {
        expect(() =>
            getConfigFrom((c) => {
                c.app.addons.useEdhWorkstations.enabled = false;
                c.app.addons.useEdhWorkstations.amiBuilds.enabled = true;
            })
        ).toThrow(/amiBuilds\.enabled requires app\.addons\.useEdhWorkstations\.enabled/);
    });

    test("a recipe reference must be a relative directory holding a valid recipe.json", () => {
        expect(() =>
            validateEdhAmiRecipes([{ recipeDir: "/abs/dir", buildOnDeploy: false }], "x.amiRecipes")
        ).toThrow(/x\.amiRecipes\[0\]\.recipeDir must be relative/);
        expect(() =>
            validateEdhAmiRecipes([{ recipeDir: "a/../b", buildOnDeploy: false }], "x.amiRecipes")
        ).toThrow(/must not contain '\.\.'/);
        expect(() =>
            validateEdhAmiRecipes(
                [{ recipeDir: "backendAddons/edh/amiRecipes/nope", buildOnDeploy: false }],
                "x.amiRecipes",
                REPO
            )
        ).toThrow(/recipe\.json/);
        expect(() =>
            validateEdhAmiRecipes(
                [{ recipeDir: "backendAddons/edh", buildOnDeploy: "yes" as any }],
                "x.amiRecipes"
            )
        ).toThrow(/buildOnDeploy must be a boolean/);
    });

    test("the shipped recipes load and validate", () => {
        for (const dir of EDH_SHIPPED_AMI_RECIPE_DIRS) {
            const loaded = loadEdhAmiRecipe(REPO, dir, false);
            expect(loaded.recipeId).toBe(path.basename(dir));
            expect(loaded.componentFiles.length).toBeGreaterThan(0);
        }
        expect(() =>
            validateEdhAmiRecipes(
                EDH_SHIPPED_AMI_RECIPE_DIRS.map((recipeDir) => ({
                    recipeDir,
                    buildOnDeploy: false,
                })),
                "x",
                REPO
            )
        ).not.toThrow();
    });

    test("the shipped recipe directories are the ones on disk", () => {
        const parent = "backendAddons/edh/amiRecipes";
        expect(EDH_SHIPPED_AMI_RECIPE_DIRS).toEqual([
            `${parent}/al2023-desktop`,
            `${parent}/windows2022-desktop`,
            `${parent}/isaacsim`,
        ]);
        const onDisk = fs
            .readdirSync(path.join(REPO, parent), { withFileTypes: true })
            .filter((entry) => entry.isDirectory())
            .map((entry) => `${parent}/${entry.name}`);
        expect([...EDH_SHIPPED_AMI_RECIPE_DIRS].sort()).toEqual(onDisk.sort());
    });

    test("every shipped recipe file is LF, as .gitattributes checks it out", () => {
        // The seeded content hash covers the component bytes, so a CRLF copy hashes differently from
        // the LF one a checkout writes, and the recipe would version anew with no change of content.
        const parent = path.join(REPO, "backendAddons/edh/amiRecipes");
        const files = (fs.readdirSync(parent, { recursive: true }) as string[])
            .map((relative) => path.join(parent, relative))
            .filter((file) => fs.statSync(file).isFile());
        expect(files.length).toBeGreaterThan(0);
        const withCarriageReturns = files
            .filter((file) => (realReadFileSync(file) as Buffer).includes(0x0d))
            .map((file) => path.relative(REPO, file).replace(/\\/g, "/"));
        expect(withCarriageReturns).toEqual([]);
    });

    // Asset and parameter rules live in loadEdhAmiRecipe, so a manifest is served from a stub while
    // the component files it names still resolve against a real shipped recipe directory.
    function loadWithManifest(overrides: Record<string, unknown>): EdhConfig.LoadedEdhAmiRecipe {
        const dir = "backendAddons/edh/amiRecipes/al2023-desktop";
        const real = JSON.parse(
            realReadFileSync(path.join(REPO, dir, "recipe.json"), "utf8") as string
        );
        (fs.readFileSync as jest.Mock).mockImplementation((file: string, ...rest: unknown[]) => {
            if (String(file).replace(/\\/g, "/").endsWith(`${dir}/recipe.json`)) {
                return JSON.stringify({ ...real, ...overrides });
            }
            return realReadFileSync(file, ...(rest as []));
        });
        try {
            return loadEdhAmiRecipe(REPO, dir, false);
        } finally {
            (fs.readFileSync as jest.Mock).mockImplementation(realReadFileSync);
        }
    }

    test("a recipe without assets loads with an empty asset list", () => {
        expect(loadWithManifest({ assets: undefined }).assets).toEqual([]);
    });

    test("a Windows recipe must be x86_64", () => {
        expect(() =>
            loadWithManifest({ osFamily: "windows", baseOs: "windows2022", architecture: "arm64" })
        ).toThrow(/a Windows recipe must be x86_64/);
        expect(() =>
            loadWithManifest({
                kind: "simulation",
                osFamily: "windows",
                baseOs: undefined,
                architecture: "arm64",
            })
        ).toThrow(/a Windows recipe must be x86_64/);
        expect(
            loadWithManifest({ osFamily: "windows", baseOs: "windows2022", architecture: "x86_64" })
                .manifest.architecture
        ).toBe("x86_64");
        expect(loadWithManifest({ architecture: "arm64" }).manifest.architecture).toBe("arm64");
    });

    test("parentImage is an AMI id, a public AMI parameter under ssm:/aws/service/ or an EDH stack", () => {
        for (const parentImage of [
            "ami-0123456789abcdef0",
            "ssm:/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id",
            "edhStack:VAMS AL2023 Desktop",
        ]) {
            expect(loadWithManifest({ parentImage }).manifest.parentImage).toBe(parentImage);
        }
        for (const parentImage of ["ssm:/team/golden-ami", "ssm:/aws/servicex/ami", "ami-0abc"]) {
            expect(() => loadWithManifest({ parentImage })).toThrow(
                "parentImage must be an AMI id, 'ssm:/aws/service/<parameter>' or 'edhStack:<stack name>'."
            );
        }
    });

    test("buildInstanceType is one family.size type or a list of up to eight distinct ones", () => {
        expect(
            loadWithManifest({ buildInstanceType: "m7i.large" }).manifest.buildInstanceType
        ).toBe("m7i.large");
        const fallbacks = ["m7i.xlarge", "m6i.xlarge", "m5.xlarge"];
        expect(
            loadWithManifest({ buildInstanceType: fallbacks }).manifest.buildInstanceType
        ).toEqual(fallbacks);
        for (const refused of [
            "large",
            [],
            ["m7i.large", "large"],
            ["m7i.large", "m7i.large"],
            Array.from({ length: 9 }, (_, n) => `c${n + 1}.large`),
            ["m7i.large", 5],
            ["m7i.large", `${"m".repeat(63)}.x`],
        ]) {
            expect(() => loadWithManifest({ buildInstanceType: refused })).toThrow(
                /buildInstanceType must be family\.size, or a list of 1-8 distinct family\.size instance types/
            );
        }
    });

    test("a declared asset must be an existing directory under tools/", () => {
        expect(
            loadWithManifest({ assets: [{ id: "vamscli", repoPath: "tools/VamsCLI" }] }).assets
        ).toEqual([{ id: "vamscli", repoPath: "tools/VamsCLI" }]);
        expect(() =>
            loadWithManifest({ assets: [{ id: "vamscli", repoPath: "backend" }] })
        ).toThrow(/assets\[0\]\.repoPath must start with one of: tools\//);
        expect(() =>
            loadWithManifest({ assets: [{ id: "vamscli", repoPath: "tools/../backend" }] })
        ).toThrow(/must not contain '\.\.'/);
        expect(() =>
            loadWithManifest({ assets: [{ id: "vamscli", repoPath: "/tools/VamsCLI" }] })
        ).toThrow(/must be relative to the repository root/);
        expect(() =>
            loadWithManifest({ assets: [{ id: "vamscli", repoPath: "tools/NotThere" }] })
        ).toThrow(/assets\[0\]\.repoPath 'tools\/NotThere' is not a directory/);
    });

    test("asset ids are patterned, unique and bounded", () => {
        expect(() =>
            loadWithManifest({ assets: [{ id: "VamsCli", repoPath: "tools/VamsCLI" }] })
        ).toThrow(/assets\[0\]\.id must match/);
        expect(() =>
            loadWithManifest({
                assets: [
                    { id: "vamscli", repoPath: "tools/VamsCLI" },
                    { id: "vamscli", repoPath: "tools/permissionsSetup" },
                ],
            })
        ).toThrow(/duplicate asset id 'vamscli'/);
        expect(() =>
            loadWithManifest({
                assets: Array.from({ length: 9 }, (_, i) => ({
                    id: `a${i}`,
                    repoPath: "tools/VamsCLI",
                })),
            })
        ).toThrow(/assets must list at most 8 entries/);
    });

    test("parameters are a bounded flat string map", () => {
        expect(() => loadWithManifest({ parameters: { isaacSimVersion: "5.1.0" } })).not.toThrow();
        expect(() => loadWithManifest({ parameters: { "bad-key": "x" } })).toThrow(
            /parameters key 'bad-key' must match/
        );
        expect(() => loadWithManifest({ parameters: { good: 5 as unknown as string } })).toThrow(
            /parameters\.good must be a string of at most 1024 characters/
        );
        expect(() => loadWithManifest({ parameters: { good: "x".repeat(1025) } })).toThrow(
            /parameters\.good must be a string of at most 1024 characters/
        );
        expect(() =>
            loadWithManifest({
                parameters: Object.fromEntries(
                    Array.from({ length: 17 }, (_, i) => [`k${i}`, "v"])
                ),
            })
        ).toThrow(/parameters must list at most 16 entries/);
        expect(() => loadWithManifest({ parameters: ["a"] as unknown as object })).toThrow(
            /parameters must be an object/
        );
    });

    test("resolveEdhAmiRecipes merges shipped recipes with operator overrides", () => {
        const config = enabledEdhConfig()();
        const edh = config.app.addons.useEdhWorkstations;
        edh.amiBuilds.enabled = true;
        edh.predefinedImages.amiRecipes = [
            { recipeDir: EDH_SHIPPED_AMI_RECIPE_DIRS[0], buildOnDeploy: true },
        ];
        const resolved = resolveEdhAmiRecipes(edh, REPO);
        expect(resolved.map((r) => r.recipeId).sort()).toEqual([
            "al2023-desktop",
            "isaacsim",
            "windows2022-desktop",
        ]);
        expect(resolved.find((r) => r.recipeId === "al2023-desktop")!.buildOnDeploy).toBe(true);
        expect(resolved.find((r) => r.recipeId === "windows2022-desktop")!.buildOnDeploy).toBe(
            false
        );
        edh.amiBuilds.includeShippedRecipes = false;
        expect(resolveEdhAmiRecipes(edh, REPO).map((r) => r.recipeId)).toEqual(["al2023-desktop"]);
        edh.amiBuilds.enabled = false;
        expect(resolveEdhAmiRecipes(edh, REPO)).toEqual([]);
    });

    test("seeding the shipped recipes without session bootstrap warns, naming both keys", () => {
        const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            getConfigFrom((c) => {
                c.app.addons.useEdhWorkstations.amiBuilds.enabled = true;
                c.app.addons.useEdhWorkstations.sessionBootstrap.enabled = false;
            });
            expect(warn).toHaveBeenCalledWith(
                expect.stringMatching(
                    /amiBuilds\.includeShippedRecipes seeds the Linux desktop recipes .* while .*\.sessionBootstrap\.enabled is false/
                )
            );
        } finally {
            warn.mockRestore();
        }
    });

    test("the warning is silent once session bootstrap is enabled", () => {
        const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            getConfigFrom((c) => {
                const edh = c.app.addons.useEdhWorkstations;
                edh.amiBuilds.enabled = true;
                edh.sessionBootstrap.enabled = true;
                edh.sessionBootstrap.workstationInstanceRoleArns = [
                    "arn:aws:iam::123456789012:role/edh-workstation-instance",
                ];
            });
            expect(warn).not.toHaveBeenCalledWith(
                expect.stringMatching(/includeShippedRecipes seeds the Linux desktop recipes/)
            );
        } finally {
            warn.mockRestore();
        }
    });

    test("two references to the same recipe id are rejected", () => {
        expect(() =>
            validateEdhAmiRecipes(
                [
                    { recipeDir: EDH_SHIPPED_AMI_RECIPE_DIRS[0], buildOnDeploy: false },
                    { recipeDir: EDH_SHIPPED_AMI_RECIPE_DIRS[0], buildOnDeploy: true },
                ],
                "x.amiRecipes",
                REPO
            )
        ).toThrow(/duplicate/);
    });
});

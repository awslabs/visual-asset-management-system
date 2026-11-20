/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` rules for the Miris Spatial Streaming add-on and its upload pipeline. Each rejection
 * asserts on a fragment of its message, since a Miris config can be invalid several ways at once, and
 * on the hand-ported ConfigBuilder rule, which `configBuilderSync.test.ts` does not cover.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import { MIRIS_STREAM_SCHEMA_EXTENSIONS } from "../../config/addon/mirisStreamingConfig";
import commercialTemplate from "../../config/config.template.commercial.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import mirisStreamSchema from "../../lib/nestedStacks/addon/miris/constructs/dynamodb-metadataschema-miris-stream.json";
import { newTestApp } from "../support/testApp";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";
import { MIRIS_STREAM_SCHEMA_EXTENSIONS as BUILDER_SCHEMA_EXTENSIONS } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/addon/mirisStreaming";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:test-miris-api-key-AbCdEf";
const VIEWER_KEY = "test-placeholder-viewer-key-000000";

const TEMPLATES = {
    commercial: { template: commercialTemplate, region: "us-east-1" },
    govcloud: { template: govcloudTemplate, region: "us-gov-west-1" },
    eusovereign: { template: eusovereignTemplate, region: "eusc-de-east-1" },
} as const;
type TemplateName = keyof typeof TEMPLATES;
type Mutate = (c: any) => void;

const UPLOAD_DEFAULTS = {
    enabled: false,
    autoRegisterWithVAMS: true,
    autoRegisterAutoTriggerOnFileUpload: false,
    triggerExtensions: ".usd,.usda,.usdc,.usdz",
    taskTimeoutSeconds: 1800,
    maxAssetSizeBytes: 5000000000,
};
const DEFAULTS = {
    enabled: false,
    viewerKeyMode: "perView",
    viewerKey: "",
    viewerKeyTtlMinutes: 15,
    apiKeySecretArn: "",
    mirisApiBaseUrl: "https://app.miris.com",
    upload: UPLOAD_DEFAULTS,
};

/** A config built from a template with the placeholders getConfig() requires filled. */
function templateFor(name: TemplateName, mutate: Mutate): any {
    const { template, region } = TEMPLATES[name];
    const config = JSON.parse(JSON.stringify(template));
    config.env.region = region;
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    }
    mutate(config);
    return config;
}

/** Serves the mutated template as config.json and returns a thunk calling getConfig(). */
function resolve(mutate: Mutate, template: TemplateName = "commercial"): () => Config.Config {
    const config = templateFor(template, mutate);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

/** The ConfigBuilder rule ids with `prefix` reported for the same config getConfig() is given. */
function builderRules(mutate: Mutate, prefix: string, template: TemplateName = "commercial") {
    return evaluateRules(templateFor(template, mutate))
        .map((rule) => rule.id)
        .filter((id) => id.startsWith(prefix));
}

function enableStatic(c: any) {
    c.app.webUi.allowUnsafeEvalFeatures = true;
    Object.assign(c.app.addons.useMirisStreaming, {
        enabled: true,
        viewerKeyMode: "static",
        viewerKey: VIEWER_KEY,
        apiKeySecretArn: SECRET_ARN,
    });
}

function enablePerView(c: any) {
    enableStatic(c);
    Object.assign(c.app.addons.useMirisStreaming, { viewerKeyMode: "perView", viewerKey: "" });
}

function enableUpload(c: any) {
    enableStatic(c);
    c.app.useGlobalVpc.enabled = true;
    c.app.addons.useMirisStreaming.upload.enabled = true;
}

/** Everything a restricted-partition template needs to pass getConfig() before the Miris rules. */
const restrictedDeployable: Mutate = (c) => (c.app.useGlobalVpc.enabled = true);

const both =
    (...mutates: Mutate[]): Mutate =>
    (c) =>
        mutates.forEach((m) => m(c));

/** Sets `field` (a path under useMirisStreaming) after `enable`. */
const set =
    (enable: Mutate, field: string, value: unknown): Mutate =>
    (c) => {
        enable(c);
        const keys = field.split(".");
        const parent = keys.slice(0, -1).reduce((o, k) => o[k], c.app.addons.useMirisStreaming);
        if (value === undefined) delete parent[keys[keys.length - 1]];
        else parent[keys[keys.length - 1]] = value;
    };

let warn: jest.SpyInstance;

beforeEach(() => {
    warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => {
    warn.mockRestore();
    // Restored to delegating rather than cleared, or a later test in this process reads nothing.
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

const mirisWarnings = () =>
    warn.mock.calls.map((args) => String(args[0])).filter((w) => w.includes("Miris"));

describe("defaults", () => {
    test.each(Object.keys(TEMPLATES) as TemplateName[])(
        "the %s template passes getConfig() with both Miris blocks disabled",
        (name) => {
            expect(resolve(() => undefined, name)().app.addons.useMirisStreaming).toEqual(DEFAULTS);
        }
    );

    test.each(["commercial", "govcloud", "eusovereign"] as const)(
        "the %s ConfigBuilder preset reports no Miris rule",
        (profile) => {
            const ids = evaluateRules(makeDefaultConfig(profile)).map((rule) => rule.id);
            expect(ids.filter((id) => id.startsWith("miris-"))).toEqual([]);
            expect(ids).not.toContain("vpc-required-miris-upload");
        }
    );

    test("each missing field is filled on its own", () => {
        const config = resolve((c) => {
            c.app.addons.useMirisStreaming = {
                enabled: false,
                viewerKeyTtlMinutes: 30,
                upload: { enabled: false, autoRegisterWithVAMS: false },
            };
        })();
        expect(config.app.addons.useMirisStreaming).toEqual({
            ...DEFAULTS,
            viewerKeyTtlMinutes: 30,
            upload: { ...UPLOAD_DEFAULTS, autoRegisterWithVAMS: false },
        });
    });

    test.each([
        ["viewerKeyTtlMinutes", null, 15],
        ["mirisApiBaseUrl", undefined, "https://app.miris.com"],
    ])("an enabled add-on with %s = %p gets %p", (field, value, expected) => {
        const config = resolve(set(enablePerView, field, value))();
        expect((config.app.addons.useMirisStreaming as any)[field]).toBe(expected);
    });

    test("an enabled add-on with no mode is perView: no viewer key is needed and a set one is ignored with a warning", () => {
        const withoutKey = resolve(
            set(set(enablePerView, "viewerKeyMode", undefined), "viewerKey", undefined)
        )();
        expect(withoutKey.app.addons.useMirisStreaming.viewerKeyMode).toBe("perView");
        expect(mirisWarnings()).toEqual([]);

        const withKey = set(set(enableStatic, "viewerKeyMode", undefined), "viewerKey", VIEWER_KEY);
        expect(resolve(withKey)().app.addons.useMirisStreaming.viewerKeyMode).toBe("perView");
        expect(mirisWarnings()).toEqual([
            expect.stringContaining('viewerKey is ignored when viewerKeyMode is "perView"'),
        ]);
        const config = makeDefaultConfig("commercial");
        withKey(config);
        expect(evaluateRules(config).map((rule) => rule.id)).toContain(
            "miris-perview-viewerkey-ignored-warn"
        );
    });

    test("the schema extension list matches the schema file and the ConfigBuilder", () => {
        expect(mirisStreamSchema.fileKeyTypeRestriction).toBe(UPLOAD_DEFAULTS.triggerExtensions);
        expect([...MIRIS_STREAM_SCHEMA_EXTENSIONS].join(",")).toBe(
            UPLOAD_DEFAULTS.triggerExtensions
        );
        expect(BUILDER_SCHEMA_EXTENSIONS).toEqual([...MIRIS_STREAM_SCHEMA_EXTENSIONS]);
    });

    test("a disabled add-on and pipeline with stale invalid fields are not validated", () => {
        const mutate: Mutate = (c) => {
            c.app.addons.useMirisStreaming = {
                enabled: false,
                viewerKeyMode: "dynamic",
                viewerKey: "short",
                viewerKeyTtlMinutes: 0,
                apiKeySecretArn: "not-an-arn",
                mirisApiBaseUrl: "http://169.254.169.254",
                upload: {
                    enabled: false,
                    triggerExtensions: ".glb",
                    taskTimeoutSeconds: -1,
                    maxAssetSizeBytes: 1.5,
                },
            };
        };
        expect(resolve(mutate)).not.toThrow();
        expect(mirisWarnings()).toEqual([]);
        expect(builderRules(mutate, "miris-")).toEqual([]);
    });
});

/** Enables what a field under useMirisStreaming needs, then sets it. */
const setField = (field: string, value: unknown) =>
    set(field.startsWith("upload.") ? enableUpload : enableStatic, field, value);

/** [field under useMirisStreaming, accepted values] */
const ACCEPTED_FIELDS: [string, unknown[]][] = [
    ["viewerKey", ["short-key"]],
    ["viewerKeyTtlMinutes", [1, 60]],
    ["apiKeySecretArn", [SECRET_ARN.replace("us-east-1", "us-west-2")]],
    ["upload.taskTimeoutSeconds", [1, 5_000_000_000]],
    ["upload.maxAssetSizeBytes", [1, 5_000_000_000]],
    ["upload.triggerExtensions", [".usd", ".usdz,.usd"]],
];

const ACCEPTED: [string, Mutate, string, TemplateName?][] = [
    ["static mode", enableStatic, "miris-"],
    ["perView mode", enablePerView, "miris-"],
    ["a restricted template with the add-on off", restrictedDeployable, "miris-", "govcloud"],
    ["the add-on alone, without the global VPC", enableStatic, "vpc-required-"],
    ["upload", enableUpload, "miris-"],
    ["upload with the global VPC", enableUpload, "vpc-"],
    ["upload in perView mode", both(enableUpload, enablePerView), "miris-"],
    ...ACCEPTED_FIELDS.flatMap(([field, values]) =>
        values.map((v): [string, Mutate, string] => [
            `${field} = ${JSON.stringify(v)}`,
            setField(field, v),
            "miris-",
        ])
    ),
];

test.each(ACCEPTED)("accepts %s", (_, mutate, prefix, template = "commercial") => {
    expect(resolve(mutate, template)).not.toThrow();
    expect(mirisWarnings()).toEqual([]);
    expect(builderRules(mutate, prefix, template)).toEqual([]);
});

/** [ConfigBuilder rule, field under useMirisStreaming, rejected values, message fragment] */
const REJECTED_FIELDS: [string, string, unknown[], string][] = [
    ["miris-viewerkeymode", "viewerKeyMode", ["dynamic", "perview", "Static"], '"perView"'],
    ["miris-viewerkey-ttl", "viewerKeyTtlMinutes", [0, 61, 15.5, "15", true], "from 1 to 60"],
    ["miris-secret-required", "apiKeySecretArn", ["", "UNDEFINED"], "requires apiKeySecretArn"],
    [
        "miris-secret-arn-format",
        "apiKeySecretArn",
        [
            // A partial ARN, without the suffix Secrets Manager appends, does not match the IAM grant.
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:miris-integration-key",
            "miris-integration-key",
            "arn:aws:secretsmanager:us-east-1:123456789012:miris-integration-key-AbCdEf",
            "arn:aws:s3:::miris-integration-key-AbCdEf",
            "arn:aws:secretsmanager:us-east-1:12345:secret:miris-integration-key-AbCdEf",
            "arn:aws-us-gov:secretsmanager:us-gov-west-1:123456789012:secret:miris/key-AbCdEf",
            "arn:awsfoo:secretsmanager:us-east-1:123456789012:secret:miris-integration-key-AbCdEf",
        ],
        "complete ARN of a Secrets Manager secret",
    ],
    ["miris-viewerkey-required", "viewerKey", ["", "UNDEFINED", "   "], "requires viewerKey"],
    ["miris-viewerkey-whitespace", "viewerKey", [" key", "key\n"], "trailing whitespace"],
    ["miris-baseurl-required", "mirisApiBaseUrl", ["", "UNDEFINED"], "requires mirisApiBaseUrl"],
    ["miris-baseurl-whitespace", "mirisApiBaseUrl", [" https://app.miris.com"], "whitespace"],
    ["miris-baseurl-url", "mirisApiBaseUrl", ["app.miris.com"], "must be a valid URL"],
    ["miris-baseurl-https", "mirisApiBaseUrl", ["http://app.miris.com"], "must use https"],
    ["miris-baseurl-private-host", "mirisApiBaseUrl", ["https://169.254.169.254"], "private"],
    ["miris-upload-task-timeout", "upload.taskTimeoutSeconds", [0, 1.5, "1800", true], "whole"],
    ["miris-upload-max-asset-size", "upload.maxAssetSizeBytes", [-1, 1.5], "positive whole"],
    [
        "miris-upload-trigger-extensions-whitespace",
        "upload.triggerExtensions",
        [" .usd , .usdz ", ".usd\t", "\n.usd"],
        "must not contain whitespace",
    ],
    [
        "miris-upload-trigger-extensions-format",
        "upload.triggerExtensions",
        ["", "usd", ".USD", ".usd,", ".usd,,.usdz", "*.usd", [".usd"], 5],
        "comma-separated list of lowercase file extensions",
    ],
    [
        "miris-upload-trigger-extensions-schema",
        "upload.triggerExtensions",
        [".glb", ".usd,.obj"],
        "Miris stream file metadata schema accepts",
    ],
];

/** [label, mutate, message fragment, ConfigBuilder rule, template] */
const REJECTED: [string, Mutate, string, string, TemplateName?][] = [
    ...(["govcloud", "eusovereign"] as const).map(
        (t): [string, Mutate, string, string, TemplateName] => [
            `the add-on in ${t}`,
            both(restrictedDeployable, enableStatic),
            "while deploying to the 'aws-",
            "miris-commercial-partition-only",
            t,
        ]
    ),
    [
        "the add-on without unsafe-eval",
        both(enableStatic, (c) => (c.app.webUi.allowUnsafeEvalFeatures = false)),
        "requires app.webUi.allowUnsafeEvalFeatures",
        "miris-unsafe-eval",
    ],
    [
        "the add-on with every Lambda function in the VPC",
        both(enableStatic, (c) => {
            c.app.useGlobalVpc.enabled = true;
            c.app.useGlobalVpc.useForAllLambdas = true;
            c.app.openSearch.useServerless.allowPublic = false;
        }),
        "cannot be combined with app.useGlobalVpc.useForAllLambdas",
        "miris-use-for-all-lambdas",
    ],
    [
        "static mode with no viewer key",
        set(setField("viewerKey", ""), "viewerKeyMode", "static"),
        '"static" requires viewerKey',
        "miris-viewerkey-required",
    ],
    [
        "upload without the add-on",
        set(enableUpload, "enabled", false),
        "upload requires app.addons.useMirisStreaming.enabled",
        "miris-upload-requires-addon",
    ],
    [
        "upload without the global VPC",
        both(enableUpload, (c) => (c.app.useGlobalVpc.enabled = false)),
        "require a VPC: addons.useMirisStreaming.upload.",
        "vpc-required-miris-upload",
    ],
    [
        "upload in an external VPC with no private subnet",
        both(enableUpload, (c) => {
            c.app.useGlobalVpc.optionalExternalVpcId = "vpc-0123456789abcdef0";
            c.app.useGlobalVpc.optionalExternalIsolatedSubnetIds = "subnet-0123456789abcdef0";
        }),
        "at least one private subnet ID",
        "miris-upload-external-vpc-private-subnets",
    ],
    ...REJECTED_FIELDS.flatMap(([rule, field, values, fragment]) =>
        values.map((v): [string, Mutate, string, string] => [
            `${field} = ${JSON.stringify(v)}`,
            setField(field, v),
            fragment,
            rule,
        ])
    ),
];

test.each(REJECTED)("rejects %s", (_, mutate, fragment, rule, template = "commercial") => {
    expect(resolve(mutate, template)).toThrow(fragment);
    expect(builderRules(mutate, rule.split("-")[0] + "-", template)).toEqual([rule]);
});

test.each([
    [
        "a viewer key in perView mode",
        set(enablePerView, "viewerKey", VIEWER_KEY),
        'viewerKey is ignored when viewerKeyMode is "perView"',
        "miris-perview-viewerkey-ignored-warn",
    ],
    [
        "an armed upload trigger without registration",
        set(
            setField("upload.autoRegisterAutoTriggerOnFileUpload", true),
            "upload.autoRegisterWithVAMS",
            false
        ),
        "autoRegisterAutoTriggerOnFileUpload is true but autoRegisterWithVAMS is not",
        "miris-upload-armed-trigger-unregistered",
    ],
])("warns on %s", (_, mutate, fragment, rule) => {
    resolve(mutate)();
    expect(warn.mock.calls.map((args) => String(args[0]))).toContainEqual(
        expect.stringContaining(fragment)
    );
    expect(builderRules(mutate, rule.split("-")[0] + "-")).toEqual([rule]);
});
